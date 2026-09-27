from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import csv
import hashlib
import io
import json
from pathlib import Path
import re
from typing import Any, Iterable
import xml.etree.ElementTree as ET
import zipfile
from zoneinfo import ZoneInfo

from .models import GlucoseReading, HealthObservation, Provenance, iso_utc, stable_hash, utc_now


LIBRE_HEADER_MARKERS = {
    "device timestamp",
    "historic glucose mg/dl",
    "scan glucose mg/dl",
    "historic glucose mmol/l",
    "scan glucose mmol/l",
}


@dataclass
class ImportPreview:
    source: str
    path: str
    files: int = 0
    rows_seen: int = 0
    records_parsed: int = 0
    records_rejected: int = 0
    first_measured_at: str | None = None
    last_measured_at: str | None = None
    warnings: list[str] = field(default_factory=list)

    def public_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "path": self.path,
            "files": self.files,
            "rows_seen": self.rows_seen,
            "records_parsed": self.records_parsed,
            "records_rejected": self.records_rejected,
            "first_measured_at": self.first_measured_at,
            "last_measured_at": self.last_measured_at,
            "warnings": self.warnings,
        }


@dataclass
class HealthImportPreview(ImportPreview):
    metric_counts: dict[str, int] = field(default_factory=dict)
    data_point_counts: dict[str, int] = field(default_factory=dict)
    skipped_files: int = 0
    source_files: dict[str, int] = field(default_factory=dict)

    def public_dict(self) -> dict[str, object]:
        payload = super().public_dict()
        payload.update(
            {
                "metric_counts": dict(sorted(self.metric_counts.items())),
                "data_point_counts": dict(sorted(self.data_point_counts.items())),
                "skipped_files": self.skipped_files,
                "source_files": dict(sorted(self.source_files.items())),
            }
        )
        return payload


def _normalized(value: str) -> str:
    return " ".join(value.strip().lower().replace("\ufeff", "").split())


def _decode_csv(path: Path) -> str:
    payload = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("The CSV encoding is not supported.")


def _header_index(rows: list[list[str]]) -> int:
    for index, row in enumerate(rows[:25]):
        names = {_normalized(value) for value in row}
        if "device timestamp" in names and names.intersection(LIBRE_HEADER_MARKERS - {"device timestamp"}):
            return index
    raise ValueError("This does not look like a LibreView glucose CSV export.")


def _parse_local_timestamp(value: str, source_timezone: str) -> datetime:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("missing timestamp")
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is None:
        formats = (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%m-%d-%Y %I:%M:%S %p",
            "%m-%d-%Y %I:%M %p",
            "%m/%d/%Y %I:%M:%S %p",
            "%m/%d/%Y %I:%M %p",
            "%d-%m-%Y %H:%M:%S",
            "%d-%m-%Y %H:%M",
        )
        for pattern in formats:
            try:
                parsed = datetime.strptime(cleaned, pattern)
                break
            except ValueError:
                continue
    if parsed is None:
        raise ValueError(f"unsupported timestamp: {cleaned}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(source_timezone))
    return parsed.astimezone(timezone.utc)


def _parse_glucose(row: dict[str, str]) -> tuple[int, str] | None:
    candidates = (
        ("historic glucose mg/dl", "history", 1.0),
        ("scan glucose mg/dl", "scan", 1.0),
        ("historic glucose mmol/l", "history", 18.0182),
        ("scan glucose mmol/l", "scan", 18.0182),
    )
    for column, sample_type, multiplier in candidates:
        value = row.get(column, "").strip()
        if not value:
            continue
        try:
            glucose = int(round(float(value.replace(",", ".")) * multiplier))
        except ValueError as exc:
            raise ValueError(f"invalid glucose value: {value}") from exc
        if not 20 <= glucose <= 600:
            raise ValueError(f"glucose value outside import bounds: {glucose}")
        return glucose, sample_type
    return None


def parse_libreview_csv(
    path: Path,
    *,
    source_timezone: str,
    stored_at: datetime | None = None,
) -> tuple[list[GlucoseReading], ImportPreview]:
    path = Path(path)
    text = _decode_csv(path)
    raw_rows = list(csv.reader(io.StringIO(text)))
    header_at = _header_index(raw_rows)
    headers = [_normalized(value) for value in raw_rows[header_at]]
    preview = ImportPreview(source="libreview_csv", path=str(path), files=1)
    readings: list[GlucoseReading] = []
    rejected_examples: list[str] = []
    stored = stored_at or utc_now()
    file_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()

    for row_number, values in enumerate(raw_rows[header_at + 1 :], start=header_at + 2):
        if not any(value.strip() for value in values):
            continue
        preview.rows_seen += 1
        padded = values + [""] * max(0, len(headers) - len(values))
        row = dict(zip(headers, padded, strict=False))
        try:
            glucose = _parse_glucose(row)
            if glucose is None:
                continue
            value_mg_dl, sample_type = glucose
            measured_at = _parse_local_timestamp(row.get("device timestamp", ""), source_timezone)
            device = row.get("device", "")
            serial = row.get("serial number", "")
            sensor_fingerprint = "|".join(part for part in (device, serial) if part)
            reading = GlucoseReading(
                adapter="libreview_csv",
                value_mg_dl=value_mg_dl,
                measured_at=measured_at,
                received_at=measured_at,
                stored_at=stored,
                provenance=Provenance(
                    adapter="libreview_csv",
                    vendor="abbott_libreview",
                    source="libreview_glucose_history_export",
                    source_patient_hash=None,
                    sensor_hash=stable_hash(sensor_fingerprint) if sensor_fingerprint else None,
                    measurement_timestamp_source="libreview_device_timestamp_with_configured_timezone",
                    received_timestamp_source="historical_export_measurement_time",
                ),
                sample_type=sample_type,
                raw={
                    "record_type": row.get("record type", ""),
                    "source_row": row_number,
                    "import_file_sha256": file_sha256,
                },
            ).with_id()
            readings.append(reading)
        except (ValueError, OverflowError) as exc:
            preview.records_rejected += 1
            if len(rejected_examples) < 5:
                rejected_examples.append(f"row {row_number}: {exc}")

    readings.sort(key=lambda item: item.measured_at)
    preview.records_parsed = len(readings)
    if readings:
        preview.first_measured_at = readings[0].measured_at.isoformat().replace("+00:00", "Z")
        preview.last_measured_at = readings[-1].measured_at.isoformat().replace("+00:00", "Z")
    if rejected_examples:
        preview.warnings.append("Rejected examples: " + "; ".join(rejected_examples))
    if not readings:
        preview.warnings.append("No glucose readings were found in the export.")
    return readings, preview


def inspect_history_export(path: Path) -> ImportPreview:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    names: list[str]
    if path.is_dir():
        names = [str(item.relative_to(path)) for item in path.rglob("*") if item.is_file()]
    elif zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            names = [item.filename for item in archive.infolist() if not item.is_dir()]
    else:
        names = [path.name]
    lowered = "\n".join(name.lower() for name in names)
    if "takeout/fit" in lowered or "/fit/" in lowered or "daily activity metrics" in lowered:
        source = "google_fit_takeout"
    elif "com.samsung" in lowered or "samsung health" in lowered or "shealth" in lowered:
        source = "samsung_health_export"
    elif "glucose" in lowered or "libreview" in lowered or "freestyle" in lowered:
        source = "possible_libreview_export"
    else:
        source = "unknown"
    preview = ImportPreview(source=source, path=str(path), files=len(names))
    suffixes: dict[str, int] = {}
    for name in names:
        suffix = Path(name).suffix.lower() or "[no extension]"
        suffixes[suffix] = suffixes.get(suffix, 0) + 1
    preview.warnings.append(
        "File types: " + ", ".join(f"{suffix}={count}" for suffix, count in sorted(suffixes.items()))
    )
    return preview


GOOGLE_FIT_RAW_TYPES = {
    "com.google.heart_rate.bpm": "vitals.heart_rate",
    "com.google.oxygen_saturation": "vitals.oxygen_saturation",
    "com.google.sleep.segment": "sleep.session",
    "com.google.blood_pressure": "vitals.blood_pressure",
    "com.google.weight": "body.weight",
    "com.google.body.fat.percentage": "body.body_fat",
    "com.google.height": "body.height",
    "com.google.speed": "activity.speed",
}

GOOGLE_FIT_DAILY_COLUMNS = {
    "Step count": ("activity.steps", "count"),
    "Distance (m)": ("activity.distance", "m"),
    "Calories (kcal)": ("activity.total_calories", "kcal"),
    "Move Minutes count": ("activity.move_minutes", "minutes"),
    "Heart Points": ("activity.heart_points", "points"),
    "Heart Minutes": ("activity.heart_minutes", "minutes"),
}

GOOGLE_FIT_SLEEP_STAGES = {
    1: "awake",
    2: "sleeping",
    3: "out_of_bed",
    4: "light",
    5: "deep",
    6: "rem",
}


class _ArchiveReader:
    def __init__(self, path: Path):
        self.path = path
        self.archive: zipfile.ZipFile | None = None

    def __enter__(self) -> "_ArchiveReader":
        if self.path.is_file():
            self.archive = zipfile.ZipFile(self.path)
        return self

    def __exit__(self, *_args: object) -> None:
        if self.archive is not None:
            self.archive.close()

    def names(self) -> list[str]:
        if self.archive is not None:
            return [item.filename for item in self.archive.infolist() if not item.is_dir()]
        return [str(item.relative_to(self.path)).replace("\\", "/") for item in self.path.rglob("*") if item.is_file()]

    def read(self, name: str) -> bytes:
        if self.archive is not None:
            return self.archive.read(name)
        return (self.path / Path(name)).read_bytes()


def _nanos_to_utc(value: Any) -> datetime:
    return datetime.fromtimestamp(int(value) / 1_000_000_000, tz=timezone.utc)


def _millis_to_utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)


def _fit_scalars(point: dict[str, Any]) -> list[int | float | str | None]:
    values: list[int | float | str | None] = []
    for item in point.get("fitValue", []):
        value = item.get("value", {}) if isinstance(item, dict) else {}
        scalar = next((value[key] for key in ("fpVal", "intVal", "stringVal") if key in value), None)
        values.append(scalar)
    return values


def _google_source(name: str) -> tuple[str, str, dict[str, Any]]:
    lowered = name.lower()
    if "com.sec.android.app.shealth" in lowered:
        return (
            "com.sec.android.app.shealth",
            "automatic",
            {"state": "samsung_health_unattributed", "evidence": "Google Fit raw Samsung Health export"},
        )
    if "com.omronhealthcare.omronconnect" in lowered:
        return (
            "com.omronhealthcare.omronconnect",
            "automatic",
            {"state": "external_device", "evidence": "Google Fit raw Omron Connect export"},
        )
    if "com.vont.smartlight" in lowered:
        return (
            "com.vont.smartlight",
            "automatic",
            {"state": "external_device", "evidence": "Google Fit raw smart-scale export"},
        )
    if "user_input" in lowered:
        return (
            "com.google.android.apps.fitness",
            "manual",
            {"state": "manual", "evidence": "Google Fit user-entered record"},
        )
    return (
        "com.google.android.apps.fitness",
        "unknown",
        {"state": "unknown", "evidence": "Google Fit Takeout export"},
    )


def _health_observation(
    *,
    metric: str,
    record_kind: str,
    payload: dict[str, Any],
    imported_at: datetime,
    source_package: str,
    recording_method: str,
    attribution: dict[str, Any],
    measured_at: datetime | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    upstream_last_modified_at: datetime | None = None,
    local_date: str | None = None,
    source_identity: str = "",
) -> HealthObservation:
    canonical = json.dumps(
        {
            "metric": metric,
            "payload": payload,
            "measured_at": iso_utc(measured_at),
            "start_at": iso_utc(start_at),
            "end_at": iso_utc(end_at),
            "source_package": source_package,
            "source_identity": source_identity,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    record_hash = stable_hash(canonical)
    return HealthObservation(
        id=stable_hash(f"google_fit_takeout|{record_hash}"),
        metric=metric,
        category=metric.split(".", 1)[0],
        record_kind=record_kind,
        payload=payload,
        measured_at=measured_at,
        start_at=start_at,
        end_at=end_at,
        observed_by_companion_at=imported_at,
        ingested_at_server=imported_at,
        upstream_last_modified_at=upstream_last_modified_at,
        source_package=source_package,
        recording_method=recording_method,
        attribution=attribution,
        installation_hash="google-fit-takeout",
        source_record_hash=record_hash,
        adapter_id="google_fit_takeout",
        adapter_version="v1",
        identity_namespace_id="google-fit-takeout-v1",
        local_date=local_date,
        schema_version="personal-state-historical-import/v1",
    )


def _raw_metric_from_name(name: str) -> str | None:
    for candidate, metric in GOOGLE_FIT_RAW_TYPES.items():
        if f"/raw_{candidate}_" in name:
            return metric
    return None


def _parse_raw_points(
    name: str,
    payload: dict[str, Any],
    imported_at: datetime,
) -> list[HealthObservation]:
    metric = _raw_metric_from_name(name)
    if metric is None:
        return []
    source_package, recording_method, attribution = _google_source(name)
    points = payload.get("Data Points", [])
    if not isinstance(points, list):
        return []

    if metric in {"vitals.heart_rate", "activity.speed"}:
        unit = "bpm" if metric == "vitals.heart_rate" else "m/s"
        maximum = 500 if metric == "vitals.heart_rate" else 500
        grouped: dict[str, dict[str, Any]] = {}
        for point in points:
            values = _fit_scalars(point)
            if not values or not isinstance(values[0], (int, float)):
                continue
            number = float(values[0])
            if not 0 <= number <= maximum:
                continue
            when = _nanos_to_utc(point.get("endTimeNanos") or point.get("startTimeNanos"))
            key = when.date().isoformat()
            bucket = grouped.setdefault(key, {"samples": {}, "modified": None})
            bucket["samples"][(iso_utc(when), number)] = {"time": iso_utc(when), "value": number, "unit": unit}
            modified = _millis_to_utc(point.get("modifiedTimeMillis"))
            if modified and (bucket["modified"] is None or modified > bucket["modified"]):
                bucket["modified"] = modified
        observations: list[HealthObservation] = []
        for local_date, bucket in grouped.items():
            samples = sorted(bucket["samples"].values(), key=lambda item: item["time"])
            if not samples:
                continue
            start = datetime.fromisoformat(samples[0]["time"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(samples[-1]["time"].replace("Z", "+00:00"))
            observations.append(
                _health_observation(
                    metric=metric,
                    record_kind="series",
                    payload={"samples": samples},
                    imported_at=imported_at,
                    source_package=source_package,
                    recording_method=recording_method,
                    attribution=attribution,
                    start_at=start,
                    end_at=end,
                    upstream_last_modified_at=bucket["modified"],
                    local_date=local_date,
                    source_identity=name,
                )
            )
        return observations

    if metric == "sleep.session":
        stages: list[tuple[datetime, datetime, str, datetime | None]] = []
        for point in points:
            values = _fit_scalars(point)
            if not values or not isinstance(values[0], int):
                continue
            start = _nanos_to_utc(point["startTimeNanos"])
            end = _nanos_to_utc(point["endTimeNanos"])
            if end <= start:
                continue
            stages.append((start, end, GOOGLE_FIT_SLEEP_STAGES.get(values[0], "unknown"), _millis_to_utc(point.get("modifiedTimeMillis"))))
        stages.sort(key=lambda item: (item[0], item[1]))
        sessions: list[list[tuple[datetime, datetime, str, datetime | None]]] = []
        for stage in stages:
            if not sessions or stage[0] - max(item[1] for item in sessions[-1]) > timedelta(hours=2):
                sessions.append([stage])
            else:
                sessions[-1].append(stage)
        observations = []
        for session in sessions:
            start = min(item[0] for item in session)
            end = max(item[1] for item in session)
            stage_payload = [
                {"start_at": iso_utc(item[0]), "end_at": iso_utc(item[1]), "stage": item[2]}
                for item in session
            ]
            modified_values = [item[3] for item in session if item[3] is not None]
            observations.append(
                _health_observation(
                    metric=metric,
                    record_kind="session",
                    payload={"title": "Sleep", "stages": stage_payload},
                    imported_at=imported_at,
                    source_package=source_package,
                    recording_method=recording_method,
                    attribution=attribution,
                    start_at=start,
                    end_at=end,
                    upstream_last_modified_at=max(modified_values) if modified_values else None,
                    local_date=end.date().isoformat(),
                    source_identity=name,
                )
            )
        return observations

    observations = []
    for index, point in enumerate(points):
        values = _fit_scalars(point)
        if not values:
            continue
        when = _nanos_to_utc(point.get("endTimeNanos") or point.get("startTimeNanos"))
        record_kind = "point"
        if metric == "vitals.oxygen_saturation" and isinstance(values[0], (int, float)):
            value = float(values[0])
            if not 0 <= value <= 100:
                continue
            value_payload = {"value": value, "unit": "%"}
        elif metric == "vitals.blood_pressure" and len(values) >= 2 and all(isinstance(item, (int, float)) for item in values[:2]):
            record_kind = "composite"
            value_payload = {
                "systolic": float(values[0]),
                "diastolic": float(values[1]),
                "unit": "mmHg",
                "body_position": f"google_fit_code_{values[2]}" if len(values) > 2 else None,
                "measurement_site": f"google_fit_code_{values[3]}" if len(values) > 3 else None,
            }
        elif metric == "body.weight" and isinstance(values[0], (int, float)):
            value_payload = {"value": float(values[0]), "unit": "kg"}
        elif metric == "body.body_fat" and isinstance(values[0], (int, float)):
            value_payload = {"value": float(values[0]), "unit": "%"}
        elif metric == "body.height" and isinstance(values[0], (int, float)):
            value_payload = {"value": float(values[0]) * 100, "unit": "cm"}
        else:
            continue
        observations.append(
            _health_observation(
                metric=metric,
                record_kind=record_kind,
                payload=value_payload,
                imported_at=imported_at,
                source_package=source_package,
                recording_method=recording_method,
                attribution=attribution,
                measured_at=when,
                upstream_last_modified_at=_millis_to_utc(point.get("modifiedTimeMillis")),
                local_date=when.date().isoformat(),
                source_identity=f"{name}|{index}",
            )
        )
    return observations


def _daily_timestamp(day: str, value: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{value.strip()}").astimezone(timezone.utc)


def _parse_daily_csv(name: str, raw: bytes, imported_at: datetime) -> list[HealthObservation]:
    day_match = re.search(r"/(\d{4}-\d{2}-\d{2})\.csv$", name)
    if not day_match:
        return []
    day = day_match.group(1)
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="replace"))))
    if not rows:
        return []
    first_time = next((row.get("Start time") for row in rows if row.get("Start time")), None)
    last_time = next((row.get("End time") for row in reversed(rows) if row.get("End time")), None)
    if not first_time or not last_time:
        return []
    start = _daily_timestamp(day, first_time)
    end = _daily_timestamp(day, last_time)
    if end <= start:
        end += timedelta(days=1)
    observations = []
    for column, (metric, unit) in GOOGLE_FIT_DAILY_COLUMNS.items():
        numbers: list[float] = []
        for row in rows:
            value = (row.get(column) or "").strip()
            if not value:
                continue
            try:
                numbers.append(float(value))
            except ValueError:
                continue
        if not numbers:
            continue
        total = sum(numbers)
        if metric == "activity.steps":
            total = int(round(total))
        observations.append(
            _health_observation(
                metric=metric,
                record_kind="aggregate",
                payload={"value": total, "unit": unit, "aggregation": "google_fit_daily_summary"},
                imported_at=imported_at,
                source_package="com.google.android.apps.fitness",
                recording_method="derived",
                attribution={"state": "unknown", "evidence": "Google Fit daily merged summary"},
                start_at=start,
                end_at=end,
                local_date=day,
                source_identity=name,
            )
        )
    return observations


def _parse_activity_tcx(name: str, raw: bytes, imported_at: datetime) -> HealthObservation | None:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None
    activity = root.find(".//{*}Activity")
    if activity is None:
        return None
    identifier = activity.findtext("{*}Id")
    laps = activity.findall("{*}Lap")
    if not identifier or not laps:
        return None
    try:
        start = datetime.fromisoformat(identifier.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
    seconds = sum(float(lap.findtext("{*}TotalTimeSeconds") or 0) for lap in laps)
    if seconds <= 0:
        return None
    end = start + timedelta(seconds=seconds)
    exercise_type = activity.attrib.get("Sport") or Path(name).stem.rsplit("_", 1)[-1]
    return _health_observation(
        metric="activity.exercise_session",
        record_kind="session",
        payload={"exercise_type": exercise_type, "title": exercise_type},
        imported_at=imported_at,
        source_package="com.google.android.apps.fitness",
        recording_method="derived",
        attribution={"state": "unknown", "evidence": "Google Fit activity export"},
        start_at=start,
        end_at=end,
        local_date=start.date().isoformat(),
        source_identity=name,
    )


def _parse_derived_point_file(
    name: str,
    payload: dict[str, Any],
    imported_at: datetime,
    metric: str,
    unit: str,
) -> list[HealthObservation]:
    result = []
    for index, point in enumerate(payload.get("Data Points", [])):
        values = _fit_scalars(point)
        if not values or not isinstance(values[0], (int, float)):
            continue
        when = _nanos_to_utc(point.get("endTimeNanos") or point.get("startTimeNanos"))
        result.append(
            _health_observation(
                metric=metric,
                record_kind="point",
                payload={"value": float(values[0]), "unit": unit},
                imported_at=imported_at,
                source_package="com.google.android.gms",
                recording_method="derived",
                attribution={"state": "unknown", "evidence": "Google Fit derived metric"},
                measured_at=when,
                upstream_last_modified_at=_millis_to_utc(point.get("modifiedTimeMillis")),
                local_date=when.date().isoformat(),
                source_identity=f"{name}|{index}",
            )
        )
    return result


def _record_preview(preview: HealthImportPreview, records: Iterable[HealthObservation]) -> None:
    for record in records:
        preview.metric_counts[record.metric] = preview.metric_counts.get(record.metric, 0) + 1
        samples = record.payload.get("samples")
        stages = record.payload.get("stages")
        point_count = len(samples) if isinstance(samples, list) else len(stages) if isinstance(stages, list) else 1
        preview.data_point_counts[record.metric] = preview.data_point_counts.get(record.metric, 0) + point_count
        preview.records_parsed += 1
        event_at = record.measured_at or record.end_at or record.start_at
        if event_at is None:
            continue
        timestamp = iso_utc(event_at)
        if preview.first_measured_at is None or timestamp < preview.first_measured_at:
            preview.first_measured_at = timestamp
        if preview.last_measured_at is None or timestamp > preview.last_measured_at:
            preview.last_measured_at = timestamp


def parse_google_fit_takeout(
    path: Path,
    *,
    imported_at: datetime | None = None,
) -> tuple[list[HealthObservation], HealthImportPreview]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    imported = imported_at or utc_now()
    preview = HealthImportPreview(source="google_fit_takeout", path=str(path))
    observations: list[HealthObservation] = []
    with _ArchiveReader(path) as reader:
        names = reader.names()
        preview.files = len(names)
        for name in names:
            lowered = name.lower()
            parsed: list[HealthObservation] = []
            try:
                if "/fit/all data/raw_" in lowered and lowered.endswith(".json"):
                    data = json.loads(reader.read(name))
                    parsed = _parse_raw_points(name, data, imported)
                    preview.source_files["raw_json"] = preview.source_files.get("raw_json", 0) + 1
                elif "/fit/daily activity metrics/" in lowered and lowered.endswith(".csv"):
                    parsed = _parse_daily_csv(name, reader.read(name), imported)
                    preview.source_files["daily_csv"] = preview.source_files.get("daily_csv", 0) + 1
                elif "/fit/activities/" in lowered and lowered.endswith(".tcx"):
                    activity = _parse_activity_tcx(name, reader.read(name), imported)
                    parsed = [activity] if activity else []
                    preview.source_files["activity_tcx"] = preview.source_files.get("activity_tcx", 0) + 1
                elif "derived_com.google.heart_rate.bpm_com.google.android.gms_resting_heart_rate" in lowered:
                    data = json.loads(reader.read(name))
                    parsed = _parse_derived_point_file(name, data, imported, "vitals.resting_heart_rate", "bpm")
                    preview.source_files["derived_json"] = preview.source_files.get("derived_json", 0) + 1
                elif "derived_com.google.calories.bmr_com.google.android.gms_merged" in lowered:
                    data = json.loads(reader.read(name))
                    parsed = _parse_derived_point_file(name, data, imported, "body.basal_metabolic_rate", "kcal/day")
                    preview.source_files["derived_json"] = preview.source_files.get("derived_json", 0) + 1
                else:
                    preview.skipped_files += 1
                    continue
                preview.rows_seen += len(parsed)
                observations.extend(parsed)
                _record_preview(preview, parsed)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError, OverflowError) as exc:
                preview.records_rejected += 1
                if len(preview.warnings) < 8:
                    preview.warnings.append(f"{name}: {exc}")
    observations.sort(key=lambda item: item.event_at)
    if not observations:
        preview.warnings.append("No supported Google Fit health records were found.")
    preview.warnings.append(
        "Location and opaque sensor-event streams are intentionally not imported; they are not physiological dashboard measurements."
    )
    return observations, preview
