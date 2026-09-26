from __future__ import annotations

from datetime import date, datetime
import re
from typing import Any
from uuid import UUID

from .models import HealthObservation
from .watch_contract import (
    AVAILABILITY_METRICS,
    DIRECT_WEAR_PACKAGE,
    MAX_CHANGES,
    MAX_SERIES_ITEMS,
    OBSERVATION_METRICS,
    SAMSUNG_HEALTH_PACKAGE,
    WatchContractError,
    _bounded_time,
    _exact_keys,
    _parse_dt,
    _validate_attribution,
    _validate_metric_payload,
    keyed_hash,
)


SCHEMA_VERSION_V2 = "personal-state-watch-batch/v2"
SAMSUNG_ADAPTER_ID = "android_samsung_health_data"
ALLOWED_ADAPTER_IDS = {
    "android_health_connect",
    SAMSUNG_ADAPTER_ID,
    "wear_health_services",
    "synthetic_test",
}
SAMSUNG_SOURCE_PACKAGES = {SAMSUNG_HEALTH_PACKAGE, "com.samsung.android.shealth"}
HASH_PATTERN = re.compile(r"[0-9a-f]{64}")

SAMSUNG_OBSERVATION_METRICS = {
    "vitals.oxygen_saturation_series",
    "sleep.samsung_session",
    "sleep.summary",
    "sleep.score",
    "vitals.skin_temperature",
    "wellness.energy_score",
    "cardiac.irregular_rhythm_notification",
    "sleep.apnea_detected_sign",
    "activity.floors",
    "activity.active_time",
}
V2_OBSERVATION_METRICS = OBSERVATION_METRICS | SAMSUNG_OBSERVATION_METRICS
V2_AVAILABILITY_ONLY_METRICS = {"sleep.snoring", "cardiac.ecg", "wellness.stress"}
V2_AVAILABILITY_METRICS = AVAILABILITY_METRICS | SAMSUNG_OBSERVATION_METRICS | V2_AVAILABILITY_ONLY_METRICS
V2_AVAILABILITY_STATES = {
    "available",
    "permission_required",
    "adapter_not_installed",
    "provider_partnership_required",
    "not_exposed_by_provider",
    "platform_feature_unavailable",
    "no_observation",
    "source_configuration_unverified",
    "companion_read_failed",
    "unknown",
}
ASSOCIATION_REQUIRED_METRICS = {"sleep.samsung_session", "sleep.summary", "sleep.score"}
ASSOCIATION_ALLOWED_METRICS = ASSOCIATION_REQUIRED_METRICS | {
    "vitals.oxygen_saturation_series",
    "vitals.skin_temperature",
}


def _validate_adapter(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise WatchContractError("invalid_adapter", "Adapter identity must be an object.")
    _exact_keys(value, {"id", "version"})
    adapter_id = value["id"]
    version = value["version"]
    if adapter_id not in ALLOWED_ADAPTER_IDS:
        raise WatchContractError("invalid_adapter", "The adapter is not supported.")
    if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9._+-]{1,64}", version):
        raise WatchContractError("invalid_adapter", "The adapter version is invalid.")
    return {"id": adapter_id, "version": version}


def _require_same_adapter(value: Any, expected: dict[str, str]) -> None:
    if _validate_adapter(value) != expected:
        raise WatchContractError("mixed_adapter_batch", "Every item must use the envelope adapter.")


def _validate_hash(value: Any, field: str) -> str:
    if not isinstance(value, str) or not HASH_PATTERN.fullmatch(value):
        raise WatchContractError("invalid_hash", f"{field} must be a lowercase SHA-256 hash.")
    return value


def _number(value: Any, minimum: float, maximum: float, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= float(value) <= maximum:
        raise WatchContractError("invalid_value", f"{field} is outside the accepted range.")
    return float(value)


def _validate_offset(value: Any) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[+-](?:0\d|1[0-4]):[0-5]\d", value):
        raise WatchContractError("invalid_zone_offset", "A zone offset is invalid.")


def _validate_samples(payload: Any, unit: str, minimum: float, maximum: float, now: datetime) -> None:
    if not isinstance(payload, dict):
        raise WatchContractError("invalid_payload", "A series payload must be an object.")
    _exact_keys(payload, {"samples", "unit", "minimum", "maximum"})
    if payload["unit"] != unit:
        raise WatchContractError("invalid_unit", "The series unit is invalid.")
    samples = payload["samples"]
    if not isinstance(samples, list) or not 1 <= len(samples) <= MAX_SERIES_ITEMS:
        raise WatchContractError("invalid_series", "The series has an invalid number of samples.")
    values: list[float] = []
    previous: datetime | None = None
    for item in samples:
        if not isinstance(item, dict):
            raise WatchContractError("invalid_series", "A series sample must be an object.")
        _exact_keys(item, {"time", "value"})
        sample_time = _bounded_time(item["time"], now)
        if previous is not None and sample_time is not None and sample_time < previous:
            raise WatchContractError("invalid_series", "Series samples must be ordered by time.")
        previous = sample_time
        values.append(_number(item["value"], minimum, maximum, "A series value"))
    declared_min = _number(payload["minimum"], minimum, maximum, "The declared minimum")
    declared_max = _number(payload["maximum"], minimum, maximum, "The declared maximum")
    if declared_min > declared_max or declared_min != min(values) or declared_max != max(values):
        raise WatchContractError("invalid_series_summary", "Series minimum and maximum must match its samples.")


def _validate_stages(payload: Any, now: datetime) -> None:
    if not isinstance(payload, dict):
        raise WatchContractError("invalid_payload", "A Samsung sleep session payload must be an object.")
    _exact_keys(payload, {"duration_minutes", "stages"})
    _number(payload["duration_minutes"], 0, 24 * 60, "Sleep duration")
    stages = payload["stages"]
    if not isinstance(stages, list) or len(stages) > MAX_SERIES_ITEMS:
        raise WatchContractError("invalid_series", "Sleep stages exceed the limit.")
    previous_end: datetime | None = None
    for stage in stages:
        if not isinstance(stage, dict):
            raise WatchContractError("invalid_series", "A sleep stage must be an object.")
        _exact_keys(stage, {"start_at", "end_at", "stage"})
        start = _bounded_time(stage["start_at"], now)
        end = _bounded_time(stage["end_at"], now)
        if start is None or end is None or end < start or (previous_end is not None and start < previous_end):
            raise WatchContractError("invalid_interval", "Sleep stages must be ordered and non-overlapping.")
        if stage["stage"] not in {"awake", "sleeping", "out_of_bed", "light", "deep", "rem", "unknown"}:
            raise WatchContractError("invalid_value", "A sleep stage is invalid.")
        previous_end = end


def _validate_new_metric_payload(metric: str, kind: str, payload: Any, now: datetime) -> None:
    if metric == "vitals.oxygen_saturation_series":
        if kind != "series":
            raise WatchContractError("invalid_record_kind", "Blood oxygen history requires a series record.")
        _validate_samples(payload, "%", 0, 100, now)
    elif metric == "vitals.skin_temperature":
        if kind != "series":
            raise WatchContractError("invalid_record_kind", "Skin temperature requires a series record.")
        _validate_samples(payload, "Cel", -50, 100, now)
    elif metric == "sleep.samsung_session":
        if kind != "session":
            raise WatchContractError("invalid_record_kind", "Samsung sleep requires a session record.")
        _validate_stages(payload, now)
    elif metric == "sleep.summary":
        if kind != "session" or not isinstance(payload, dict):
            raise WatchContractError("invalid_record_kind", "Sleep summary requires a session record.")
        _exact_keys(payload, {"duration_minutes", "session_record_hashes"})
        _number(payload["duration_minutes"], 0, 24 * 60, "Sleep duration")
        hashes = payload["session_record_hashes"]
        if not isinstance(hashes, list) or len(hashes) > MAX_SERIES_ITEMS:
            raise WatchContractError("invalid_value", "Sleep session references are invalid.")
        for item in hashes:
            _validate_hash(item, "A sleep session record hash")
        if len(hashes) != len(set(hashes)):
            raise WatchContractError("invalid_value", "Sleep session references must be unique.")
    elif metric in {"sleep.score", "wellness.energy_score"}:
        expected_kind = "daily" if metric == "wellness.energy_score" else "point"
        if kind != expected_kind or not isinstance(payload, dict):
            raise WatchContractError("invalid_record_kind", "The Samsung score has an invalid record kind.")
        _exact_keys(payload, {"value", "unit"})
        if payload["unit"] != "score":
            raise WatchContractError("invalid_unit", "Samsung scores use the score unit.")
        _number(payload["value"], 0, 100, "Samsung score")
    elif metric == "cardiac.irregular_rhythm_notification":
        if kind != "point" or not isinstance(payload, dict):
            raise WatchContractError("invalid_record_kind", "Rhythm notifications require a point record.")
        _exact_keys(payload, {"status"})
        if payload["status"] not in {"detected", "undefined"}:
            raise WatchContractError("invalid_value", "The rhythm notification status is invalid.")
    elif metric == "sleep.apnea_detected_sign":
        if kind != "point" or not isinstance(payload, dict):
            raise WatchContractError("invalid_record_kind", "Sleep apnea detected signs require a point record.")
        _exact_keys(payload, {"status"})
        if payload["status"] not in {"detected", "not_detected", "undefined"}:
            raise WatchContractError("invalid_value", "The sleep apnea detected sign is invalid.")
    elif metric in {"activity.floors", "activity.active_time"}:
        if kind != "aggregate" or not isinstance(payload, dict):
            raise WatchContractError("invalid_record_kind", "Activity totals require an aggregate record.")
        _exact_keys(payload, {"value", "unit"})
        expected_unit = "count" if metric == "activity.floors" else "min"
        if payload["unit"] != expected_unit or isinstance(payload["value"], bool) or not isinstance(payload["value"], int):
            raise WatchContractError("invalid_value", "The activity total is invalid.")
        _number(payload["value"], 0, 1_000_000, "Activity total")
    else:
        raise WatchContractError("unsupported_metric", "This metric is not supported by schema v2.")


def _validate_observation(value: Any, adapter: dict[str, str], now: datetime) -> None:
    if not isinstance(value, dict):
        raise WatchContractError("invalid_observation", "An observation must be an object.")
    required = {
        "adapter", "record_hash", "metric", "record_kind", "observed_by_companion_at",
        "upstream_last_modified_at", "source_package", "recording_method", "attribution", "payload",
    }
    optional = {
        "association_hash", "measured_at", "start_at", "end_at", "local_date",
        "zone_offset", "start_zone_offset", "end_zone_offset",
    }
    _exact_keys(value, required, optional)
    _require_same_adapter(value["adapter"], adapter)
    _validate_hash(value["record_hash"], "record_hash")
    metric = value["metric"]
    kind = value["record_kind"]
    if metric not in V2_OBSERVATION_METRICS:
        raise WatchContractError("unsupported_metric", "This metric is not supported by schema v2.")
    if kind not in {"point", "interval", "series", "session", "composite", "aggregate", "daily"}:
        raise WatchContractError("invalid_record_kind", "The record kind is invalid.")
    if adapter["id"] == SAMSUNG_ADAPTER_ID:
        if metric not in SAMSUNG_OBSERVATION_METRICS or value["source_package"] not in SAMSUNG_SOURCE_PACKAGES:
            raise WatchContractError("adapter_metric_mismatch", "The Samsung adapter does not own this metric or source.")
    elif adapter["id"] == "wear_health_services":
        if metric != "vitals.heart_rate" or value["source_package"] != DIRECT_WEAR_PACKAGE:
            raise WatchContractError("adapter_metric_mismatch", "The direct Wear adapter only owns heart rate.")
    elif adapter["id"] == "android_health_connect":
        if metric in SAMSUNG_OBSERVATION_METRICS or value["source_package"] != SAMSUNG_HEALTH_PACKAGE:
            raise WatchContractError("adapter_metric_mismatch", "The Health Connect adapter does not own this metric or source.")
    elif adapter["id"] != "synthetic_test" and metric in SAMSUNG_OBSERVATION_METRICS:
        raise WatchContractError("adapter_metric_mismatch", "This metric requires the Samsung Health Data adapter.")
    if value["recording_method"] not in {"active", "automatic", "manual", "unknown"}:
        raise WatchContractError("invalid_recording_method", "The recording method is invalid.")
    _validate_attribution(value["attribution"])
    for offset_field in ("zone_offset", "start_zone_offset", "end_zone_offset"):
        if offset_field in value:
            _validate_offset(value[offset_field])
    _bounded_time(value["observed_by_companion_at"], now)
    _bounded_time(value["upstream_last_modified_at"], now, nullable=True)
    association_hash = value.get("association_hash")
    if metric in ASSOCIATION_REQUIRED_METRICS and association_hash is None:
        raise WatchContractError("association_required", "This record requires a parent association.")
    if association_hash is not None:
        if metric not in ASSOCIATION_ALLOWED_METRICS:
            raise WatchContractError("association_not_allowed", "This metric cannot carry a parent association.")
        _validate_hash(association_hash, "association_hash")
    if kind in {"point", "composite", "daily"}:
        if "measured_at" not in value or any(key in value for key in {"start_at", "end_at", "start_zone_offset", "end_zone_offset"}):
            raise WatchContractError("ambiguous_timing", "Point and daily timing fields are ambiguous.")
        _bounded_time(value["measured_at"], now)
        if kind == "daily":
            if "local_date" not in value or "zone_offset" not in value:
                raise WatchContractError("missing_field", "Daily records require local_date and zone_offset.")
            try:
                date.fromisoformat(value["local_date"])
            except (TypeError, ValueError) as exc:
                raise WatchContractError("invalid_local_date", "The local date is invalid.") from exc
            _validate_offset(value["zone_offset"])
        elif "local_date" in value:
            raise WatchContractError("invalid_local_date", "Only daily records may carry local_date.")
    else:
        if "start_at" not in value or "end_at" not in value or any(key in value for key in {"measured_at", "local_date", "zone_offset"}):
            raise WatchContractError("ambiguous_timing", "Interval timing fields are ambiguous.")
        start = _bounded_time(value["start_at"], now)
        end = _bounded_time(value["end_at"], now)
        if start is None or end is None or end < start:
            raise WatchContractError("invalid_interval", "A record ends before it starts.")
    if metric in SAMSUNG_OBSERVATION_METRICS:
        _validate_new_metric_payload(metric, kind, value["payload"], now)
    else:
        _validate_metric_payload(metric, kind, value["payload"], now)


def _validate_deletion(value: Any, adapter: dict[str, str], now: datetime) -> None:
    if not isinstance(value, dict):
        raise WatchContractError("invalid_delete", "A deletion must be an object.")
    _exact_keys(
        value,
        {"adapter", "record_hash", "metric", "source_package", "upstream_last_modified_at"},
    )
    _require_same_adapter(value["adapter"], adapter)
    _validate_hash(value["record_hash"], "record_hash")
    if value["metric"] not in V2_OBSERVATION_METRICS or not isinstance(value["source_package"], str):
        raise WatchContractError("invalid_delete", "A deletion has an invalid metric or source.")
    _bounded_time(value["upstream_last_modified_at"], now, nullable=True)


def _validate_manifest(value: Any, adapter: dict[str, str]) -> tuple[str, set[tuple[str, str]]]:
    if not isinstance(value, dict):
        raise WatchContractError("invalid_association_manifest", "An association manifest must be an object.")
    _exact_keys(value, {"adapter", "association_hash", "members"})
    _require_same_adapter(value["adapter"], adapter)
    association_hash = _validate_hash(value["association_hash"], "association_hash")
    members = value["members"]
    if not isinstance(members, list) or len(members) > MAX_CHANGES:
        raise WatchContractError("invalid_association_manifest", "The association manifest is invalid.")
    normalized: set[tuple[str, str]] = set()
    for item in members:
        if not isinstance(item, dict):
            raise WatchContractError("invalid_association_manifest", "A manifest member must be an object.")
        _exact_keys(item, {"metric", "record_hash"})
        if item["metric"] not in ASSOCIATION_ALLOWED_METRICS:
            raise WatchContractError("invalid_association_manifest", "A manifest member metric is invalid.")
        member = (item["metric"], _validate_hash(item["record_hash"], "record_hash"))
        if member in normalized:
            raise WatchContractError("invalid_association_manifest", "Association manifest members must be unique.")
        normalized.add(member)
    return association_hash, normalized


def _validate_source_change(value: Any, adapter: dict[str, str], now: datetime) -> None:
    if not isinstance(value, dict):
        raise WatchContractError("invalid_source_change", "A source change must be an object.")
    _exact_keys(
        value,
        {"operation", "source_type", "changed_at", "adapter", "observations", "deletions"},
        {"association_manifest"},
    )
    _require_same_adapter(value["adapter"], adapter)
    if value["operation"] not in {"upsert", "delete"} or not isinstance(value["source_type"], str) or not value["source_type"]:
        raise WatchContractError("invalid_source_change", "A source change operation or type is invalid.")
    _bounded_time(value["changed_at"], now)
    observations = value["observations"]
    deletions = value["deletions"]
    if not isinstance(observations, list) or not isinstance(deletions, list):
        raise WatchContractError("invalid_source_change", "Source-change observations and deletions must be arrays.")
    if len(observations) + len(deletions) > MAX_CHANGES:
        raise WatchContractError("batch_too_large", "A source change has too many records.")
    for observation in observations:
        _validate_observation(observation, adapter, now)
    for deletion in deletions:
        _validate_deletion(deletion, adapter, now)
    manifest_value = value.get("association_manifest")
    manifest: tuple[str, set[tuple[str, str]]] | None = None
    if manifest_value is not None:
        manifest = _validate_manifest(manifest_value, adapter)
    if value["operation"] == "upsert":
        if not observations or deletions:
            raise WatchContractError("invalid_source_change", "An upsert must contain observations and no deletions.")
        associated = [item for item in observations if item.get("association_hash")]
        if associated:
            if manifest is None:
                raise WatchContractError("association_manifest_required", "Associated records require a complete manifest.")
            manifest_hash, members = manifest
            if any(item["association_hash"] != manifest_hash for item in associated):
                raise WatchContractError("association_mismatch", "Every associated record must use the manifest association.")
            observed_members = {(item["metric"], item["record_hash"]) for item in associated}
            if observed_members != members:
                raise WatchContractError("association_manifest_incomplete", "The association manifest must exactly match its records.")
        elif manifest is not None:
            raise WatchContractError("association_mismatch", "A manifest cannot accompany unrelated records.")
    elif observations or (not deletions and manifest is None):
        raise WatchContractError("invalid_source_change", "A delete must contain deletions or an empty association manifest.")
    if value["operation"] == "delete" and manifest is not None and manifest[1]:
        raise WatchContractError("invalid_association_manifest", "A parent deletion requires an empty association manifest.")


def _validate_availability(value: Any, adapter: dict[str, str], now: datetime) -> None:
    if not isinstance(value, dict):
        raise WatchContractError("invalid_availability", "Availability must be an object.")
    _exact_keys(value, {"adapter", "metric", "state", "evidence", "coverage_checked_at", "coverage"})
    _require_same_adapter(value["adapter"], adapter)
    metric = value["metric"]
    state = value["state"]
    if metric not in V2_AVAILABILITY_METRICS or state not in V2_AVAILABILITY_STATES or not isinstance(value["evidence"], str):
        raise WatchContractError("invalid_availability", "Availability metric, state, or evidence is invalid.")
    if metric in V2_AVAILABILITY_ONLY_METRICS and state == "available":
        raise WatchContractError("unsupported_availability", "This provider does not expose observations for the metric.")
    _bounded_time(value["coverage_checked_at"], now)
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


def validate_batch_v2(value: dict[str, Any], now: datetime) -> None:
    _exact_keys(
        value,
        {"schema_version", "adapter", "identity_namespace_id", "batch_id", "installation_id", "generated_at", "changes", "availability"},
    )
    if value["schema_version"] != SCHEMA_VERSION_V2:
        raise WatchContractError("upgrade_required", "The watch schema version is not supported.")
    adapter = _validate_adapter(value["adapter"])
    try:
        UUID(str(value["identity_namespace_id"]))
        UUID(str(value["batch_id"]))
    except (TypeError, ValueError) as exc:
        raise WatchContractError("invalid_identifier", "A v2 batch identifier is invalid.") from exc
    if not isinstance(value["installation_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", value["installation_id"]):
        raise WatchContractError("invalid_installation_id", "The installation identifier is invalid.")
    _bounded_time(value["generated_at"], now)
    changes = value["changes"]
    availability = value["availability"]
    if not isinstance(changes, list) or len(changes) > MAX_CHANGES:
        raise WatchContractError("batch_too_large", "The batch has too many source changes.")
    if not isinstance(availability, list) or len(availability) > 128:
        raise WatchContractError("batch_too_large", "The batch has too many availability entries.")
    for change in changes:
        _validate_source_change(change, adapter, now)
    for item in availability:
        _validate_availability(item, adapter, now)


def _observation_id(identifier_key: str, namespace: str, adapter_id: str, record_hash: str, metric: str) -> str:
    return keyed_hash(identifier_key, f"observation\0{namespace}\0{adapter_id}\0{record_hash}\0{metric}")


def batch_v2_to_observations(
    batch: dict[str, Any],
    identifier_key: str,
    now: datetime,
) -> tuple[list[HealthObservation], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    adapter = batch["adapter"]
    namespace = batch["identity_namespace_id"]
    installation_hash = keyed_hash(identifier_key, f"installation|{batch['installation_id']}")
    observations: list[HealthObservation] = []
    deletions: list[dict[str, Any]] = []
    reconciliations: list[dict[str, Any]] = []
    for change in batch["changes"]:
        for raw in change["observations"]:
            observations.append(
                HealthObservation(
                    id=_observation_id(identifier_key, namespace, adapter["id"], raw["record_hash"], raw["metric"]),
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
                    source_record_hash=raw["record_hash"],
                    zone_offset=raw.get("zone_offset"),
                    start_zone_offset=raw.get("start_zone_offset"),
                    end_zone_offset=raw.get("end_zone_offset"),
                    schema_version=SCHEMA_VERSION_V2,
                    adapter_id=adapter["id"],
                    adapter_version=adapter["version"],
                    identity_namespace_id=namespace,
                    association_hash=raw.get("association_hash"),
                    local_date=raw.get("local_date"),
                )
            )
        for raw in change["deletions"]:
            deletions.append(
                {
                    "id": _observation_id(identifier_key, namespace, adapter["id"], raw["record_hash"], raw["metric"]),
                    "metric": raw["metric"],
                    "source_record_hash": raw["record_hash"],
                    "upstream_last_modified_at": raw["upstream_last_modified_at"],
                    "adapter_id": adapter["id"],
                    "identity_namespace_id": namespace,
                }
            )
        manifest = change.get("association_manifest")
        if manifest is not None:
            reconciliations.append(
                {
                    "identity_namespace_id": namespace,
                    "adapter_id": adapter["id"],
                    "association_hash": manifest["association_hash"],
                    "changed_at": change["changed_at"],
                    "members": [
                        {
                            "metric": member["metric"],
                            "source_record_hash": member["record_hash"],
                            "observation_id": _observation_id(
                                identifier_key,
                                namespace,
                                adapter["id"],
                                member["record_hash"],
                                member["metric"],
                            ),
                        }
                        for member in manifest["members"]
                    ],
                }
            )
    normalized = dict(batch)
    normalized["availability"] = [
        {
            "adapter": item["adapter"],
            "metric": item["metric"],
            "state": item["state"],
            "evidence": item["evidence"],
            "checked_at": item["coverage_checked_at"],
            "coverage": item["coverage"],
        }
        for item in batch["availability"]
    ]
    return observations, deletions, reconciliations, normalized
