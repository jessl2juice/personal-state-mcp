from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import math
import re
from typing import Any
from uuid import UUID

from .models import HealthObservation, iso_utc


SCHEMA_VERSION = "personal-state-watch-batch/v1"
SAMSUNG_HEALTH_PACKAGE = "com.sec.android.app.shealth"
DIRECT_WEAR_PACKAGE = "ai.clinicianassist.personalstate"
FITBIT_HEALTH_CONNECT_PACKAGES = frozenset({"com.fitbit.FitbitMobile", "com.fitbit.fitbitmobile"})
FITBIT_HEALTH_CONNECT_ADAPTER_ID = "android_health_connect_fitbit"
DIRECT_WEAR_LIVE_MAX_AGE_SECONDS = 10
MAX_BODY_BYTES = 1024 * 1024
MAX_CHANGES = 500
MAX_SERIES_ITEMS = 2000
MAX_JSON_DEPTH = 12
MAX_STRING_LENGTH = 512
MAX_AGENT_HOURS = 720.0
MAX_AGENT_PAGE = 200
REQUEST_SKEW_SECONDS = 300
MIN_RECORD_TIME = datetime(2015, 1, 1, tzinfo=timezone.utc)

OBSERVATION_METRICS = {
    "activity.steps",
    "activity.exercise_calories",
    "activity.distance",
    "activity.exercise_session",
    "activity.exercise_power",
    "activity.speed",
    "activity.vo2_max",
    "vitals.heart_rate",
    "vitals.oxygen_saturation",
    "vitals.blood_pressure",
    "vitals.blood_glucose",
    "sleep.session",
    "body.weight",
    "body.body_fat",
    "body.basal_metabolic_rate",
    "body.height",
    "nutrition.intake",
}

AVAILABILITY_METRICS = OBSERVATION_METRICS | {
    "vitals.resting_heart_rate",
    "vitals.heart_rate_variability",
    "vitals.skin_temperature",
    "wellness.stress",
    "activity.floors",
    "activity.active_time",
}

STATIC_UNAVAILABLE = {
    "vitals.resting_heart_rate": "Not in Samsung's documented Health Connect export mapping.",
    "vitals.heart_rate_variability": "Not in Samsung's documented Health Connect export mapping.",
    "vitals.skin_temperature": "Not in Samsung's documented Health Connect export mapping.",
    "wellness.stress": "Samsung's stress score is not exposed through the documented Health Connect mapping.",
    "activity.floors": "Not in Samsung's documented Health Connect export mapping.",
    "activity.active_time": "Samsung activity-tracker summaries are not synchronized through Health Connect.",
}

AVAILABILITY_STATES = {
    "available",
    "permission_required",
    "platform_feature_unavailable",
    "not_exported_by_samsung_mapping",
    "no_observation",
    "source_configuration_unverified",
    "companion_read_failed",
    "unknown",
}

ATTRIBUTION_STATES = {
    "watch_confirmed",
    "samsung_health_unattributed",
    "phone",
    "manual",
    "external_device",
    "unknown",
}

BLOCKED_LOCATION_KEYS = {"route", "location", "latitude", "longitude", "polyline", "encoded_polyline"}


class WatchContractError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _pairs_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise WatchContractError("duplicate_json_key", "The JSON body contains a duplicate key.")
        result[key] = value
    return result


def parse_json_strict(body: bytes) -> dict[str, Any]:
    if len(body) > MAX_BODY_BYTES:
        raise WatchContractError("body_too_large", "The request body exceeds the allowed size.")
    try:
        text = body.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_pairs_no_duplicates,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                WatchContractError("non_finite_number", "Non-finite numbers are not accepted.")
            ),
        )
    except WatchContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WatchContractError("invalid_json", "The request body is not valid UTF-8 JSON.") from exc
    if not isinstance(value, dict):
        raise WatchContractError("invalid_payload", "The top-level JSON value must be an object.")
    _validate_tree(value)
    return value


def _validate_tree(value: Any, depth: int = 0) -> None:
    if depth > MAX_JSON_DEPTH:
        raise WatchContractError("payload_too_deep", "The JSON body exceeds the nesting limit.")
    if isinstance(value, str):
        if len(value) > MAX_STRING_LENGTH:
            raise WatchContractError("string_too_long", "A string exceeds the length limit.")
        return
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise WatchContractError("non_finite_number", "Non-finite numbers are not accepted.")
        return
    if isinstance(value, list):
        for item in value:
            _validate_tree(item, depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 128:
                raise WatchContractError("invalid_field", "A field name is invalid.")
            if key.lower() in BLOCKED_LOCATION_KEYS:
                raise WatchContractError("location_data_forbidden", "Route and location data are not accepted.")
            _validate_tree(item, depth + 1)
        return
    raise WatchContractError("invalid_value", "The JSON body contains an unsupported value.")


def _exact_keys(value: dict[str, Any], required: set[str], optional: set[str] = set()) -> None:
    missing = required - value.keys()
    unknown = value.keys() - required - optional
    if missing:
        raise WatchContractError("missing_field", f"A required field is missing: {sorted(missing)[0]}.")
    if unknown:
        raise WatchContractError("unknown_field", f"An unsupported field is present: {sorted(unknown)[0]}.")


def _parse_dt(value: Any, *, nullable: bool = False) -> datetime | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise WatchContractError("invalid_timestamp", "A timestamp is invalid.")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WatchContractError("invalid_timestamp", "A timestamp is invalid.") from exc
    if dt.tzinfo is None:
        raise WatchContractError("invalid_timestamp", "Timestamps must include an offset.")
    return dt.astimezone(timezone.utc)


def _bounded_time(value: Any, now: datetime, *, nullable: bool = False) -> datetime | None:
    dt = _parse_dt(value, nullable=nullable)
    if dt is not None and (dt < MIN_RECORD_TIME or dt > now + timedelta(minutes=10)):
        raise WatchContractError("timestamp_out_of_range", "A timestamp is outside the accepted range.")
    return dt


def _number(value: Any, minimum: float, maximum: float, *, integer: bool = False) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise WatchContractError("invalid_number", "A metric value must be a finite number.")
    if integer and not isinstance(value, int):
        raise WatchContractError("invalid_number", "This metric requires an integer value.")
    if value < minimum or value > maximum:
        raise WatchContractError("value_out_of_range", "A metric value is outside the contract range.")
    return value


def _value_payload(payload: Any, unit: str, minimum: float, maximum: float, *, integer: bool = False, aggregate: bool = False) -> None:
    if not isinstance(payload, dict):
        raise WatchContractError("invalid_payload", "A metric payload must be an object.")
    optional = {"aggregation"} if aggregate else set()
    _exact_keys(payload, {"value", "unit"}, optional)
    if payload["unit"] != unit:
        raise WatchContractError("invalid_unit", "The metric unit is not canonical.")
    _number(payload["value"], minimum, maximum, integer=integer)
    if "aggregation" in payload and payload["aggregation"] not in {"health_connect", "raw_interval"}:
        raise WatchContractError("invalid_value", "The aggregation source is invalid.")


def _series_payload(payload: Any, unit: str, minimum: float, maximum: float, now: datetime) -> None:
    if not isinstance(payload, dict):
        raise WatchContractError("invalid_payload", "A series payload must be an object.")
    _exact_keys(payload, {"samples"})
    samples = payload["samples"]
    if not isinstance(samples, list) or not 1 <= len(samples) <= MAX_SERIES_ITEMS:
        raise WatchContractError("invalid_series", "A series has an invalid sample count.")
    previous: datetime | None = None
    for sample in samples:
        if not isinstance(sample, dict):
            raise WatchContractError("invalid_series", "A series sample must be an object.")
        _exact_keys(sample, {"time", "value", "unit"})
        if sample["unit"] != unit:
            raise WatchContractError("invalid_unit", "A series unit is not canonical.")
        when = _bounded_time(sample["time"], now)
        _number(sample["value"], minimum, maximum)
        if previous is not None and when is not None and when < previous:
            raise WatchContractError("invalid_series", "Series samples must be time ordered.")
        previous = when


def _validate_metric_payload(metric: str, kind: str, payload: Any, now: datetime) -> None:
    if metric == "activity.steps":
        if kind not in {"interval", "aggregate"}:
            raise WatchContractError("invalid_record_kind", "Steps require an interval or aggregate record.")
        _value_payload(payload, "count", 0, 1_000_000, integer=True, aggregate=True)
    elif metric == "activity.exercise_calories":
        if kind not in {"interval", "aggregate"}:
            raise WatchContractError("invalid_record_kind", "Exercise calories require an interval or aggregate record.")
        _value_payload(payload, "kcal", 0, 10_000_000)
    elif metric == "activity.distance":
        if kind not in {"interval", "aggregate"}:
            raise WatchContractError("invalid_record_kind", "Distance requires an interval or aggregate record.")
        _value_payload(payload, "m", 0, 10_000_000)
    elif metric == "activity.exercise_session":
        if kind != "session" or not isinstance(payload, dict):
            raise WatchContractError("invalid_record_kind", "Exercise requires a session record.")
        _exact_keys(payload, {"exercise_type"}, {"title"})
        if not isinstance(payload["exercise_type"], str) or not payload["exercise_type"]:
            raise WatchContractError("invalid_value", "Exercise type is required.")
        if payload.get("title") is not None and not isinstance(payload.get("title"), str):
            raise WatchContractError("invalid_value", "Exercise title is invalid.")
    elif metric == "activity.exercise_power":
        if kind != "series":
            raise WatchContractError("invalid_record_kind", "Power requires a series record.")
        _series_payload(payload, "W", 0, 100_000, now)
    elif metric == "activity.speed":
        if kind != "series":
            raise WatchContractError("invalid_record_kind", "Speed requires a series record.")
        _series_payload(payload, "m/s", 0, 500, now)
    elif metric == "activity.vo2_max":
        if kind != "point" or not isinstance(payload, dict):
            raise WatchContractError("invalid_record_kind", "VO2 max requires a point record.")
        _exact_keys(payload, {"value", "unit", "measurement_method"})
        if payload["unit"] != "mL/kg/min" or not isinstance(payload["measurement_method"], str):
            raise WatchContractError("invalid_unit", "VO2 max payload is invalid.")
        _number(payload["value"], 0, 500)
    elif metric == "vitals.heart_rate":
        if kind != "series":
            raise WatchContractError("invalid_record_kind", "Heart rate requires a series record.")
        _series_payload(payload, "bpm", 0, 500, now)
    elif metric == "vitals.oxygen_saturation":
        if kind != "point":
            raise WatchContractError("invalid_record_kind", "Oxygen saturation requires a point record.")
        _value_payload(payload, "%", 0, 100)
    elif metric == "vitals.blood_pressure":
        if kind != "composite" or not isinstance(payload, dict):
            raise WatchContractError("invalid_record_kind", "Blood pressure requires a composite record.")
        _exact_keys(payload, {"systolic", "diastolic", "unit"}, {"body_position", "measurement_site"})
        if payload["unit"] != "mmHg":
            raise WatchContractError("invalid_unit", "Blood pressure unit is invalid.")
        _number(payload["systolic"], 0, 500)
        _number(payload["diastolic"], 0, 500)
    elif metric == "vitals.blood_glucose":
        if kind != "point" or not isinstance(payload, dict):
            raise WatchContractError("invalid_record_kind", "Blood glucose requires a point record.")
        _exact_keys(payload, {"value", "unit"}, {"specimen_source", "relation_to_meal"})
        if payload["unit"] != "mg/dL":
            raise WatchContractError("invalid_unit", "Blood glucose unit is invalid.")
        _number(payload["value"], 0, 2000)
    elif metric == "sleep.session":
        if kind != "session" or not isinstance(payload, dict):
            raise WatchContractError("invalid_record_kind", "Sleep requires a session record.")
        _exact_keys(payload, {"stages"}, {"title"})
        stages = payload["stages"]
        if not isinstance(stages, list) or len(stages) > MAX_SERIES_ITEMS:
            raise WatchContractError("invalid_series", "Sleep stages exceed the limit.")
        for stage in stages:
            if not isinstance(stage, dict):
                raise WatchContractError("invalid_series", "A sleep stage must be an object.")
            _exact_keys(stage, {"start_at", "end_at", "stage"})
            start = _bounded_time(stage["start_at"], now)
            end = _bounded_time(stage["end_at"], now)
            if start is not None and end is not None and end < start:
                raise WatchContractError("invalid_interval", "A sleep stage ends before it starts.")
            if stage["stage"] not in {"awake", "sleeping", "out_of_bed", "light", "deep", "rem", "unknown"}:
                raise WatchContractError("invalid_value", "A sleep stage is invalid.")
    elif metric == "body.weight":
        if kind != "point":
            raise WatchContractError("invalid_record_kind", "Weight requires a point record.")
        _value_payload(payload, "kg", 0, 1000)
    elif metric == "body.body_fat":
        if kind != "point":
            raise WatchContractError("invalid_record_kind", "Body fat requires a point record.")
        _value_payload(payload, "%", 0, 100)
    elif metric == "body.basal_metabolic_rate":
        if kind != "point":
            raise WatchContractError("invalid_record_kind", "BMR requires a point record.")
        _value_payload(payload, "kcal/day", 0, 20_000)
    elif metric == "body.height":
        if kind != "point":
            raise WatchContractError("invalid_record_kind", "Height requires a point record.")
        _value_payload(payload, "cm", 0, 300)
    elif metric == "nutrition.intake":
        if kind != "interval" or not isinstance(payload, dict):
            raise WatchContractError("invalid_record_kind", "Nutrition requires an interval record.")
        _exact_keys(payload, {"meal_type", "energy_kcal", "nutrients"}, {"name"})
        if not isinstance(payload["meal_type"], str) or not isinstance(payload["nutrients"], dict):
            raise WatchContractError("invalid_value", "Nutrition payload is invalid.")
        if payload["energy_kcal"] is not None:
            _number(payload["energy_kcal"], 0, 100_000)
        allowed = {"protein_g", "carbohydrate_g", "fat_g", "fiber_g", "sugar_g", "sodium_mg"}
        _exact_keys(payload["nutrients"], set(), allowed)
        for key, value in payload["nutrients"].items():
            if value is not None:
                _number(value, 0, 10_000_000 if key == "sodium_mg" else 100_000)
    else:
        raise WatchContractError("unsupported_metric", "This metric cannot contain observations in schema v1.")


def _validate_attribution(value: Any) -> None:
    if not isinstance(value, dict):
        raise WatchContractError("invalid_attribution", "Attribution must be an object.")
    _exact_keys(value, {"state", "evidence"}, {"device_type", "device_model"})
    if value["state"] not in ATTRIBUTION_STATES or not isinstance(value["evidence"], str):
        raise WatchContractError("invalid_attribution", "Attribution is invalid.")
    if value["state"] == "watch_confirmed":
        evidence = " ".join(str(value.get(key) or "") for key in ("evidence", "device_type", "device_model")).lower()
        if "watch5" not in evidence and "sm-r9" not in evidence:
            raise WatchContractError("unproven_watch_attribution", "Watch attribution requires positive device evidence.")


def _validate_observation(value: Any, now: datetime) -> None:
    if not isinstance(value, dict):
        raise WatchContractError("invalid_observation", "An observation must be an object.")
    required = {
        "record_id", "metric", "record_kind", "observed_by_companion_at", "upstream_last_modified_at",
        "source_package", "recording_method", "attribution", "payload",
    }
    timing = {"measured_at", "start_at", "end_at", "zone_offset", "start_zone_offset", "end_zone_offset"}
    _exact_keys(value, required, timing)
    if not isinstance(value["record_id"], str) or not value["record_id"]:
        raise WatchContractError("invalid_record_id", "A source record identifier is required.")
    metric = value["metric"]
    kind = value["record_kind"]
    if metric not in OBSERVATION_METRICS:
        raise WatchContractError("unsupported_metric", "This metric cannot contain observations in schema v1.")
    if kind not in {"point", "interval", "series", "session", "composite", "aggregate"}:
        raise WatchContractError("invalid_record_kind", "The record kind is invalid.")
    source_package = value["source_package"]
    allowed_source = (
        source_package == SAMSUNG_HEALTH_PACKAGE
        or (metric == "vitals.heart_rate" and source_package == DIRECT_WEAR_PACKAGE)
        or (metric == "vitals.heart_rate" and source_package in FITBIT_HEALTH_CONNECT_PACKAGES)
    )
    if not allowed_source:
        raise WatchContractError("source_not_allowed", "This source is not accepted for the supplied metric.")
    if value["recording_method"] not in {"active", "automatic", "manual", "unknown"}:
        raise WatchContractError("invalid_recording_method", "The recording method is invalid.")
    _validate_attribution(value["attribution"])
    _bounded_time(value["observed_by_companion_at"], now)
    _bounded_time(value["upstream_last_modified_at"], now, nullable=True)
    if kind in {"point", "composite"}:
        if "measured_at" not in value or any(key in value for key in {"start_at", "end_at", "start_zone_offset", "end_zone_offset"}):
            raise WatchContractError("ambiguous_timing", "Point timing fields are ambiguous.")
        _bounded_time(value["measured_at"], now)
    else:
        if "start_at" not in value or "end_at" not in value or any(key in value for key in {"measured_at", "zone_offset"}):
            raise WatchContractError("ambiguous_timing", "Interval timing fields are ambiguous.")
        start = _bounded_time(value["start_at"], now)
        end = _bounded_time(value["end_at"], now)
        if start is not None and end is not None and end < start:
            raise WatchContractError("invalid_interval", "A record ends before it starts.")
    _validate_metric_payload(metric, kind, value["payload"], now)


def _validate_availability(value: Any, now: datetime) -> None:
    if not isinstance(value, dict):
        raise WatchContractError("invalid_availability", "Availability must be an object.")
    _exact_keys(value, {"metric", "state", "evidence", "checked_at", "coverage"})
    if value["metric"] not in AVAILABILITY_METRICS or value["state"] not in AVAILABILITY_STATES:
        raise WatchContractError("invalid_availability", "Availability metric or state is invalid.")
    if not isinstance(value["evidence"], str):
        raise WatchContractError("invalid_availability", "Availability evidence is invalid.")
    _bounded_time(value["checked_at"], now)
    coverage = value["coverage"]
    if not isinstance(coverage, dict):
        raise WatchContractError("invalid_coverage", "Coverage must be an object.")
    _exact_keys(coverage, {"window_start", "window_end", "truncated", "backfill_limited", "interrupted", "reconciling"})
    for key in ("truncated", "backfill_limited", "interrupted", "reconciling"):
        if not isinstance(coverage[key], bool):
            raise WatchContractError("invalid_coverage", "Coverage flags must be boolean.")
    start = _bounded_time(coverage["window_start"], now, nullable=True)
    end = _bounded_time(coverage["window_end"], now, nullable=True)
    if start and end and end < start:
        raise WatchContractError("invalid_coverage", "Coverage ends before it starts.")
    if value["metric"] in STATIC_UNAVAILABLE and value["state"] == "available":
        raise WatchContractError("unsupported_availability", "This metric is not exported by the documented Samsung mapping.")


def validate_batch(value: dict[str, Any], now: datetime) -> None:
    _exact_keys(value, {"schema_version", "batch_id", "installation_id", "generated_at", "changes", "availability"})
    if value["schema_version"] != SCHEMA_VERSION:
        raise WatchContractError("upgrade_required", "The watch schema version is not supported.")
    try:
        UUID(str(value["batch_id"]))
    except (ValueError, TypeError) as exc:
        raise WatchContractError("invalid_batch_id", "The batch identifier is invalid.") from exc
    if not isinstance(value["installation_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", value["installation_id"]):
        raise WatchContractError("invalid_installation_id", "The installation identifier is invalid.")
    _bounded_time(value["generated_at"], now)
    changes = value["changes"]
    availability = value["availability"]
    if not isinstance(changes, list) or len(changes) > MAX_CHANGES:
        raise WatchContractError("batch_too_large", "The batch has too many changes.")
    if not isinstance(availability, list) or len(availability) > 64:
        raise WatchContractError("batch_too_large", "The batch has too many availability entries.")
    for change in changes:
        if not isinstance(change, dict) or change.get("operation") not in {"upsert", "delete"}:
            raise WatchContractError("invalid_change", "A change operation is invalid.")
        if change["operation"] == "upsert":
            _exact_keys(change, {"operation", "observation"})
            _validate_observation(change["observation"], now)
        else:
            _exact_keys(change, {"operation", "record_id", "metric", "source_package", "upstream_last_modified_at"})
            allowed_delete_source = change["source_package"] == SAMSUNG_HEALTH_PACKAGE or (
                change["metric"] == "vitals.heart_rate" and change["source_package"] in FITBIT_HEALTH_CONNECT_PACKAGES
            )
            if change["metric"] not in OBSERVATION_METRICS or not allowed_delete_source:
                raise WatchContractError("invalid_delete", "A delete operation has an invalid source or metric.")
            if not isinstance(change["record_id"], str) or not change["record_id"]:
                raise WatchContractError("invalid_delete", "A delete operation needs a record identifier.")
            _bounded_time(change["upstream_last_modified_at"], now, nullable=True)
    for item in availability:
        _validate_availability(item, now)


def keyed_hash(key: str, value: str) -> str:
    return hmac.new(key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def observation_id(key: str, source_package: str, record_id: str, metric: str) -> str:
    return keyed_hash(key, f"observation|{source_package}|{record_id}|{metric}")


def batch_to_observations(batch: dict[str, Any], identifier_key: str, now: datetime) -> tuple[list[HealthObservation], list[dict[str, Any]]]:
    installation_hash = keyed_hash(identifier_key, f"installation|{batch['installation_id']}")
    observations: list[HealthObservation] = []
    deletions: list[dict[str, Any]] = []
    for change in batch["changes"]:
        if change["operation"] == "delete":
            deletions.append(
                {
                    "id": observation_id(identifier_key, change["source_package"], change["record_id"], change["metric"]),
                    "metric": change["metric"],
                    "source_record_hash": keyed_hash(identifier_key, f"record|{change['record_id']}"),
                    "upstream_last_modified_at": change["upstream_last_modified_at"],
                }
            )
            continue
        raw = change["observation"]
        observations.append(
            HealthObservation(
                id=observation_id(identifier_key, raw["source_package"], raw["record_id"], raw["metric"]),
                metric=raw["metric"],
                category=raw["metric"].split(".", 1)[0],
                record_kind=raw["record_kind"],
                payload=raw["payload"],
                measured_at=_parse_dt(raw.get("measured_at"), nullable=True),
                start_at=_parse_dt(raw.get("start_at"), nullable=True),
                end_at=_parse_dt(raw.get("end_at"), nullable=True),
                observed_by_companion_at=_parse_dt(raw["observed_by_companion_at"]),  # type: ignore[arg-type]
                ingested_at_server=now,
                upstream_last_modified_at=_parse_dt(raw.get("upstream_last_modified_at"), nullable=True),
                source_package=raw["source_package"],
                recording_method=raw["recording_method"],
                attribution=raw["attribution"],
                installation_hash=installation_hash,
                source_record_hash=keyed_hash(identifier_key, f"record|{raw['record_id']}"),
                zone_offset=raw.get("zone_offset"),
                start_zone_offset=raw.get("start_zone_offset"),
                end_zone_offset=raw.get("end_zone_offset"),
                adapter_id=(
                    "wear_health_services"
                    if raw["source_package"] == DIRECT_WEAR_PACKAGE
                    else FITBIT_HEALTH_CONNECT_ADAPTER_ID
                    if raw["source_package"] in FITBIT_HEALTH_CONNECT_PACKAGES
                    else "android_health_connect"
                ),
            )
        )
    return observations, deletions


def upload_signature(secret: str, timestamp: str, nonce: str, batch_id: str, body: bytes) -> str:
    prefix = f"{timestamp}\n{nonce}\n{batch_id}\n".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), prefix + body, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def verify_upload_signature(secret: str, timestamp: str, nonce: str, batch_id: str, body: bytes, now: datetime) -> bool:
    if not nonce or len(nonce) > 128 or not batch_id:
        return False
    try:
        request_time = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        return False
    if abs((now - request_time).total_seconds()) > REQUEST_SKEW_SECONDS:
        return False
    return isinstance(body, bytes)


def signature_matches(secret: str, timestamp: str, nonce: str, batch_id: str, body: bytes, supplied: str, now: datetime) -> bool:
    if not supplied or not verify_upload_signature(secret, timestamp, nonce, batch_id, body, now):
        return False
    return hmac.compare_digest(upload_signature(secret, timestamp, nonce, batch_id, body), supplied)


def observation_recency(observation: HealthObservation, now: datetime) -> dict[str, Any]:
    event_at = observation.event_at
    age = (now - event_at).total_seconds()
    if age < -600:
        status = "unknown"
        reason = "The observation timestamp is in the future."
    elif observation.metric == "vitals.heart_rate":
        if observation.adapter_id == "wear_health_services" and age <= DIRECT_WEAR_LIVE_MAX_AGE_SECONDS:
            status, reason = "live", "Direct Wear heart rate was measured within the past ten seconds."
        elif age <= 900:
            status, reason = "recent_record", "This heart-rate record is within 15 minutes but is not labeled live."
        else:
            status, reason = "last_recorded", "This is the last recorded heart-rate value."
    elif observation.metric in {"vitals.oxygen_saturation", "vitals.oxygen_saturation_series"}:
        status, reason = ("recent", "Oxygen sample is within 2 hours.") if age <= 7200 else ("latest_recorded", "This is the latest recorded oxygen sample.")
    elif observation.metric in {"vitals.resting_heart_rate", "vitals.heart_rate_variability", "vitals.respiratory_rate"}:
        status, reason = ("recent", "This Fitbit-derived vital is within 24 hours.") if age <= 86400 else ("latest_recorded", "This is the latest recorded value; it is not live.")
    elif observation.metric.startswith("activity.") and observation.metric != "activity.exercise_session":
        status, reason = ("recent", "Activity record is within 12 hours.") if age <= 43200 else ("latest_recorded", "This is the latest recorded activity value.")
    elif observation.metric == "activity.exercise_session":
        status, reason = ("recent", "Exercise session ended within 24 hours.") if age <= 86400 else ("latest_recorded", "This is the latest recorded exercise session.")
    elif observation.metric in {"sleep.session", "sleep.samsung_session", "sleep.summary", "sleep.score"}:
        status, reason = ("recent", "Sleep session ended within 36 hours.") if age <= 129600 else ("latest_recorded", "This is the latest recorded sleep session.")
    elif observation.metric == "wellness.energy_score":
        status, reason = "latest_recorded", "This is Samsung's latest recorded Energy Score for its stated local date."
    elif observation.metric in {"vitals.skin_temperature", "cardiac.irregular_rhythm_notification", "sleep.apnea_detected_sign"}:
        status, reason = "latest_recorded", "This is the latest vendor-recorded result; it is never labeled live."
    else:
        status, reason = "latest_recorded", "This is the latest recorded value for this metric."
    return {
        "status": status,
        "reason": reason,
        "measurement_age_seconds": max(0, int(age)),
        "observed_path_lag_seconds": max(0, int((observation.observed_by_companion_at - event_at).total_seconds())),
        "server_ingest_lag_seconds": max(0, int((observation.ingested_at_server - observation.observed_by_companion_at).total_seconds())),
        "event_at": iso_utc(event_at),
    }


def encode_cursor(payload: dict[str, Any], key: str) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(key.encode("utf-8"), raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw + signature).decode("ascii").rstrip("=")


def decode_cursor(cursor: str, key: str, now: datetime) -> dict[str, Any]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        combined = base64.urlsafe_b64decode(padded.encode("ascii"))
        raw, supplied = combined[:-32], combined[-32:]
        expected = hmac.new(key.encode("utf-8"), raw, hashlib.sha256).digest()
        if not hmac.compare_digest(supplied, expected):
            raise ValueError
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or float(payload["expires_at"]) < now.timestamp():
            raise ValueError
        return payload
    except Exception as exc:
        raise WatchContractError("invalid_cursor", "The watch-history cursor is invalid or expired.") from exc
