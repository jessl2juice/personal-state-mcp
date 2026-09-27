from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import hashlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
import json
import os
from pathlib import Path
import secrets
import statistics
import threading
from typing import Any
from urllib.parse import parse_qs, urlparse

from .adapters.libre_linkup import LibreLinkUpAdapter
from .collector import Collector
from .config import AppConfig, load_config
from .freshness import FreshnessPolicy, classify_freshness, classify_threshold
from .models import GlucoseReading, SAFETY_NOTICE, iso_utc, utc_now
from .storage import StateStore
from .watch_contract import (
    DIRECT_WEAR_LIVE_MAX_AGE_SECONDS,
    MAX_BODY_BYTES,
    OBSERVATION_METRICS,
    SCHEMA_VERSION,
    STATIC_UNAVAILABLE,
    WatchContractError,
    batch_to_observations,
    keyed_hash,
    observation_recency,
    parse_json_strict,
    signature_matches,
    validate_batch,
)
from .watch_contract_v2 import (
    SCHEMA_VERSION_V2,
    SAMSUNG_OBSERVATION_METRICS,
    V2_AVAILABILITY_ONLY_METRICS,
    batch_v2_to_observations,
    validate_batch_v2,
)


RANGES: dict[str, float | None] = {
    "3h": 3,
    "6h": 6,
    "12h": 12,
    "24h": 24,
    "3d": 72,
    "7d": 24 * 7,
    "30d": 24 * 30,
    "1y": 24 * 365,
    "all": None,
}
MAX_SOURCE_READINGS = 200_000
MAX_CHART_POINTS = 1_200
HEART_LIVE_MAX_AGE_SECONDS = DIRECT_WEAR_LIVE_MAX_AGE_SECONDS
HISTORICAL_OBSERVATION_METRICS = {
    "activity.total_calories",
    "activity.move_minutes",
    "activity.heart_points",
    "activity.heart_minutes",
    "vitals.resting_heart_rate",
}
WATCH_INGEST_REQUESTS_PER_MINUTE = 40
AVAILABILITY_TTL = timedelta(hours=26)
AVAILABILITY_PRECEDENCE = (
    "available",
    "permission_required",
    "provider_partnership_required",
    "companion_read_failed",
    "source_configuration_unverified",
    "no_observation",
    "platform_feature_unavailable",
    "adapter_not_installed",
    "not_exported_by_samsung_mapping",
    "not_exposed_by_provider",
    "unknown",
)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _range_since(range_key: str, now: datetime) -> datetime | None:
    hours = RANGES.get(range_key, RANGES["24h"])
    return now - timedelta(hours=hours) if hours is not None else None


def _downsample(readings: list[GlucoseReading], max_points: int = MAX_CHART_POINTS) -> list[GlucoseReading]:
    if len(readings) <= max_points:
        return readings
    bucket_count = max(1, (max_points - 2) // 2)
    interior = readings[1:-1]
    bucket_size = len(interior) / bucket_count
    sampled = [readings[0]]
    for index in range(bucket_count):
        start = int(index * bucket_size)
        end = max(start + 1, int((index + 1) * bucket_size))
        bucket = interior[start:end]
        if not bucket:
            continue
        low = min(bucket, key=lambda reading: reading.value_mg_dl)
        high = max(bucket, key=lambda reading: reading.value_mg_dl)
        extrema = [low] if low.id == high.id else [low, high]
        sampled.extend(sorted(extrema, key=lambda reading: reading.measured_at))
    sampled.append(readings[-1])
    return sampled


def _downsample_numeric_points(
    points: list[dict[str, Any]],
    *,
    max_points: int = MAX_CHART_POINTS,
) -> list[dict[str, Any]]:
    if len(points) <= max_points:
        return points
    bucket_count = max(1, (max_points - 2) // 2)
    interior = points[1:-1]
    bucket_size = len(interior) / bucket_count
    sampled = [points[0]]
    for index in range(bucket_count):
        start = int(index * bucket_size)
        end = max(start + 1, int((index + 1) * bucket_size))
        bucket = interior[start:end]
        if not bucket:
            continue
        low = min(bucket, key=lambda point: float(point["value"]))
        high = max(bucket, key=lambda point: float(point["value"]))
        extrema = [low] if low is high else [low, high]
        sampled.extend(sorted(extrema, key=lambda point: point["time"]))
    sampled.append(points[-1])
    return sampled


def _gaps(readings: list[GlucoseReading], max_gap_minutes: int = 30) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    for previous, current in zip(readings, readings[1:]):
        seconds = int((current.measured_at - previous.measured_at).total_seconds())
        if seconds > max_gap_minutes * 60:
            gaps.append(
                {
                    "from": iso_utc(previous.measured_at),
                    "to": iso_utc(current.measured_at),
                    "gap_seconds": seconds,
                }
            )
    return gaps[-100:]


def _stats(readings: list[GlucoseReading], threshold: int, near_margin: int) -> dict[str, Any]:
    values = [reading.value_mg_dl for reading in readings]
    if not values:
        return {
            "count": 0,
            "average_mg_dl": None,
            "median_mg_dl": None,
            "minimum_mg_dl": None,
            "maximum_mg_dl": None,
            "standard_deviation_mg_dl": None,
            "below_threshold": 0,
            "near_threshold": 0,
            "above_near_band": 0,
            "below_threshold_percent": None,
        }
    below = sum(value < threshold for value in values)
    near = sum(threshold <= value < threshold + near_margin for value in values)
    return {
        "count": len(values),
        "average_mg_dl": round(statistics.fmean(values), 1),
        "median_mg_dl": round(statistics.median(values), 1),
        "minimum_mg_dl": min(values),
        "maximum_mg_dl": max(values),
        "standard_deviation_mg_dl": round(statistics.pstdev(values), 1) if len(values) > 1 else 0.0,
        "below_threshold": below,
        "near_threshold": near,
        "above_near_band": len(values) - below - near,
        "below_threshold_percent": round((below / len(values)) * 100, 1),
    }


def _numeric_point_stats(points: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(point["value"]) for point in points]
    if not values:
        return {
            "count": 0,
            "average": None,
            "minimum": None,
            "maximum": None,
            "first_at": None,
            "last_at": None,
        }
    return {
        "count": len(values),
        "average": round(statistics.fmean(values), 1),
        "minimum": round(min(values), 1),
        "maximum": round(max(values), 1),
        "first_at": points[0]["time"],
        "last_at": points[-1]["time"],
    }


def _comparison_summary(
    glucose_readings: list[GlucoseReading],
    heart_points: list[dict[str, Any]],
    *,
    tolerance_minutes: int = 5,
) -> dict[str, Any]:
    if not glucose_readings or not heart_points:
        missing = []
        if not glucose_readings:
            missing.append("glucose")
        if not heart_points:
            missing.append("heart rate")
        return {
            "status": "unavailable",
            "paired_count": 0,
            "tolerance_minutes": tolerance_minutes,
            "window_start": None,
            "window_end": None,
            "message": f"No {' or '.join(missing)} measurements in the selected period.",
        }

    glucose_times = [reading.measured_at.astimezone(timezone.utc) for reading in glucose_readings]
    tolerance = timedelta(minutes=tolerance_minutes)
    nearest_index = 0
    paired_times: list[datetime] = []
    for point in heart_points:
        heart_time = _parse_dt(point.get("time"))
        if heart_time is None:
            continue
        while nearest_index + 1 < len(glucose_times) and abs(
            glucose_times[nearest_index + 1] - heart_time
        ) <= abs(glucose_times[nearest_index] - heart_time):
            nearest_index += 1
        if abs(glucose_times[nearest_index] - heart_time) <= tolerance:
            paired_times.append(heart_time)

    paired_count = len(paired_times)
    if paired_count == 0:
        status = "no_overlap"
        message = "No glucose and heart-rate measurements occur within 5 minutes of each other."
    elif paired_count < 3:
        status = "limited"
        message = f"Only {paired_count} measurements align within 5 minutes; comparison is limited."
    else:
        status = "comparable"
        message = f"{paired_count:,} heart-rate samples align with glucose within 5 minutes."
    return {
        "status": status,
        "paired_count": paired_count,
        "tolerance_minutes": tolerance_minutes,
        "window_start": iso_utc(paired_times[0]) if paired_times else None,
        "window_end": iso_utc(paired_times[-1]) if paired_times else None,
        "message": message,
    }


def _heart_sync_status(
    summary: dict[str, Any],
    last_upload: dict[str, Any] | None,
    now: datetime,
) -> dict[str, Any]:
    last_sample = _parse_dt(summary.get("last_at"))
    last_upload_at = _parse_dt(last_upload.get("received_at_utc")) if last_upload else None
    if last_sample is None:
        return {
            "status": "failure",
            "last_sample_at": None,
            "sample_age_seconds": None,
            "message": "The companion reached the server, but no heart-rate samples were transferred.",
        }
    age_seconds = max(0, int((now - last_sample).total_seconds()))
    if age_seconds <= HEART_LIVE_MAX_AGE_SECONDS:
        return {
            "status": "current",
            "last_sample_at": iso_utc(last_sample),
            "sample_age_seconds": age_seconds,
            "message": "Heart-rate data is live (measured within the past ten seconds).",
        }
    recent_upload = last_upload_at is not None and (now - last_upload_at) <= timedelta(hours=24)
    return {
        "status": "failure" if recent_upload else "stale",
        "last_sample_at": iso_utc(last_sample),
        "sample_age_seconds": age_seconds,
        "message": (
            "The companion reached the server, but its newest heart-rate sample is still "
            f"{iso_utc(last_sample)}. Current heart-rate transfer is failing."
            if recent_upload
            else f"The newest heart-rate sample is {iso_utc(last_sample)} and the companion has not uploaded recently."
        ),
    }


def _glucose_stream_status(
    reading: GlucoseReading | None,
    freshness: dict[str, Any],
) -> dict[str, Any]:
    """Describe whether the newest stored glucose can be presented as current."""
    freshness_status = freshness.get("status")
    age_seconds = freshness.get("measurement_age_seconds")
    current_window_seconds = freshness.get("max_age_seconds")

    if reading is None or freshness_status == "unavailable":
        return {
            "status": "unavailable",
            "current": False,
            "last_measurement_at": None,
            "measurement_age_seconds": age_seconds,
            "current_window_seconds": current_window_seconds,
            "message": "No glucose measurements are stored.",
        }
    if freshness_status == "fresh":
        return {
            "status": "current",
            "current": True,
            "last_measurement_at": iso_utc(reading.measured_at),
            "measurement_age_seconds": age_seconds,
            "current_window_seconds": current_window_seconds,
            "message": "Glucose data is inside the configured current-data window.",
        }
    if freshness_status == "recent":
        status = "delayed"
        message = "No new glucose measurement arrived inside the current-data window."
    else:
        status = "stopped"
        message = "Glucose updates have stopped; the sensor may have ended or stopped sharing."
    return {
        "status": status,
        "current": False,
        "last_measurement_at": iso_utc(reading.measured_at),
        "measurement_age_seconds": age_seconds,
        "current_window_seconds": current_window_seconds,
        "message": message,
    }


def _merged_watch_availability(
    rows: list[dict[str, Any]],
    active_installations: set[str],
    now: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    active_rows: list[dict[str, Any]] = []
    for item in rows:
        if item.get("installation_hash") not in active_installations:
            continue
        checked_at = _parse_dt(item.get("checked_at_utc"))
        enriched = dict(item)
        enriched["stale"] = checked_at is None or now - checked_at > AVAILABILITY_TTL
        active_rows.append(enriched)

    all_metrics = (
        set(OBSERVATION_METRICS)
        | SAMSUNG_OBSERVATION_METRICS
        | V2_AVAILABILITY_ONLY_METRICS
        | set(STATIC_UNAVAILABLE)
        | {item["metric"] for item in active_rows}
    )
    rank = {state: index for index, state in enumerate(AVAILABILITY_PRECEDENCE)}
    merged: list[dict[str, Any]] = []
    for metric in sorted(all_metrics):
        metric_rows = [item for item in active_rows if item["metric"] == metric]
        eligible = [item for item in metric_rows if not item["stale"]]
        if eligible:
            chosen = min(
                eligible,
                key=lambda item: (
                    rank.get(item["state"], len(rank)),
                    -(_parse_dt(item.get("checked_at_utc")) or datetime(1970, 1, 1, tzinfo=timezone.utc)).timestamp(),
                    item.get("adapter_id", ""),
                ),
            )
            merged.append({**chosen, "contributors": len(eligible), "merged": True})
            continue
        if metric_rows:
            newest = max(metric_rows, key=lambda item: item.get("checked_at_utc") or "")
            merged.append(
                {
                    **newest,
                    "state": "unknown",
                    "evidence": "Coverage report older than 26 hours.",
                    "contributors": 0,
                    "merged": True,
                }
            )
            continue
        if metric in STATIC_UNAVAILABLE:
            merged.append(
                {
                    "metric": metric,
                    "adapter_id": "android_health_connect",
                    "adapter_version": "legacy-v1",
                    "state": "not_exported_by_samsung_mapping",
                    "evidence": STATIC_UNAVAILABLE[metric],
                    "checked_at_utc": None,
                    "coverage": {"window_start": None, "window_end": None},
                    "contributors": 0,
                    "merged": True,
                }
            )
            continue
        merged.append(
            {
                "metric": metric,
                "adapter_id": None,
                "adapter_version": None,
                "state": "unknown",
                "evidence": "Awaiting an active companion coverage report.",
                "checked_at_utc": None,
                "coverage": {"window_start": None, "window_end": None},
                "contributors": 0,
                "merged": True,
            }
        )
    return merged, active_rows


class DashboardApp:
    def __init__(self, config: AppConfig):
        self.config = config
        self.store = StateStore(config.db_path)
        self.collector = Collector(
            store=self.store,
            adapters=[LibreLinkUpAdapter(config)],
            min_poll_interval_seconds=config.min_poll_interval_seconds,
        )
        self.refresh_lock = threading.Lock()
        self.token = secrets.token_urlsafe(24)
        self.static_dir = Path(__file__).with_name("dashboard_static")
        configured_apk = os.environ.get("PERSONAL_STATE_COMPANION_APK")
        self.companion_apk = (
            Path(configured_apk).expanduser()
            if configured_apk
            else Path(__file__).resolve().parents[2]
            / "android"
            / "health-connect-companion"
            / "app"
            / "build"
            / "outputs"
            / "apk"
            / "debug"
            / "app-debug.apk"
        )
        configured_hosts = os.environ.get("PERSONAL_STATE_DASHBOARD_ALLOWED_HOSTS", "")
        self.allowed_hosts = {
            "127.0.0.1",
            "localhost",
            *(host.strip().lower() for host in configured_hosts.split(",") if host.strip()),
        }
        self.ingest_hosts = {
            *(host.strip().lower() for host in config.watch_ingest_hosts if host.strip()),
        }

    def host_allowed(self, header: str | None) -> bool:
        if not header:
            return False
        host = header.rsplit(":", 1)[0].strip("[]").lower()
        return host in self.allowed_hosts

    def ingest_host_allowed(self, header: str | None) -> bool:
        if not header or not self.config.watch_enabled:
            return False
        host = header.rsplit(":", 1)[0].strip("[]").lower()
        return host in self.ingest_hosts or host in {"127.0.0.1", "localhost"}

    def watch_dashboard(
        self,
        now: datetime,
        since: datetime | None,
        glucose_readings: list[GlucoseReading] | None = None,
    ) -> dict[str, Any]:
        merged_availability, availability_by_adapter = _merged_watch_availability(
            self.store.watch_availability(),
            self.store.active_watch_installation_hashes(),
            now,
        )
        latest = self.store.latest_watch_by_metric()
        latest_payload = {}
        for metric, observation in latest.items():
            item = observation.public_dict()
            item["observation_recency"] = observation_recency(observation, now)
            latest_payload[metric] = item

        heart_records = self.store.watch_observations(
            metric="vitals.heart_rate", since=since, limit=100_000
        )
        heart_samples: list[dict[str, Any]] = []
        direct_heart_samples: list[dict[str, Any]] = []
        seen_heart_samples: set[tuple[str, float]] = set()
        for record in heart_records:
            for sample in record.payload.get("samples", []):
                sample_time = _parse_dt(sample.get("time"))
                sample_value = sample.get("value")
                if sample_time is None or sample_value is None or (since is not None and sample_time < since):
                    continue
                point = {
                    "time": iso_utc(sample_time),
                    "value": sample_value,
                    "attribution": record.attribution.get("state"),
                    "adapter": record.adapter_id,
                }
                key = (point["time"], float(sample_value))
                if key not in seen_heart_samples:
                    heart_samples.append(point)
                    seen_heart_samples.add(key)
                if record.adapter_id == "wear_health_services":
                    direct_heart_samples.append(point)
        heart_samples.sort(key=lambda sample: sample["time"])
        direct_heart_samples.sort(key=lambda sample: sample["time"])
        recent = self.store.watch_observations(limit=200)
        last_upload = self.store.latest_watch_sync()
        heart_summary = _numeric_point_stats(heart_samples)
        heart_sync_summary = _numeric_point_stats(direct_heart_samples)
        latest_heart = latest_payload.get("vitals.heart_rate")
        if heart_sync_summary["last_at"] is None and latest_heart and latest_heart.get("provenance", {}).get("adapter") == "wear_health_services":
            heart_sync_summary["last_at"] = latest_heart.get("observation_recency", {}).get("event_at")
        return {
            "enabled": self.config.watch_enabled,
            "configured": bool(self.config.watch_device_id and self.config.watch_device_secret and self.config.watch_identifier_key),
            "last_upload": last_upload,
            "latest": latest_payload,
            "heart_rate_samples": _downsample_numeric_points(heart_samples),
            "heart_rate_summary": heart_summary,
            "heart_rate_sync": _heart_sync_status(heart_sync_summary, last_upload, now),
            "comparison": _comparison_summary(glucose_readings or [], heart_samples),
            "recent": [
                {**item.public_dict(), "observation_recency": observation_recency(item, now)} for item in recent
            ],
            "availability": merged_availability,
            "availability_by_adapter": availability_by_adapter,
            "history": self.store.watch_history_overview(),
            "retention_days": self.config.watch_retention_days,
            "authoritative_source": "Samsung Health and Samsung Health Monitor remain authoritative for device features and official notices.",
            "sync_limit": "The dashboard knows companion read and upload times; Samsung/watch synchronization status remains unknown.",
        }

    def watch_metric_history(self, metric: str, range_key: str) -> dict[str, Any]:
        allowed_metrics = set(OBSERVATION_METRICS) | SAMSUNG_OBSERVATION_METRICS | HISTORICAL_OBSERVATION_METRICS
        if metric not in allowed_metrics:
            raise ValueError("unsupported_watch_metric")
        if range_key not in RANGES:
            range_key = "24h"
        now = utc_now()
        since = _range_since(range_key, now)
        records = self.store.watch_observations(metric=metric, since=since, limit=100_000)
        records.reverse()
        points: list[dict[str, Any]] = []
        for record in records:
            samples = record.payload.get("samples")
            if isinstance(samples, list):
                for sample in samples:
                    if not isinstance(sample, dict) or not isinstance(sample.get("value"), (int, float)):
                        continue
                    sample_time = _parse_dt(sample.get("time"))
                    if sample_time is not None:
                        points.append({"time": iso_utc(sample_time), "value": sample["value"], "unit": sample.get("unit", "")})
                continue
            event_at = record.measured_at or record.end_at or record.start_at
            value = record.payload.get("value")
            unit = record.payload.get("unit", "")
            if not isinstance(value, (int, float)) and metric in {"sleep.session", "sleep.samsung_session", "sleep.summary"}:
                if record.start_at is not None and record.end_at is not None:
                    value = (record.end_at - record.start_at).total_seconds() / 3600
                    unit = "hours"
            if event_at is not None and isinstance(value, (int, float)):
                points.append({"time": iso_utc(event_at), "value": value, "unit": unit})
        return {
            "metric": metric,
            "generated_at": iso_utc(now),
            "range": {"key": range_key, "window_start": iso_utc(since) if since else None, "window_end": iso_utc(now)},
            "points": _downsample_numeric_points(points),
            "observations": [
                {**record.public_dict(), "observation_recency": observation_recency(record, now)}
                for record in reversed(records[-200:])
            ],
        }

    def ingest_watch(self, body: bytes, headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
        now = utc_now()
        if not self.config.watch_enabled:
            return HTTPStatus.NOT_FOUND, {"error": "not_found"}
        if not self.config.watch_device_id or not self.config.watch_device_secret or not self.config.watch_identifier_key:
            return HTTPStatus.SERVICE_UNAVAILABLE, {"error": "watch_ingest_not_configured"}
        device_id = headers.get("x-psm-device-id", "")
        timestamp = headers.get("x-psm-timestamp", "")
        nonce = headers.get("x-psm-nonce", "")
        batch_id = headers.get("x-psm-batch-id", "")
        supplied_signature = headers.get("x-psm-signature", "")
        if device_id != self.config.watch_device_id or not signature_matches(
            self.config.watch_device_secret,
            timestamp,
            nonce,
            batch_id,
            body,
            supplied_signature,
            now,
        ):
            return HTTPStatus.UNAUTHORIZED, {"error": "invalid_device_authentication"}

        body_sha256 = hashlib.sha256(body).hexdigest()
        prior = self.store.watch_batch(batch_id)
        if prior:
            if prior["body_sha256"] == body_sha256:
                return HTTPStatus.OK, {"status": "already_committed", "batch_id": batch_id}
            return HTTPStatus.CONFLICT, {"error": "batch_id_conflict"}

        device_hash = keyed_hash(self.config.watch_identifier_key, f"device|{device_id}")
        if self.store.watch_requests_since(device_hash, now - timedelta(minutes=1)) >= WATCH_INGEST_REQUESTS_PER_MINUTE:
            return HTTPStatus.TOO_MANY_REQUESTS, {"error": "device_rate_limit", "retryable": True}
        nonce_hash = keyed_hash(self.config.watch_identifier_key, f"nonce|{nonce}")
        if not self.store.claim_watch_nonce(device_hash, nonce_hash, batch_id, now):
            return HTTPStatus.CONFLICT, {"error": "replay_rejected"}

        try:
            batch = parse_json_strict(body)
            schema_version = batch.get("schema_version")
            association_reconciliations: list[dict[str, Any]] = []
            if schema_version == SCHEMA_VERSION:
                validate_batch(batch, now)
                observations, deletions = batch_to_observations(batch, self.config.watch_identifier_key, now)
                storage_batch = batch
            elif schema_version == SCHEMA_VERSION_V2:
                validate_batch_v2(batch, now)
                if batch["adapter"]["id"] == "synthetic_test":
                    raise WatchContractError("adapter_not_allowed", "Synthetic adapters are not accepted by the production endpoint.")
                observations, deletions, association_reconciliations, storage_batch = batch_v2_to_observations(
                    batch,
                    self.config.watch_identifier_key,
                    now,
                )
            else:
                raise WatchContractError("upgrade_required", "The watch schema version is not supported.")
            if batch["batch_id"] != batch_id:
                raise WatchContractError("batch_id_mismatch", "The signed batch identifier does not match the body.")
            installation_hash = keyed_hash(
                self.config.watch_identifier_key,
                f"installation|{batch['installation_id']}",
            )
            result = self.store.apply_watch_batch(
                batch=storage_batch,
                body_sha256=body_sha256,
                device_hash=device_hash,
                installation_hash=installation_hash,
                observations=observations,
                deletions=deletions,
                association_reconciliations=association_reconciliations,
                now=now,
            )
            self.store.prune_watch_retention(self.config.watch_retention_days, now)
            return HTTPStatus.OK, {"status": result["status"], "batch_id": batch_id, "counts": {
                "inserted": result.get("inserted", 0),
                "updated": result.get("updated", 0),
                "deleted": result.get("deleted", 0),
                "ignored": result.get("ignored", 0),
            }}
        except WatchContractError as exc:
            status = HTTPStatus.CONFLICT if exc.code == "upgrade_required" else HTTPStatus.BAD_REQUEST
            return status, {"error": exc.code, "retryable": exc.code == "upgrade_required"}
        except ValueError as exc:
            conflict_codes = {"batch_id_conflict", "identity_namespace_mismatch"}
            code = str(exc) if str(exc) in conflict_codes else "invalid_payload"
            return HTTPStatus.CONFLICT if code in conflict_codes else HTTPStatus.BAD_REQUEST, {"error": code}

    def dashboard(self, range_key: str) -> dict[str, Any]:
        if range_key not in RANGES:
            range_key = "24h"
        now = utc_now()
        since = _range_since(range_key, now)
        if range_key == "all":
            readings = self.store.glucose_history(limit=MAX_SOURCE_READINGS + 1, descending=True)
            readings.reverse()
        else:
            readings = self.store.glucose_history(since=since)
        truncated = len(readings) > MAX_SOURCE_READINGS
        if truncated:
            readings = readings[-MAX_SOURCE_READINGS:]

        latest = self.store.latest_glucose()
        policy = FreshnessPolicy(
            fresh_max_age_seconds=self.config.fresh_max_age_seconds,
            recent_max_age_seconds=self.config.recent_max_age_seconds,
            future_skew_seconds=self.config.future_skew_seconds,
        )
        freshness = classify_freshness(latest, now, policy)
        threshold_context = classify_threshold(
            latest,
            freshness,
            self.config.glucose_threshold_mg_dl,
            self.config.near_threshold_margin_mg_dl,
        )
        stream_status = _glucose_stream_status(latest, freshness)
        chart = _downsample(readings)
        history = self.store.history_overview()
        runs = self.store.collector_runs(limit=12)
        for run in runs:
            metadata = run.pop("metadata_json", "{}")
            try:
                run["metadata"] = json.loads(metadata)
            except json.JSONDecodeError:
                run["metadata"] = {}

        return {
            "generated_at": iso_utc(now),
            "range": {
                "key": range_key,
                "hours": RANGES[range_key],
                "window_start": iso_utc(since) if since else (iso_utc(readings[0].measured_at) if readings else None),
                "window_end": iso_utc(now),
                "start": iso_utc(readings[0].measured_at) if readings else None,
                "end": iso_utc(readings[-1].measured_at) if readings else None,
                "truncated": truncated,
            },
            "current": {
                "reading": latest.public_dict() if latest else None,
                "freshness": freshness,
                "threshold_context": threshold_context,
                "stream": stream_status,
            },
            "stats": _stats(
                readings,
                self.config.glucose_threshold_mg_dl,
                self.config.near_threshold_margin_mg_dl,
            ),
            "history": history,
            "chart": [
                {
                    "measured_at": iso_utc(reading.measured_at),
                    "received_at": iso_utc(reading.received_at),
                    "value_mg_dl": reading.value_mg_dl,
                    "trend": reading.trend,
                    "trend_raw": reading.trend_raw,
                    "sample_type": reading.sample_type,
                }
                for reading in chart
            ],
            "table": [reading.public_dict() for reading in reversed(readings[-200:])],
            "gaps": _gaps(readings),
            "collector": {
                "last_run": runs[0] if runs else None,
                "runs": runs,
                "poll_interval_seconds": self.config.min_poll_interval_seconds,
            },
            "provenance": latest.provenance.public_dict() if latest else None,
            "settings": {
                "decision_support_threshold_mg_dl": self.config.glucose_threshold_mg_dl,
                "near_threshold_margin_mg_dl": self.config.near_threshold_margin_mg_dl,
                "fresh_max_age_seconds": self.config.fresh_max_age_seconds,
                "recent_max_age_seconds": self.config.recent_max_age_seconds,
                "history_database": str(self.config.db_path),
            },
            "watch": self.watch_dashboard(now, since, readings),
            "safety": SAFETY_NOTICE,
        }

    def live_state(self) -> dict[str, Any]:
        now = utc_now()
        latest_glucose = self.store.latest_glucose()
        policy = FreshnessPolicy(
            fresh_max_age_seconds=self.config.fresh_max_age_seconds,
            recent_max_age_seconds=self.config.recent_max_age_seconds,
            future_skew_seconds=self.config.future_skew_seconds,
        )
        freshness = classify_freshness(latest_glucose, now, policy)
        threshold_context = classify_threshold(
            latest_glucose,
            freshness,
            self.config.glucose_threshold_mg_dl,
            self.config.near_threshold_margin_mg_dl,
        )
        stream_status = _glucose_stream_status(latest_glucose, freshness)

        latest_heart = next(
            (
                observation
                for observation in self.store.watch_observations(metric="vitals.heart_rate", limit=200)
                if observation.adapter_id == "wear_health_services"
            ),
            None,
        )
        heart_payload = None
        heart_last_at = None
        if latest_heart:
            heart_payload = latest_heart.public_dict()
            heart_payload["observation_recency"] = observation_recency(latest_heart, now)
            heart_last_at = heart_payload["observation_recency"].get("event_at")

        last_upload = self.store.latest_watch_sync()
        return {
            "generated_at": iso_utc(now),
            "current": {
                "reading": latest_glucose.public_dict() if latest_glucose else None,
                "freshness": freshness,
                "threshold_context": threshold_context,
                "stream": stream_status,
            },
            "watch": {
                "latest": {"vitals.heart_rate": heart_payload} if heart_payload else {},
                "heart_rate_sync": _heart_sync_status({"last_at": heart_last_at}, last_upload, now),
            },
        }

    def refresh(self) -> dict[str, Any]:
        if not self.refresh_lock.acquire(blocking=False):
            return {"status": "busy", "message": "A refresh is already running."}
        try:
            last = self.store.last_collector_run()
            last_finished = _parse_dt(last.get("finished_at_utc")) if last else None
            now = utc_now()
            if last_finished and (now - last_finished).total_seconds() < self.config.min_poll_interval_seconds:
                return {
                    "status": "skipped",
                    "message": "The collector already refreshed inside the minimum polling interval.",
                    "last_finished_at": iso_utc(last_finished),
                }
            return {"status": "ok", "results": self.collector.collect_once()}
        finally:
            self.refresh_lock.release()

    def csv_export(self, range_key: str) -> bytes:
        if range_key not in RANGES:
            range_key = "24h"
        since = _range_since(range_key, utc_now())
        readings = self.store.glucose_history(since=since)
        output = StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            [
                "measured_at_utc",
                "received_at_utc",
                "value_mg_dl",
                "trend",
                "trend_raw",
                "sample_type",
                "adapter",
                "vendor",
                "source",
            ]
        )
        for reading in readings:
            provenance = reading.provenance
            writer.writerow(
                [
                    iso_utc(reading.measured_at),
                    iso_utc(reading.received_at),
                    reading.value_mg_dl,
                    reading.trend or "",
                    reading.trend_raw if reading.trend_raw is not None else "",
                    reading.sample_type,
                    provenance.adapter,
                    provenance.vendor,
                    provenance.source,
                ]
            )
        return output.getvalue().encode("utf-8")

    def watch_csv_export(self, range_key: str) -> bytes:
        if range_key not in RANGES:
            range_key = "24h"
        since = _range_since(range_key, utc_now())
        observations = []
        offset = 0
        while True:
            page = self.store.watch_observations(
                since=since,
                limit=5000,
                offset=offset,
                descending=False,
            )
            observations.extend(page)
            if len(page) < 5000:
                break
            offset += len(page)

        output = StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            [
                "record_id",
                "metric",
                "record_kind",
                "measured_at_utc",
                "start_at_utc",
                "end_at_utc",
                "observed_by_companion_at_utc",
                "ingested_at_server_utc",
                "upstream_last_modified_at_utc",
                "recording_method",
                "attribution_state",
                "attribution_evidence",
                "device_type",
                "device_model",
                "schema_version",
                "adapter_id",
                "adapter_version",
                "identity_namespace_id",
                "source_package",
                "source_record_hash",
                "association_hash",
                "local_date",
                "payload_json",
            ]
        )
        for observation in observations:
            attribution = observation.attribution
            writer.writerow(
                [
                    observation.id,
                    observation.metric,
                    observation.record_kind,
                    iso_utc(observation.measured_at),
                    iso_utc(observation.start_at),
                    iso_utc(observation.end_at),
                    iso_utc(observation.observed_by_companion_at),
                    iso_utc(observation.ingested_at_server),
                    iso_utc(observation.upstream_last_modified_at),
                    observation.recording_method,
                    attribution.get("state", "unknown"),
                    attribution.get("evidence", ""),
                    attribution.get("device_type", ""),
                    attribution.get("device_model", ""),
                    observation.schema_version,
                    observation.adapter_id,
                    observation.adapter_version,
                    observation.identity_namespace_id,
                    observation.source_package,
                    observation.source_record_hash,
                    observation.association_hash or "",
                    observation.local_date or "",
                    json.dumps(observation.payload, separators=(",", ":"), sort_keys=True),
                ]
            )
        return output.getvalue().encode("utf-8")


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], app: DashboardApp):
        self.app = app
        super().__init__(address, DashboardHandler)


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer

    def _security_headers(self, content_type: str, length: int) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")

    def _send_bytes(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self._security_headers(content_type, len(payload))
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        self._send_bytes(status, data, "application/json; charset=utf-8")

    def _local_host(self) -> bool:
        return self.server.app.host_allowed(self.headers.get("Host"))

    def _range_key(self, query: dict[str, list[str]]) -> str:
        value = query.get("range", ["24h"])[0]
        return value if value in RANGES else "24h"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        host = self.headers.get("Host")
        if not self._local_host() and self.server.app.ingest_host_allowed(host):
            if parsed.path == "/api/watch/health":
                self._send_json(HTTPStatus.OK, {"ok": True})
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        if not self._local_host():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "local_access_only"})
            return
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/":
                html = (self.server.app.static_dir / "index.html").read_text(encoding="utf-8")
                html = html.replace("__PSM_TOKEN__", self.server.app.token)
                self._send_bytes(HTTPStatus.OK, html.encode("utf-8"), "text/html; charset=utf-8")
            elif parsed.path == "/static/app.css":
                self._send_bytes(
                    HTTPStatus.OK,
                    (self.server.app.static_dir / "app.css").read_bytes(),
                    "text/css; charset=utf-8",
                )
            elif parsed.path == "/static/app.js":
                self._send_bytes(
                    HTTPStatus.OK,
                    (self.server.app.static_dir / "app.js").read_bytes(),
                    "text/javascript; charset=utf-8",
                )
            elif parsed.path == "/downloads/personal-state-companion.apk":
                if not self.server.app.companion_apk.is_file():
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "companion_not_installed"})
                    return
                payload = self.server.app.companion_apk.read_bytes()
                self.send_response(HTTPStatus.OK)
                self._security_headers("application/vnd.android.package-archive", len(payload))
                self.send_header("Content-Disposition", 'attachment; filename="personal-state-companion-0.4.0.apk"')
                self.end_headers()
                self.wfile.write(payload)
            elif parsed.path == "/api/dashboard":
                self._send_json(HTTPStatus.OK, self.server.app.dashboard(self._range_key(query)))
            elif parsed.path == "/api/live":
                self._send_json(HTTPStatus.OK, self.server.app.live_state())
            elif parsed.path == "/api/watch/history":
                metric = query.get("metric", [""])[0]
                try:
                    self._send_json(HTTPStatus.OK, self.server.app.watch_metric_history(metric, self._range_key(query)))
                except ValueError:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": "unsupported_watch_metric"})
            elif parsed.path == "/api/export.csv":
                range_key = self._range_key(query)
                payload = self.server.app.csv_export(range_key)
                self.send_response(HTTPStatus.OK)
                self._security_headers("text/csv; charset=utf-8", len(payload))
                self.send_header("Content-Disposition", f'attachment; filename="personal-state-{range_key}.csv"')
                self.end_headers()
                self.wfile.write(payload)
            elif parsed.path == "/api/watch/export.csv":
                range_key = self._range_key(query)
                payload = self.server.app.watch_csv_export(range_key)
                self.send_response(HTTPStatus.OK)
                self._security_headers("text/csv; charset=utf-8", len(payload))
                self.send_header("Content-Disposition", f'attachment; filename="personal-state-watch-{range_key}.csv"')
                self.end_headers()
                self.wfile.write(payload)
            elif parsed.path == "/api/health":
                self._send_json(HTTPStatus.OK, {"ok": True, "generated_at": iso_utc(utc_now())})
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
        except Exception:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "dashboard_request_failed"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/watch/ingest":
            if not self.server.app.ingest_host_allowed(self.headers.get("Host")):
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "ingest_host_required"})
                return
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                self._send_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "application_json_required"})
                return
            if self.headers.get("Content-Encoding"):
                self._send_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "content_encoding_not_supported"})
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                content_length = -1
            if content_length < 1 or content_length > MAX_BODY_BYTES:
                self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "invalid_body_size"})
                return
            body = self.rfile.read(content_length)
            headers = {key.lower(): value for key, value in self.headers.items()}
            status, payload = self.server.app.ingest_watch(body, headers)
            self._send_json(status, payload)
            return
        if not self._local_host():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "local_access_only"})
            return
        if self.headers.get("X-Personal-State-Token") != self.server.app.token:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "invalid_request_token"})
            return
        if parsed.path != "/api/refresh":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        try:
            result = self.server.app.refresh()
            self._send_json(HTTPStatus.OK, result)
        except Exception:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "refresh_failed"})

    def log_message(self, _format: str, *_args: object) -> None:
        return


def serve(host: str = "127.0.0.1", port: int = 8766) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("The dashboard may only bind to a loopback address.")
    app = DashboardApp(load_config())
    server = DashboardServer((host, port), app)
    print(f"Personal State dashboard: http://{host}:{port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local Personal State dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    serve(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
