from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return dt.astimezone(timezone.utc)


def iso_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return to_utc(dt).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_hash(value: str) -> str:
    payload = f"personal-state-mcp:v1:{value}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def reading_id(
    adapter: str,
    source_patient_hash: str | None,
    measured_at: datetime,
    value_mg_dl: int,
    sample_type: str,
) -> str:
    parts = [
        adapter,
        source_patient_hash or "",
        iso_utc(measured_at) or "",
        str(value_mg_dl),
        sample_type,
    ]
    return stable_hash("|".join(parts))


@dataclass(frozen=True)
class Provenance:
    adapter: str
    vendor: str
    source: str
    source_patient_hash: str | None = None
    sensor_hash: str | None = None
    measurement_timestamp_source: str = "unknown"
    received_timestamp_source: str = "collector_clock"
    selected_region_host: str | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "vendor": self.vendor,
            "source": self.source,
            "source_patient_hash": self.source_patient_hash,
            "sensor_hash": self.sensor_hash,
            "measurement_timestamp_source": self.measurement_timestamp_source,
            "received_timestamp_source": self.received_timestamp_source,
            "selected_region_host": self.selected_region_host,
        }


@dataclass(frozen=True)
class GlucoseReading:
    adapter: str
    value_mg_dl: int
    measured_at: datetime
    received_at: datetime
    stored_at: datetime
    provenance: Provenance
    trend: str | None = None
    trend_rate_mg_dl_per_min: float | None = None
    trend_raw: str | int | None = None
    sample_type: str = "unknown"
    raw: dict[str, Any] | None = None
    id: str | None = None

    def with_id(self) -> "GlucoseReading":
        if self.id:
            return self
        return GlucoseReading(
            id=reading_id(
                self.adapter,
                self.provenance.source_patient_hash,
                self.measured_at,
                self.value_mg_dl,
                self.sample_type,
            ),
            adapter=self.adapter,
            value_mg_dl=self.value_mg_dl,
            measured_at=self.measured_at,
            received_at=self.received_at,
            stored_at=self.stored_at,
            provenance=self.provenance,
            trend=self.trend,
            trend_rate_mg_dl_per_min=self.trend_rate_mg_dl_per_min,
            trend_raw=self.trend_raw,
            sample_type=self.sample_type,
            raw=self.raw,
        )

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.with_id().id,
            "value_mg_dl": self.value_mg_dl,
            "trend": self.trend,
            "trend_rate_mg_dl_per_min": self.trend_rate_mg_dl_per_min,
            "trend_raw": self.trend_raw,
            "sample_type": self.sample_type,
            "measured_at": iso_utc(self.measured_at),
            "received_at": iso_utc(self.received_at),
            "stored_at": iso_utc(self.stored_at),
            "provenance": self.provenance.public_dict(),
        }

    def persistence_tuple(self) -> tuple[Any, ...]:
        reading = self.with_id()
        return (
            reading.id,
            reading.adapter,
            reading.provenance.source_patient_hash,
            reading.value_mg_dl,
            reading.trend,
            None if reading.trend_raw is None else str(reading.trend_raw),
            reading.sample_type,
            iso_utc(reading.measured_at),
            iso_utc(reading.received_at),
            iso_utc(reading.stored_at),
            json.dumps(reading.provenance.public_dict(), sort_keys=True),
            json.dumps(reading.raw or {}, sort_keys=True),
        )


@dataclass(frozen=True)
class HealthObservation:
    id: str
    metric: str
    category: str
    record_kind: str
    payload: dict[str, Any]
    observed_by_companion_at: datetime
    ingested_at_server: datetime
    source_package: str
    recording_method: str
    attribution: dict[str, Any]
    installation_hash: str
    source_record_hash: str
    adapter_id: str = "android_health_connect"
    adapter_version: str = "legacy-v1"
    identity_namespace_id: str = "legacy-v1"
    association_hash: str | None = None
    local_date: str | None = None
    measured_at: datetime | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    upstream_last_modified_at: datetime | None = None
    zone_offset: str | None = None
    start_zone_offset: str | None = None
    end_zone_offset: str | None = None
    schema_version: str = "personal-state-watch-batch/v1"

    @property
    def event_at(self) -> datetime:
        if self.record_kind == "series":
            samples = self.payload.get("samples")
            if isinstance(samples, list) and samples:
                latest = samples[-1].get("time") if isinstance(samples[-1], dict) else None
                if isinstance(latest, str):
                    return datetime.fromisoformat(latest.replace("Z", "+00:00")).astimezone(timezone.utc)
        return self.measured_at or self.end_at or self.start_at or self.observed_by_companion_at

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "metric": self.metric,
            "category": self.category,
            "record_kind": self.record_kind,
            "payload": self.payload,
            "measured_at": iso_utc(self.measured_at),
            "start_at": iso_utc(self.start_at),
            "end_at": iso_utc(self.end_at),
            "local_date": self.local_date,
            "observed_by_companion_at": iso_utc(self.observed_by_companion_at),
            "ingested_at_server": iso_utc(self.ingested_at_server),
            "upstream_last_modified_at": iso_utc(self.upstream_last_modified_at),
            "zone_offset": self.zone_offset,
            "start_zone_offset": self.start_zone_offset,
            "end_zone_offset": self.end_zone_offset,
            "provenance": {
                "adapter": self.adapter_id,
                "adapter_version": self.adapter_version,
                "vendor": "Samsung",
                "source": "Samsung Health Data SDK" if self.adapter_id == "android_samsung_health_data" else (
                    "Direct Wear OS Health Services" if self.adapter_id == "wear_health_services" else "Samsung Health via Health Connect"
                ),
                "source_package": self.source_package,
                "recording_method": self.recording_method,
                "attribution": self.attribution,
                "installation_hash": self.installation_hash,
                "source_record_hash": self.source_record_hash,
                "identity_namespace_id": self.identity_namespace_id,
                "association_hash": self.association_hash,
            },
            "schema_version": self.schema_version,
        }

    def persistence_tuple(self) -> tuple[Any, ...]:
        return (
            self.id,
            self.metric,
            self.category,
            self.record_kind,
            json.dumps(self.payload, separators=(",", ":"), sort_keys=True),
            iso_utc(self.measured_at),
            iso_utc(self.start_at),
            iso_utc(self.end_at),
            iso_utc(self.observed_by_companion_at),
            iso_utc(self.ingested_at_server),
            iso_utc(self.upstream_last_modified_at),
            self.source_package,
            self.recording_method,
            json.dumps(self.attribution, separators=(",", ":"), sort_keys=True),
            self.installation_hash,
            self.source_record_hash,
            self.zone_offset,
            self.start_zone_offset,
            self.end_zone_offset,
            self.schema_version,
            self.adapter_id,
            self.adapter_version,
            self.identity_namespace_id,
            self.association_hash,
            self.local_date,
        )

@dataclass(frozen=True)
class ErrorInfo:
    code: str
    message: str
    retryable: bool = False

    def public_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }


@dataclass(frozen=True)
class ResponseEnvelope:
    ok: bool
    tool: str
    generated_at: datetime
    data: dict[str, Any]
    freshness: dict[str, Any]
    provenance: dict[str, Any]
    safety: dict[str, Any]
    errors: list[ErrorInfo] = field(default_factory=list)

    def public_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "tool": self.tool,
            "generated_at": iso_utc(self.generated_at),
            "data": self.data,
            "freshness": self.freshness,
            "provenance": self.provenance,
            "safety": self.safety,
            "errors": [error.public_dict() for error in self.errors],
        }


SAFETY_NOTICE = {
    "use": "informational_context_only",
    "not_for": ["alarms", "diagnosis", "treatment_automation"],
    "message": "Use the official Libre app/sensor for alerts and health decisions.",
}


WATCH_SAFETY_NOTICE = {
    "use": "informational_context_only",
    "not_for": ["alarms", "diagnosis", "risk_scoring", "treatment_automation"],
    "message": "Samsung Health and Samsung Health Monitor remain authoritative for device features and official notices.",
}
