from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import shutil
import sqlite3
from typing import Any, Iterable

from .models import GlucoseReading, HealthObservation, Provenance, iso_utc, stable_hash, utc_now


SCHEMA = """
CREATE TABLE IF NOT EXISTS glucose_readings (
    id TEXT PRIMARY KEY,
    adapter TEXT NOT NULL,
    source_patient_hash TEXT,
    value_mg_dl INTEGER NOT NULL,
    trend TEXT,
    trend_raw TEXT,
    sample_type TEXT NOT NULL,
    measured_at_utc TEXT NOT NULL,
    received_at_utc TEXT NOT NULL,
    stored_at_utc TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    raw_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_glucose_measured ON glucose_readings(measured_at_utc DESC);

CREATE TABLE IF NOT EXISTS collector_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    adapter TEXT NOT NULL,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT NOT NULL,
    status TEXT NOT NULL,
    readings_seen INTEGER NOT NULL,
    readings_inserted INTEGER NOT NULL,
    error_code TEXT,
    error_message TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_collector_runs_started ON collector_runs(started_at_utc DESC);

CREATE TABLE IF NOT EXISTS access_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    accessed_at_utc TEXT NOT NULL,
    host_id_hash TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_access_audit_host_time ON access_audit(host_id_hash, accessed_at_utc DESC);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at_utc TEXT NOT NULL
);
"""


WATCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS health_observations (
    id TEXT PRIMARY KEY,
    metric TEXT NOT NULL,
    category TEXT NOT NULL,
    record_kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    measured_at_utc TEXT,
    start_at_utc TEXT,
    end_at_utc TEXT,
    observed_by_companion_at_utc TEXT NOT NULL,
    ingested_at_server_utc TEXT NOT NULL,
    upstream_last_modified_at_utc TEXT,
    source_package TEXT NOT NULL,
    recording_method TEXT NOT NULL,
    attribution_json TEXT NOT NULL,
    installation_hash TEXT NOT NULL,
    source_record_hash TEXT NOT NULL,
    zone_offset TEXT,
    start_zone_offset TEXT,
    end_zone_offset TEXT,
    schema_version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_health_metric_time ON health_observations(metric, COALESCE(measured_at_utc, end_at_utc, start_at_utc) DESC);
CREATE INDEX IF NOT EXISTS idx_health_ingested ON health_observations(ingested_at_server_utc DESC);

CREATE TABLE IF NOT EXISTS health_tombstones (
    observation_id TEXT PRIMARY KEY,
    metric TEXT NOT NULL,
    source_record_hash TEXT NOT NULL,
    upstream_last_modified_at_utc TEXT,
    deleted_at_utc TEXT NOT NULL,
    expires_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS device_sync_runs (
    batch_id TEXT PRIMARY KEY,
    body_sha256 TEXT NOT NULL,
    device_hash TEXT NOT NULL,
    installation_hash TEXT NOT NULL,
    generated_at_utc TEXT NOT NULL,
    received_at_utc TEXT NOT NULL,
    status TEXT NOT NULL,
    changes_seen INTEGER NOT NULL,
    observations_inserted INTEGER NOT NULL,
    observations_updated INTEGER NOT NULL,
    observations_deleted INTEGER NOT NULL,
    observations_ignored INTEGER NOT NULL,
    availability_count INTEGER NOT NULL,
    error_code TEXT
);
CREATE INDEX IF NOT EXISTS idx_device_sync_received ON device_sync_runs(received_at_utc DESC);

CREATE TABLE IF NOT EXISTS live_heart_demand (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    revision INTEGER NOT NULL,
    requested_until_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);
INSERT OR IGNORE INTO live_heart_demand (
    singleton, revision, requested_until_utc, updated_at_utc
) VALUES (1, 0, '1970-01-01T00:00:00Z', '1970-01-01T00:00:00Z');

CREATE TABLE IF NOT EXISTS watch_availability (
    installation_hash TEXT NOT NULL,
    metric TEXT NOT NULL,
    state TEXT NOT NULL,
    evidence TEXT NOT NULL,
    checked_at_utc TEXT NOT NULL,
    coverage_json TEXT NOT NULL,
    PRIMARY KEY (installation_hash, metric)
);

CREATE TABLE IF NOT EXISTS watch_replay_nonces (
    device_hash TEXT NOT NULL,
    nonce_hash TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    seen_at_utc TEXT NOT NULL,
    PRIMARY KEY (device_hash, nonce_hash)
);
CREATE INDEX IF NOT EXISTS idx_watch_nonce_time ON watch_replay_nonces(seen_at_utc);
"""


WATCH_SCHEMA_V3 = """
CREATE TABLE IF NOT EXISTS health_association_members (
    identity_namespace_id TEXT NOT NULL,
    adapter_id TEXT NOT NULL,
    association_hash TEXT NOT NULL,
    metric TEXT NOT NULL,
    source_record_hash TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    PRIMARY KEY (identity_namespace_id, adapter_id, association_hash, metric, source_record_hash)
);
CREATE INDEX IF NOT EXISTS idx_health_association_observation ON health_association_members(observation_id);

CREATE TABLE IF NOT EXISTS health_associations (
    identity_namespace_id TEXT NOT NULL,
    adapter_id TEXT NOT NULL,
    association_hash TEXT NOT NULL,
    changed_at_utc TEXT NOT NULL,
    PRIMARY KEY (identity_namespace_id, adapter_id, association_hash)
);

CREATE TABLE IF NOT EXISTS watch_active_installations (
    device_hash TEXT PRIMARY KEY,
    installation_hash TEXT NOT NULL,
    identity_namespace_id TEXT,
    activated_at_utc TEXT NOT NULL
);
"""


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


class StateStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self) -> None:
        existed = self.path.exists() and self.path.stat().st_size > 0
        conn = sqlite3.connect(self.path)
        try:
            conn.executescript(SCHEMA)
            applied = conn.execute("SELECT 1 FROM schema_migrations WHERE version = 2").fetchone()
            if not applied:
                conn.commit()
                if existed:
                    backup = self.path.with_suffix(self.path.suffix + ".pre-watch-v2.bak")
                    if not backup.exists():
                        shutil.copy2(self.path, backup)
                conn.executescript(WATCH_SCHEMA)
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at_utc) VALUES (?, ?)",
                    (2, iso_utc(utc_now())),
                )
            else:
                conn.executescript(WATCH_SCHEMA)
            applied_v3 = conn.execute("SELECT 1 FROM schema_migrations WHERE version = 3").fetchone()
            if not applied_v3:
                conn.commit()
                if existed:
                    backup = self.path.with_suffix(self.path.suffix + ".pre-samsung-v3.bak")
                    if not backup.exists():
                        shutil.copy2(self.path, backup)
                observation_columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(health_observations)").fetchall()
                }
                for name, declaration in (
                    ("adapter_id", "TEXT NOT NULL DEFAULT 'android_health_connect'"),
                    ("adapter_version", "TEXT NOT NULL DEFAULT 'legacy-v1'"),
                    ("identity_namespace_id", "TEXT NOT NULL DEFAULT 'legacy-v1'"),
                    ("association_hash", "TEXT"),
                    ("local_date", "TEXT"),
                ):
                    if name not in observation_columns:
                        conn.execute(f"ALTER TABLE health_observations ADD COLUMN {name} {declaration}")
                conn.execute(
                    "UPDATE health_observations SET adapter_id='wear_health_services' "
                    "WHERE source_package='ai.clinicianassist.personalstate'"
                )
                conn.executescript(
                    """
                    CREATE TABLE health_tombstones_v3 (
                        identity_namespace_id TEXT NOT NULL,
                        adapter_id TEXT NOT NULL,
                        observation_id TEXT NOT NULL,
                        metric TEXT NOT NULL,
                        source_record_hash TEXT NOT NULL,
                        upstream_last_modified_at_utc TEXT,
                        deleted_at_utc TEXT NOT NULL,
                        expires_at_utc TEXT NOT NULL,
                        PRIMARY KEY (identity_namespace_id, adapter_id, observation_id)
                    );
                    INSERT INTO health_tombstones_v3 (
                        identity_namespace_id, adapter_id, observation_id, metric,
                        source_record_hash, upstream_last_modified_at_utc, deleted_at_utc, expires_at_utc
                    )
                    SELECT 'legacy-v1', 'android_health_connect', observation_id, metric,
                           source_record_hash, upstream_last_modified_at_utc, deleted_at_utc, expires_at_utc
                    FROM health_tombstones;
                    DROP TABLE health_tombstones;
                    ALTER TABLE health_tombstones_v3 RENAME TO health_tombstones;
                    """
                )
                sync_columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(device_sync_runs)").fetchall()
                }
                for name, declaration in (
                    ("adapter_id", "TEXT NOT NULL DEFAULT 'android_health_connect'"),
                    ("adapter_version", "TEXT NOT NULL DEFAULT 'legacy-v1'"),
                    ("identity_namespace_id", "TEXT NOT NULL DEFAULT 'legacy-v1'"),
                ):
                    if name not in sync_columns:
                        conn.execute(f"ALTER TABLE device_sync_runs ADD COLUMN {name} {declaration}")
                conn.executescript(
                    """
                    CREATE TABLE watch_availability_v3 (
                        installation_hash TEXT NOT NULL,
                        adapter_id TEXT NOT NULL,
                        adapter_version TEXT NOT NULL,
                        identity_namespace_id TEXT NOT NULL,
                        metric TEXT NOT NULL,
                        state TEXT NOT NULL,
                        evidence TEXT NOT NULL,
                        checked_at_utc TEXT NOT NULL,
                        coverage_json TEXT NOT NULL,
                        PRIMARY KEY (installation_hash, adapter_id, metric)
                    );
                    INSERT INTO watch_availability_v3 (
                        installation_hash, adapter_id, adapter_version, identity_namespace_id,
                        metric, state, evidence, checked_at_utc, coverage_json
                    )
                    SELECT installation_hash, 'android_health_connect', 'legacy-v1', 'legacy-v1',
                           metric, state, evidence, checked_at_utc, coverage_json
                    FROM watch_availability;
                    DROP TABLE watch_availability;
                    ALTER TABLE watch_availability_v3 RENAME TO watch_availability;
                    """
                )
                conn.executescript(WATCH_SCHEMA_V3)
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at_utc) VALUES (?, ?)",
                    (3, iso_utc(utc_now())),
                )
            else:
                conn.executescript(WATCH_SCHEMA_V3)
            active_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(watch_active_installations)").fetchall()
            }
            if "identity_namespace_id" not in active_columns:
                conn.execute("ALTER TABLE watch_active_installations ADD COLUMN identity_namespace_id TEXT")
            conn.commit()
        finally:
            conn.close()

    def backup_to(self, destination: Path) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = sqlite3.connect(self.path)
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        return destination

    def request_live_heart(self, now: datetime, lease_seconds: int = 20) -> dict[str, Any]:
        requested_until = now + timedelta(seconds=max(10, min(lease_seconds, 60)))
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE live_heart_demand
                SET revision = revision + 1, requested_until_utc = ?, updated_at_utc = ?
                WHERE singleton = 1
                """,
                (iso_utc(requested_until), iso_utc(now)),
            )
            row = conn.execute(
                "SELECT revision, requested_until_utc, updated_at_utc FROM live_heart_demand WHERE singleton = 1"
            ).fetchone()
        return dict(row)

    def live_heart_demand(self) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT revision, requested_until_utc, updated_at_utc FROM live_heart_demand WHERE singleton = 1"
            ).fetchone()
        return dict(row)

    def upsert_glucose_readings(self, readings: Iterable[GlucoseReading]) -> int:
        inserted = 0
        with self.connect() as conn:
            for reading in readings:
                before = conn.total_changes
                conn.execute(
                    """
                    INSERT OR IGNORE INTO glucose_readings (
                        id, adapter, source_patient_hash, value_mg_dl, trend, trend_raw,
                        sample_type, measured_at_utc, received_at_utc, stored_at_utc,
                        provenance_json, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    reading.persistence_tuple(),
                )
                if conn.total_changes > before:
                    inserted += 1
        return inserted

    def import_glucose_readings(self, readings: Iterable[GlucoseReading]) -> dict[str, int]:
        """Import historical readings while deduplicating across adapter boundaries."""
        inserted = 0
        duplicates = 0
        with self.connect() as conn:
            for reading in readings:
                values = reading.persistence_tuple()
                existing = conn.execute(
                    """
                    SELECT 1 FROM glucose_readings
                    WHERE measured_at_utc = ? AND value_mg_dl = ?
                    LIMIT 1
                    """,
                    (iso_utc(reading.measured_at), reading.value_mg_dl),
                ).fetchone()
                if existing:
                    duplicates += 1
                    continue
                before = conn.total_changes
                conn.execute(
                    """
                    INSERT OR IGNORE INTO glucose_readings (
                        id, adapter, source_patient_hash, value_mg_dl, trend, trend_raw,
                        sample_type, measured_at_utc, received_at_utc, stored_at_utc,
                        provenance_json, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                if conn.total_changes > before:
                    inserted += 1
                else:
                    duplicates += 1
        return {"inserted": inserted, "duplicates": duplicates}

    def import_health_observations(self, observations: Iterable[HealthObservation]) -> dict[str, int]:
        """Import historical health observations with adapter-independent exact deduplication."""
        inserted = 0
        duplicates = 0
        with self.connect() as conn:
            for observation in observations:
                values = observation.persistence_tuple()
                payload_json = values[4]
                existing = conn.execute(
                    """
                    SELECT 1 FROM health_observations
                    WHERE metric = ?
                      AND IFNULL(measured_at_utc, '') = IFNULL(?, '')
                      AND IFNULL(start_at_utc, '') = IFNULL(?, '')
                      AND IFNULL(end_at_utc, '') = IFNULL(?, '')
                      AND payload_json = ?
                    LIMIT 1
                    """,
                    (observation.metric, values[5], values[6], values[7], payload_json),
                ).fetchone()
                if existing:
                    duplicates += 1
                    continue
                before = conn.total_changes
                conn.execute(
                    """
                    INSERT OR IGNORE INTO health_observations (
                        id, metric, category, record_kind, payload_json, measured_at_utc, start_at_utc,
                        end_at_utc, observed_by_companion_at_utc, ingested_at_server_utc,
                        upstream_last_modified_at_utc, source_package, recording_method, attribution_json,
                        installation_hash, source_record_hash, zone_offset, start_zone_offset, end_zone_offset,
                        schema_version, adapter_id, adapter_version, identity_namespace_id,
                        association_hash, local_date
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                if conn.total_changes > before:
                    inserted += 1
                else:
                    duplicates += 1
        return {"inserted": inserted, "duplicates": duplicates}

    def latest_glucose(self) -> GlucoseReading | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM glucose_readings ORDER BY measured_at_utc DESC, received_at_utc DESC LIMIT 1"
            ).fetchone()
        return self._row_to_reading(row) if row else None

    def recent_glucose(self, hours: float = 3, limit: int = 96) -> list[GlucoseReading]:
        since = utc_now() - timedelta(hours=hours)
        return self.glucose_history(since=since, limit=limit)

    def glucose_history(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
        descending: bool = False,
    ) -> list[GlucoseReading]:
        clauses: list[str] = []
        params: list[Any] = []
        if since is not None:
            clauses.append("measured_at_utc >= ?")
            params.append(iso_utc(since))
        if until is not None:
            clauses.append("measured_at_utc <= ?")
            params.append(iso_utc(until))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        direction = "DESC" if descending else "ASC"
        limit_sql = ""
        if limit is not None:
            limit_sql = " LIMIT ?"
            params.append(max(1, int(limit)))
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM glucose_readings
                {where}
                ORDER BY measured_at_utc {direction}, received_at_utc {direction}
                {limit_sql}
                """,
                params,
            ).fetchall()
        return [self._row_to_reading(row) for row in rows]

    def history_overview(self) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS count,
                    MIN(measured_at_utc) AS first_measured_at,
                    MAX(measured_at_utc) AS last_measured_at
                FROM glucose_readings
                """
            ).fetchone()
        return {
            "count": int(row["count"]),
            "first_measured_at": row["first_measured_at"],
            "last_measured_at": row["last_measured_at"],
        }

    def record_collector_run(
        self,
        adapter: str,
        started_at: datetime,
        finished_at: datetime,
        status: str,
        readings_seen: int,
        readings_inserted: int,
        error_code: str | None = None,
        error_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO collector_runs (
                    adapter, started_at_utc, finished_at_utc, status, readings_seen,
                    readings_inserted, error_code, error_message, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    adapter,
                    iso_utc(started_at),
                    iso_utc(finished_at),
                    status,
                    readings_seen,
                    readings_inserted,
                    error_code,
                    error_message,
                    json.dumps(metadata or {}, sort_keys=True),
                ),
            )

    def last_collector_run(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM collector_runs ORDER BY started_at_utc DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def collector_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM collector_runs ORDER BY started_at_utc DESC LIMIT ?",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_access(self, host_id: str, tool_name: str, decision: str, reason: str, now: datetime | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO access_audit (accessed_at_utc, host_id_hash, tool_name, decision, reason)
                VALUES (?, ?, ?, ?, ?)
                """,
                (iso_utc(now or utc_now()), stable_hash(host_id), tool_name, decision, reason),
            )

    def count_allowed_accesses_since(self, host_id: str, since: datetime) -> int:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count FROM access_audit
                WHERE host_id_hash = ? AND accessed_at_utc >= ? AND decision = 'allowed'
                """,
                (stable_hash(host_id), iso_utc(since)),
            ).fetchone()
        return int(row["count"])

    def export_history_jsonl(self, output_path: Path) -> int:
        readings = self.recent_glucose(hours=24 * 365 * 100, limit=10_000_000)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with output_path.open("w", encoding="utf-8") as handle:
            for reading in readings:
                handle.write(json.dumps(reading.public_dict(), sort_keys=True) + "\n")
                count += 1
        return count

    def delete_history(self, before: datetime | None = None) -> int:
        with self.connect() as conn:
            if before is None:
                before_count = conn.execute("SELECT COUNT(*) AS count FROM glucose_readings").fetchone()["count"]
                conn.execute("DELETE FROM glucose_readings")
                return int(before_count)
            before_count = conn.execute(
                "SELECT COUNT(*) AS count FROM glucose_readings WHERE measured_at_utc < ?",
                (iso_utc(before),),
            ).fetchone()["count"]
            conn.execute("DELETE FROM glucose_readings WHERE measured_at_utc < ?", (iso_utc(before),))
            return int(before_count)

    def prune_retention(self, retention_days: int, now: datetime | None = None) -> int:
        cutoff = (now or utc_now()) - timedelta(days=retention_days)
        return self.delete_history(before=cutoff)

    def watch_batch(self, batch_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM device_sync_runs WHERE batch_id = ?", (batch_id,)).fetchone()
        return dict(row) if row else None

    def claim_watch_nonce(self, device_hash: str, nonce_hash: str, batch_id: str, now: datetime) -> bool:
        cutoff = now - timedelta(hours=24)
        with self.connect() as conn:
            conn.execute("DELETE FROM watch_replay_nonces WHERE seen_at_utc < ?", (iso_utc(cutoff),))
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM watch_replay_nonces WHERE device_hash = ?",
                (device_hash,),
            ).fetchone()["count"]
            if int(count) >= 20_000:
                return False
            try:
                conn.execute(
                    "INSERT INTO watch_replay_nonces (device_hash, nonce_hash, batch_id, seen_at_utc) VALUES (?, ?, ?, ?)",
                    (device_hash, nonce_hash, batch_id, iso_utc(now)),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def watch_requests_since(self, device_hash: str, since: datetime) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM watch_replay_nonces WHERE device_hash = ? AND seen_at_utc >= ?",
                (device_hash, iso_utc(since)),
            ).fetchone()
        return int(row["count"])

    def apply_watch_batch(
        self,
        *,
        batch: dict[str, Any],
        body_sha256: str,
        device_hash: str,
        installation_hash: str,
        observations: list[HealthObservation],
        deletions: list[dict[str, Any]],
        association_reconciliations: list[dict[str, Any]] | None = None,
        now: datetime,
    ) -> dict[str, Any]:
        counts = {"inserted": 0, "updated": 0, "deleted": 0, "ignored": 0}
        with self.connect() as conn:
            prior = conn.execute("SELECT * FROM device_sync_runs WHERE batch_id = ?", (batch["batch_id"],)).fetchone()
            if prior:
                if prior["body_sha256"] != body_sha256:
                    raise ValueError("batch_id_conflict")
                return {
                    "status": "already_committed",
                    "inserted": int(prior["observations_inserted"]),
                    "updated": int(prior["observations_updated"]),
                    "deleted": int(prior["observations_deleted"]),
                    "ignored": int(prior["observations_ignored"]),
                }

            incoming_namespace = batch.get("identity_namespace_id")
            active_installation = conn.execute(
                "SELECT identity_namespace_id FROM watch_active_installations WHERE device_hash=?",
                (device_hash,),
            ).fetchone()
            if (
                incoming_namespace
                and active_installation
                and active_installation["identity_namespace_id"]
                and active_installation["identity_namespace_id"] != incoming_namespace
            ):
                raise ValueError("identity_namespace_mismatch")

            conn.execute("DELETE FROM health_tombstones WHERE expires_at_utc < ?", (iso_utc(now),))
            for observation in observations:
                tombstone = conn.execute(
                    """
                    SELECT upstream_last_modified_at_utc FROM health_tombstones
                    WHERE identity_namespace_id=? AND adapter_id=? AND observation_id=?
                    """,
                    (observation.identity_namespace_id, observation.adapter_id, observation.id),
                ).fetchone()
                incoming_version = iso_utc(observation.upstream_last_modified_at or observation.observed_by_companion_at)
                if tombstone and (tombstone["upstream_last_modified_at_utc"] or "") >= (incoming_version or ""):
                    counts["ignored"] += 1
                    continue
                existing = conn.execute(
                    "SELECT upstream_last_modified_at_utc, observed_by_companion_at_utc FROM health_observations WHERE id = ?",
                    (observation.id,),
                ).fetchone()
                if existing:
                    existing_version = existing["upstream_last_modified_at_utc"] or existing["observed_by_companion_at_utc"]
                    if existing_version > (incoming_version or ""):
                        counts["ignored"] += 1
                        continue
                    conn.execute(
                        """
                        UPDATE health_observations SET
                            metric=?, category=?, record_kind=?, payload_json=?, measured_at_utc=?, start_at_utc=?,
                            end_at_utc=?, observed_by_companion_at_utc=?, ingested_at_server_utc=?,
                            upstream_last_modified_at_utc=?, source_package=?, recording_method=?, attribution_json=?,
                            installation_hash=?, source_record_hash=?, zone_offset=?, start_zone_offset=?,
                            end_zone_offset=?, schema_version=?, adapter_id=?, adapter_version=?,
                            identity_namespace_id=?, association_hash=?, local_date=?
                        WHERE id=?
                        """,
                        observation.persistence_tuple()[1:] + (observation.id,),
                    )
                    counts["updated"] += 1
                else:
                    conn.execute(
                        """
                        INSERT INTO health_observations (
                            id, metric, category, record_kind, payload_json, measured_at_utc, start_at_utc,
                            end_at_utc, observed_by_companion_at_utc, ingested_at_server_utc,
                            upstream_last_modified_at_utc, source_package, recording_method, attribution_json,
                            installation_hash, source_record_hash, zone_offset, start_zone_offset, end_zone_offset,
                            schema_version, adapter_id, adapter_version, identity_namespace_id,
                            association_hash, local_date
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        observation.persistence_tuple(),
                    )
                    counts["inserted"] += 1

            for deletion in deletions:
                existing = conn.execute(
                    "SELECT upstream_last_modified_at_utc, observed_by_companion_at_utc FROM health_observations WHERE id = ?",
                    (deletion["id"],),
                ).fetchone()
                delete_version = (
                    iso_utc(_parse_dt(deletion["upstream_last_modified_at"]))
                    if deletion["upstream_last_modified_at"]
                    else iso_utc(now)
                )
                if existing:
                    existing_version = existing["upstream_last_modified_at_utc"] or existing["observed_by_companion_at_utc"]
                    if existing_version > delete_version:
                        counts["ignored"] += 1
                        continue
                    conn.execute("DELETE FROM health_observations WHERE id = ?", (deletion["id"],))
                    counts["deleted"] += 1
                conn.execute(
                    """
                    INSERT INTO health_tombstones (
                        observation_id, metric, source_record_hash, upstream_last_modified_at_utc,
                        deleted_at_utc, expires_at_utc, adapter_id, identity_namespace_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(identity_namespace_id, adapter_id, observation_id) DO UPDATE SET
                        upstream_last_modified_at_utc=excluded.upstream_last_modified_at_utc,
                        deleted_at_utc=excluded.deleted_at_utc,
                        expires_at_utc=excluded.expires_at_utc
                    """,
                    (
                        deletion["id"], deletion["metric"], deletion["source_record_hash"], delete_version,
                        iso_utc(now), iso_utc(now + timedelta(days=30)),
                        deletion.get("adapter_id", "android_health_connect"),
                        deletion.get("identity_namespace_id", "legacy-v1"),
                    ),
                )

            for reconciliation in association_reconciliations or []:
                key = (
                    reconciliation["identity_namespace_id"],
                    reconciliation["adapter_id"],
                    reconciliation["association_hash"],
                )
                changed_at = iso_utc(_parse_dt(reconciliation["changed_at"]))
                prior_association = conn.execute(
                    """
                    SELECT changed_at_utc FROM health_associations
                    WHERE identity_namespace_id=? AND adapter_id=? AND association_hash=?
                    """,
                    key,
                ).fetchone()
                if prior_association and prior_association["changed_at_utc"] >= changed_at:
                    counts["ignored"] += 1
                    continue
                existing_members = conn.execute(
                    """
                    SELECT metric, source_record_hash, observation_id
                    FROM health_association_members
                    WHERE identity_namespace_id=? AND adapter_id=? AND association_hash=?
                    """,
                    key,
                ).fetchall()
                current = {
                    (item["metric"], item["source_record_hash"]): item["observation_id"]
                    for item in reconciliation["members"]
                }
                for member in existing_members:
                    member_key = (member["metric"], member["source_record_hash"])
                    if member_key in current:
                        continue
                    if conn.execute("SELECT 1 FROM health_observations WHERE id=?", (member["observation_id"],)).fetchone():
                        conn.execute("DELETE FROM health_observations WHERE id=?", (member["observation_id"],))
                        counts["deleted"] += 1
                    conn.execute(
                        """
                        INSERT INTO health_tombstones (
                            observation_id, metric, source_record_hash, upstream_last_modified_at_utc,
                            deleted_at_utc, expires_at_utc, adapter_id, identity_namespace_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(identity_namespace_id, adapter_id, observation_id) DO UPDATE SET
                            upstream_last_modified_at_utc=excluded.upstream_last_modified_at_utc,
                            deleted_at_utc=excluded.deleted_at_utc,
                            expires_at_utc=excluded.expires_at_utc
                        """,
                        (
                            member["observation_id"], member["metric"], member["source_record_hash"],
                            changed_at, iso_utc(now), iso_utc(now + timedelta(days=30)),
                            reconciliation["adapter_id"], reconciliation["identity_namespace_id"],
                        ),
                    )
                conn.execute(
                    "DELETE FROM health_association_members WHERE identity_namespace_id=? AND adapter_id=? AND association_hash=?",
                    key,
                )
                for item in reconciliation["members"]:
                    if not conn.execute("SELECT 1 FROM health_observations WHERE id=?", (item["observation_id"],)).fetchone():
                        raise ValueError("association_member_missing")
                    conn.execute(
                        """
                        INSERT INTO health_association_members (
                            identity_namespace_id, adapter_id, association_hash, metric,
                            source_record_hash, observation_id
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        key + (item["metric"], item["source_record_hash"], item["observation_id"]),
                    )
                conn.execute(
                    """
                    INSERT INTO health_associations (
                        identity_namespace_id, adapter_id, association_hash, changed_at_utc
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(identity_namespace_id, adapter_id, association_hash) DO UPDATE SET
                        changed_at_utc=excluded.changed_at_utc
                    """,
                    key + (changed_at,),
                )

            batch_adapter = batch.get("adapter") or {"id": "android_health_connect", "version": "legacy-v1"}
            identity_namespace_id = batch.get("identity_namespace_id", "legacy-v1")
            for item in batch["availability"]:
                item_adapter = item.get("adapter", batch_adapter)
                conn.execute(
                    """
                    INSERT INTO watch_availability (
                        installation_hash, adapter_id, adapter_version, identity_namespace_id,
                        metric, state, evidence, checked_at_utc, coverage_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(installation_hash, adapter_id, metric) DO UPDATE SET
                        adapter_version=excluded.adapter_version,
                        identity_namespace_id=excluded.identity_namespace_id,
                        state=excluded.state,
                        evidence=excluded.evidence,
                        checked_at_utc=excluded.checked_at_utc,
                        coverage_json=excluded.coverage_json
                    WHERE excluded.checked_at_utc >= watch_availability.checked_at_utc
                    """,
                    (
                        installation_hash,
                        item_adapter["id"],
                        item_adapter["version"],
                        identity_namespace_id,
                        item["metric"],
                        item["state"],
                        item["evidence"],
                        item["checked_at"],
                        json.dumps(item["coverage"], separators=(",", ":"), sort_keys=True),
                    ),
                )

            conn.execute(
                """
                INSERT INTO watch_active_installations (
                    device_hash, installation_hash, identity_namespace_id, activated_at_utc
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(device_hash) DO UPDATE SET
                    installation_hash=excluded.installation_hash,
                    identity_namespace_id=COALESCE(excluded.identity_namespace_id, watch_active_installations.identity_namespace_id),
                    activated_at_utc=excluded.activated_at_utc
                """,
                (device_hash, installation_hash, incoming_namespace, iso_utc(now)),
            )

            conn.execute(
                """
                INSERT INTO device_sync_runs (
                    batch_id, body_sha256, device_hash, installation_hash, generated_at_utc,
                    received_at_utc, status, changes_seen, observations_inserted,
                    observations_updated, observations_deleted, observations_ignored,
                    availability_count, error_code, adapter_id, adapter_version, identity_namespace_id
                ) VALUES (?, ?, ?, ?, ?, ?, 'ok', ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    batch["batch_id"], body_sha256, device_hash, installation_hash,
                    batch["generated_at"], iso_utc(now), len(batch["changes"]),
                    counts["inserted"], counts["updated"], counts["deleted"], counts["ignored"],
                    len(batch["availability"]),
                    batch_adapter["id"], batch_adapter["version"], identity_namespace_id,
                ),
            )
        return {"status": "ok", **counts}

    def watch_observations(
        self,
        *,
        metric: str | None = None,
        since: datetime | None = None,
        limit: int = 200,
        offset: int = 0,
        descending: bool = True,
    ) -> list[HealthObservation]:
        clauses: list[str] = []
        params: list[Any] = []
        if metric:
            clauses.append("metric = ?")
            params.append(metric)
        if since:
            clauses.append("COALESCE(measured_at_utc, end_at_utc, start_at_utc) >= ?")
            params.append(iso_utc(since))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        direction = "DESC" if descending else "ASC"
        params.extend([max(1, min(int(limit), 100_000)), max(0, int(offset))])
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM health_observations
                {where}
                ORDER BY COALESCE(measured_at_utc, end_at_utc, start_at_utc) {direction}, id {direction}
                LIMIT ? OFFSET ?
                """,
                params,
            ).fetchall()
        return [self._row_to_observation(row) for row in rows]

    def latest_watch_by_metric(
        self,
        metrics: Iterable[str] | None = None,
        *,
        adapter_ids: Iterable[str] | None = None,
    ) -> dict[str, HealthObservation]:
        allowed = set(metrics) if metrics is not None else None
        adapters = tuple(dict.fromkeys(adapter_ids or ()))
        where = ""
        params: list[Any] = []
        if adapters:
            placeholders = ", ".join("?" for _ in adapters)
            where = f"WHERE adapter_id IN ({placeholders})"
            params.extend(adapters)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                WITH ranked AS (
                    SELECT health_observations.*,
                           ROW_NUMBER() OVER (
                               PARTITION BY metric
                               ORDER BY COALESCE(measured_at_utc, end_at_utc, start_at_utc) DESC, id DESC
                           ) AS metric_rank
                    FROM health_observations
                    {where}
                )
                SELECT * FROM ranked WHERE metric_rank = 1
                ORDER BY COALESCE(measured_at_utc, end_at_utc, start_at_utc) DESC, id DESC
                """,
                params,
            ).fetchall()
        result: dict[str, HealthObservation] = {}
        for observation in rows:
            observation = self._row_to_observation(observation)
            if observation.metric in result or (allowed is not None and observation.metric not in allowed):
                continue
            result[observation.metric] = observation
        return result

    def latest_watch_sync(self) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM device_sync_runs ORDER BY received_at_utc DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def watch_availability(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM watch_availability ORDER BY metric, adapter_id").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["coverage"] = json.loads(item.pop("coverage_json"))
            result.append(item)
        return result

    def active_watch_installation_hashes(self) -> set[str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT installation_hash FROM watch_active_installations").fetchall()
        return {str(row["installation_hash"]) for row in rows}

    def watch_history_overview(self) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count,
                       MIN(COALESCE(measured_at_utc, end_at_utc, start_at_utc)) AS first_recorded_at,
                       MAX(COALESCE(measured_at_utc, end_at_utc, start_at_utc)) AS last_recorded_at
                FROM health_observations
                """
            ).fetchone()
        return {"count": int(row["count"]), "first_recorded_at": row["first_recorded_at"], "last_recorded_at": row["last_recorded_at"]}

    def delete_watch_history(self, before: datetime | None = None) -> int:
        with self.connect() as conn:
            if before is None:
                count = int(conn.execute("SELECT COUNT(*) AS count FROM health_observations").fetchone()["count"])
                conn.execute("DELETE FROM health_observations")
                conn.execute("DELETE FROM health_tombstones")
                conn.execute("DELETE FROM watch_availability")
                conn.execute("DELETE FROM device_sync_runs")
                conn.execute("DELETE FROM watch_replay_nonces")
                conn.execute("DELETE FROM health_association_members")
                conn.execute("DELETE FROM health_associations")
                conn.execute("DELETE FROM watch_active_installations")
                return count
            cutoff = iso_utc(before)
            count = int(conn.execute(
                "SELECT COUNT(*) AS count FROM health_observations WHERE COALESCE(measured_at_utc, end_at_utc, start_at_utc) < ?",
                (cutoff,),
            ).fetchone()["count"])
            conn.execute(
                "DELETE FROM health_observations WHERE COALESCE(measured_at_utc, end_at_utc, start_at_utc) < ?",
                (cutoff,),
            )
            return count

    def prune_watch_retention(self, retention_days: int, now: datetime | None = None) -> int:
        return self.delete_watch_history(before=(now or utc_now()) - timedelta(days=retention_days))

    def _row_to_reading(self, row: sqlite3.Row) -> GlucoseReading:
        provenance_data = json.loads(row["provenance_json"])
        provenance = Provenance(
            adapter=provenance_data["adapter"],
            vendor=provenance_data["vendor"],
            source=provenance_data["source"],
            source_patient_hash=provenance_data.get("source_patient_hash"),
            sensor_hash=provenance_data.get("sensor_hash"),
            measurement_timestamp_source=provenance_data.get("measurement_timestamp_source", "unknown"),
            received_timestamp_source=provenance_data.get("received_timestamp_source", "collector_clock"),
            selected_region_host=provenance_data.get("selected_region_host"),
        )
        return GlucoseReading(
            id=row["id"],
            adapter=row["adapter"],
            value_mg_dl=int(row["value_mg_dl"]),
            trend=row["trend"],
            trend_raw=row["trend_raw"],
            sample_type=row["sample_type"],
            measured_at=_parse_dt(row["measured_at_utc"]),
            received_at=_parse_dt(row["received_at_utc"]),
            stored_at=_parse_dt(row["stored_at_utc"]),
            provenance=provenance,
            raw=json.loads(row["raw_json"]),
        )

    def _row_to_observation(self, row: sqlite3.Row) -> HealthObservation:
        return HealthObservation(
            id=row["id"],
            metric=row["metric"],
            category=row["category"],
            record_kind=row["record_kind"],
            payload=json.loads(row["payload_json"]),
            measured_at=_parse_dt(row["measured_at_utc"]) if row["measured_at_utc"] else None,
            start_at=_parse_dt(row["start_at_utc"]) if row["start_at_utc"] else None,
            end_at=_parse_dt(row["end_at_utc"]) if row["end_at_utc"] else None,
            observed_by_companion_at=_parse_dt(row["observed_by_companion_at_utc"]),
            ingested_at_server=_parse_dt(row["ingested_at_server_utc"]),
            upstream_last_modified_at=_parse_dt(row["upstream_last_modified_at_utc"]) if row["upstream_last_modified_at_utc"] else None,
            source_package=row["source_package"],
            recording_method=row["recording_method"],
            attribution=json.loads(row["attribution_json"]),
            installation_hash=row["installation_hash"],
            source_record_hash=row["source_record_hash"],
            zone_offset=row["zone_offset"],
            start_zone_offset=row["start_zone_offset"],
            end_zone_offset=row["end_zone_offset"],
            schema_version=row["schema_version"],
            adapter_id=row["adapter_id"],
            adapter_version=row["adapter_version"],
            identity_namespace_id=row["identity_namespace_id"],
            association_hash=row["association_hash"],
            local_date=row["local_date"],
        )
