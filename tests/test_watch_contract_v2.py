from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
from uuid import uuid4

from personal_state_mcp.config import AppConfig
from personal_state_mcp.adapters.samsung_health_fake import build_fake_samsung_batch
from personal_state_mcp.dashboard import DashboardApp
from personal_state_mcp.service import HealthService
from personal_state_mcp.storage import SCHEMA, WATCH_SCHEMA, StateStore
from personal_state_mcp.watch_contract import upload_signature
from personal_state_mcp.watch_contract_v2 import SAMSUNG_OBSERVATION_METRICS
from personal_state_mcp.watch_contract_v2 import validate_batch_v2


DEVICE_ID = "phone-test-v2"
DEVICE_SECRET = "synthetic-device-secret-v2"
IDENTIFIER_KEY = "synthetic-identifier-key-v2"
NAMESPACE = "3dbf024a-0633-4c39-a356-e3791ee633af"
SAMSUNG_ADAPTER = {"id": "android_samsung_health_data", "version": "1.1.0"}
HEALTH_CONNECT_ADAPTER = {"id": "android_health_connect", "version": "2.0.0"}
WEAR_ADAPTER = {"id": "wear_health_services", "version": "0.2.0"}


def config(db_path: Path, *, allowed_metrics: tuple[str, ...] = ("vitals.heart_rate",)) -> AppConfig:
    return AppConfig(
        db_path=db_path,
        host_id="watch-v2-test",
        allowed_hosts=("watch-v2-test",),
        rate_limit_per_minute=30,
        opportunistic_refresh_enabled=False,
        min_poll_interval_seconds=60,
        glucose_threshold_mg_dl=80,
        near_threshold_margin_mg_dl=10,
        fresh_max_age_seconds=600,
        recent_max_age_seconds=1800,
        future_skew_seconds=300,
        retention_days=None,
        source_timezone="America/Los_Angeles",
        libre_email=None,
        libre_version="4.16.0",
        libre_product="llu.android",
        libre_base_url="https://api.libreview.io",
        watch_enabled=True,
        watch_ingest_hosts=("ingest.example.test",),
        watch_device_id=DEVICE_ID,
        watch_device_secret=DEVICE_SECRET,
        watch_identifier_key=IDENTIFIER_KEY,
        watch_retention_days=3650,
        watch_mcp_metrics=allowed_metrics,
    )


def coverage(now: datetime) -> dict:
    return {
        "window_start": (now - timedelta(days=30)).isoformat().replace("+00:00", "Z"),
        "window_end": now.isoformat().replace("+00:00", "Z"),
        "truncated": False,
        "backfill_limited": True,
        "interrupted": False,
        "reconciling": False,
    }


def batch(now: datetime, adapter: dict, changes: list[dict], availability: list[dict] | None = None) -> dict:
    return {
        "schema_version": "personal-state-watch-batch/v2",
        "adapter": adapter,
        "identity_namespace_id": NAMESPACE,
        "batch_id": str(uuid4()),
        "installation_id": "installation_test_v2_0001",
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "changes": changes,
        "availability": availability or [],
    }


def signed_request(payload: dict, now: datetime) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    timestamp = str(int(now.timestamp()))
    nonce = f"nonce-{uuid4()}"
    return body, {
        "x-psm-device-id": DEVICE_ID,
        "x-psm-timestamp": timestamp,
        "x-psm-nonce": nonce,
        "x-psm-batch-id": payload["batch_id"],
        "x-psm-signature": upload_signature(DEVICE_SECRET, timestamp, nonce, payload["batch_id"], body),
    }


def ingest(app: DashboardApp, payload: dict, now: datetime) -> tuple[int, dict]:
    body, headers = signed_request(payload, now)
    return app.ingest_watch(body, headers)


def point_observation(
    now: datetime,
    adapter: dict,
    *,
    metric: str,
    record_hash: str,
    payload: dict,
    source_package: str = "com.sec.android.app.shealth",
    association_hash: str | None = None,
) -> dict:
    result = {
        "adapter": adapter,
        "record_hash": record_hash,
        "metric": metric,
        "record_kind": "point",
        "measured_at": now.isoformat().replace("+00:00", "Z"),
        "zone_offset": "-07:00",
        "observed_by_companion_at": now.isoformat().replace("+00:00", "Z"),
        "upstream_last_modified_at": now.isoformat().replace("+00:00", "Z"),
        "source_package": source_package,
        "recording_method": "automatic",
        "attribution": {"state": "samsung_health_unattributed", "evidence": "Samsung Health Data SDK record."},
        "payload": payload,
    }
    if association_hash:
        result["association_hash"] = association_hash
    return result


def source_upsert(now: datetime, adapter: dict, observations: list[dict], manifest: dict | None = None) -> dict:
    value = {
        "operation": "upsert",
        "source_type": "SyntheticProviderType",
        "changed_at": now.isoformat().replace("+00:00", "Z"),
        "adapter": adapter,
        "observations": observations,
        "deletions": [],
    }
    if manifest is not None:
        value["association_manifest"] = manifest
    return value


def test_v2_persists_adapter_provenance_and_is_denied_to_agents_by_default() -> None:
    with tempfile.TemporaryDirectory() as directory:
        cfg = config(Path(directory) / "state.db")
        app = DashboardApp(cfg)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        record_hash = "a" * 64
        observation = point_observation(
            now,
            SAMSUNG_ADAPTER,
            metric="cardiac.irregular_rhythm_notification",
            record_hash=record_hash,
            payload={"status": "detected"},
        )
        status, result = ingest(app, batch(now, SAMSUNG_ADAPTER, [source_upsert(now, SAMSUNG_ADAPTER, [observation])]), now)

        assert status == 200
        assert result["counts"]["inserted"] == 1
        stored = app.store.watch_observations(metric="cardiac.irregular_rhythm_notification")[0]
        assert stored.adapter_id == "android_samsung_health_data"
        assert stored.adapter_version == "1.1.0"
        assert stored.identity_namespace_id == NAMESPACE
        assert stored.public_dict()["provenance"]["adapter"] == "android_samsung_health_data"

        service = HealthService(config=cfg, store=app.store, clock=lambda: now)
        assert "cardiac.irregular_rhythm_notification" not in service.watch()["data"]["latest"]
        assert "cardiac.irregular_rhythm_notification" not in service.current_state()["data"]["watch"]["latest"]
        denied = service.watch_recent("cardiac.irregular_rhythm_notification")
        assert not denied["ok"]
        assert denied["errors"][0]["code"] == "metric_not_authorized"

        explicitly_allowed = config(
            Path(directory) / "state.db",
            allowed_metrics=("vitals.heart_rate", "cardiac.irregular_rhythm_notification"),
        )
        explicit_service = HealthService(config=explicitly_allowed, store=app.store, clock=lambda: now)
        assert "cardiac.irregular_rhythm_notification" in explicit_service.watch()["data"]["latest"]
        assert "cardiac.irregular_rhythm_notification" not in explicit_service.current_state()["data"]["watch"]["latest"]


def test_every_new_samsung_metric_is_absent_from_default_agent_policy() -> None:
    cfg = config(Path("unused.db"))
    assert SAMSUNG_OBSERVATION_METRICS.isdisjoint(cfg.watch_mcp_metrics)


def test_mixed_adapter_item_and_synthetic_production_batch_are_rejected() -> None:
    with tempfile.TemporaryDirectory() as directory:
        app = DashboardApp(config(Path(directory) / "state.db"))
        now = datetime.now(timezone.utc).replace(microsecond=0)
        observation = point_observation(
            now,
            SAMSUNG_ADAPTER,
            metric="cardiac.irregular_rhythm_notification",
            record_hash="b" * 64,
            payload={"status": "undefined"},
        )
        mixed = batch(now, HEALTH_CONNECT_ADAPTER, [source_upsert(now, HEALTH_CONNECT_ADAPTER, [observation])])
        status, result = ingest(app, mixed, now)
        assert status == 400
        assert result["error"] == "mixed_adapter_batch"

        synthetic = {"id": "synthetic_test", "version": "1"}
        synthetic_observation = point_observation(
            now,
            synthetic,
            metric="vitals.oxygen_saturation",
            record_hash="c" * 64,
            payload={"value": 97, "unit": "%"},
        )
        status, result = ingest(app, batch(now, synthetic, [source_upsert(now, synthetic, [synthetic_observation])]), now)
        assert status == 400
        assert result["error"] == "adapter_not_allowed"


def test_adapter_scoped_delete_keeps_equivalent_heart_rate_from_other_adapter() -> None:
    with tempfile.TemporaryDirectory() as directory:
        app = DashboardApp(config(Path(directory) / "state.db"))
        now = datetime.now(timezone.utc).replace(microsecond=0)
        shared_hash = "d" * 64

        def heart(adapter: dict, source_package: str, bpm: int) -> dict:
            start = now - timedelta(seconds=15)
            observation = {
                "adapter": adapter,
                "record_hash": shared_hash,
                "metric": "vitals.heart_rate",
                "record_kind": "series",
                "start_at": start.isoformat().replace("+00:00", "Z"),
                "end_at": now.isoformat().replace("+00:00", "Z"),
                "start_zone_offset": "-07:00",
                "end_zone_offset": "-07:00",
                "observed_by_companion_at": now.isoformat().replace("+00:00", "Z"),
                "upstream_last_modified_at": now.isoformat().replace("+00:00", "Z"),
                "source_package": source_package,
                "recording_method": "automatic",
                "attribution": {"state": "unknown", "evidence": "Test source."},
                "payload": {"samples": [{"time": now.isoformat().replace("+00:00", "Z"), "value": bpm, "unit": "bpm"}]},
            }
            return observation

        assert ingest(app, batch(now, HEALTH_CONNECT_ADAPTER, [source_upsert(now, HEALTH_CONNECT_ADAPTER, [heart(HEALTH_CONNECT_ADAPTER, "com.sec.android.app.shealth", 70)])]), now)[0] == 200
        assert ingest(app, batch(now, WEAR_ADAPTER, [source_upsert(now, WEAR_ADAPTER, [heart(WEAR_ADAPTER, "ai.clinicianassist.personalstate", 71)])]), now)[0] == 200
        assert len(app.store.watch_observations(metric="vitals.heart_rate")) == 2

        later = now + timedelta(seconds=1)
        deletion = {
            "operation": "delete",
            "source_type": "HeartRateSeries",
            "changed_at": later.isoformat().replace("+00:00", "Z"),
            "adapter": WEAR_ADAPTER,
            "observations": [],
            "deletions": [{
                "adapter": WEAR_ADAPTER,
                "record_hash": shared_hash,
                "metric": "vitals.heart_rate",
                "source_package": "ai.clinicianassist.personalstate",
                "upstream_last_modified_at": later.isoformat().replace("+00:00", "Z"),
            }],
        }
        assert ingest(app, batch(later, WEAR_ADAPTER, [deletion]), later)[0] == 200
        remaining = app.store.watch_observations(metric="vitals.heart_rate")
        assert len(remaining) == 1
        assert remaining[0].adapter_id == "android_health_connect"


def test_sleep_family_manifest_replacement_removes_absent_members_atomically() -> None:
    with tempfile.TemporaryDirectory() as directory:
        app = DashboardApp(config(Path(directory) / "state.db"))
        now = datetime.now(timezone.utc).replace(microsecond=0)
        association = "e" * 64
        summary_hash = "f" * 64
        score_hash = "1" * 64
        start = now - timedelta(hours=8)

        summary = {
            "adapter": SAMSUNG_ADAPTER,
            "record_hash": summary_hash,
            "association_hash": association,
            "metric": "sleep.summary",
            "record_kind": "session",
            "start_at": start.isoformat().replace("+00:00", "Z"),
            "end_at": now.isoformat().replace("+00:00", "Z"),
            "start_zone_offset": "-07:00",
            "end_zone_offset": "-07:00",
            "observed_by_companion_at": now.isoformat().replace("+00:00", "Z"),
            "upstream_last_modified_at": now.isoformat().replace("+00:00", "Z"),
            "source_package": "com.sec.android.app.shealth",
            "recording_method": "automatic",
            "attribution": {"state": "watch_confirmed", "evidence": "Galaxy Watch5 Pro SM-R920 sleep record."},
            "payload": {"duration_minutes": 480, "session_record_hashes": []},
        }
        score = point_observation(
            now,
            SAMSUNG_ADAPTER,
            metric="sleep.score",
            record_hash=score_hash,
            payload={"value": 82, "unit": "score"},
            association_hash=association,
        )
        manifest = {
            "adapter": SAMSUNG_ADAPTER,
            "association_hash": association,
            "members": [
                {"metric": "sleep.summary", "record_hash": summary_hash},
                {"metric": "sleep.score", "record_hash": score_hash},
            ],
        }
        assert ingest(app, batch(now, SAMSUNG_ADAPTER, [source_upsert(now, SAMSUNG_ADAPTER, [summary, score], manifest)]), now)[0] == 200
        assert len(app.store.watch_observations()) == 2

        later = now + timedelta(seconds=1)
        summary["observed_by_companion_at"] = later.isoformat().replace("+00:00", "Z")
        summary["upstream_last_modified_at"] = later.isoformat().replace("+00:00", "Z")
        reduced_manifest = {
            "adapter": SAMSUNG_ADAPTER,
            "association_hash": association,
            "members": [{"metric": "sleep.summary", "record_hash": summary_hash}],
        }
        status, result = ingest(
            app,
            batch(later, SAMSUNG_ADAPTER, [source_upsert(later, SAMSUNG_ADAPTER, [summary], reduced_manifest)]),
            later,
        )
        assert status == 200
        assert result["counts"]["deleted"] == 1
        remaining = app.store.watch_observations()
        assert [item.metric for item in remaining] == ["sleep.summary"]

        stale_summary = dict(summary)
        stale_summary["observed_by_companion_at"] = now.isoformat().replace("+00:00", "Z")
        stale_summary["upstream_last_modified_at"] = now.isoformat().replace("+00:00", "Z")
        stale_status, stale_result = ingest(
            app,
            batch(now, SAMSUNG_ADAPTER, [source_upsert(now, SAMSUNG_ADAPTER, [stale_summary, score], manifest)]),
            now,
        )
        assert stale_status == 200
        assert stale_result["counts"]["ignored"] >= 1
        assert [item.metric for item in app.store.watch_observations()] == ["sleep.summary"]


def test_v2_availability_is_stored_per_adapter() -> None:
    with tempfile.TemporaryDirectory() as directory:
        app = DashboardApp(config(Path(directory) / "state.db"))
        now = datetime.now(timezone.utc).replace(microsecond=0)
        item = {
            "adapter": SAMSUNG_ADAPTER,
            "metric": "vitals.skin_temperature",
            "state": "no_observation",
            "evidence": "The SDK query succeeded and returned no records in the coverage window.",
            "coverage_checked_at": now.isoformat().replace("+00:00", "Z"),
            "coverage": coverage(now),
        }
        assert ingest(app, batch(now, SAMSUNG_ADAPTER, [], [item]), now)[0] == 200
        stored = app.store.watch_availability()
        assert len(stored) == 1
        assert stored[0]["adapter_id"] == "android_samsung_health_data"
        assert stored[0]["state"] == "no_observation"

        dashboard = app.watch_dashboard(now + timedelta(hours=27), None)
        merged = {entry["metric"]: entry for entry in dashboard["availability"]}
        assert merged["vitals.skin_temperature"]["state"] == "unknown"
        assert merged["vitals.skin_temperature"]["evidence"] == "Coverage report older than 26 hours."

        replacement = batch(now + timedelta(seconds=1), SAMSUNG_ADAPTER, [])
        replacement["identity_namespace_id"] = "f0c220ff-f1b8-4227-a022-7ef5848241fe"
        status, response = ingest(app, replacement, now + timedelta(seconds=1))
        assert status == 409
        assert response["error"] == "identity_namespace_mismatch"


def test_public_source_tree_contains_no_proprietary_samsung_sdk_binary() -> None:
    root = Path(__file__).parents[1]
    forbidden = [path for path in root.rglob("*.aar") if "build" not in {part.lower() for part in path.parts}]
    assert forbidden == []
    gradle = (root / "android" / "health-connect-companion" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
    assert "SAMSUNG_HEALTH_DATA_SDK_AAR" in gradle
    assert "maven" not in "\n".join(line.lower() for line in gradle.splitlines() if "samsung" in line.lower())
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (root / "android" / "health-connect-companion" / "app" / "src" / "main").rglob("*.kt")
    )
    assert "import com.samsung.android.sdk.health" not in source


def test_optional_samsung_reader_fails_closed_and_survives_release_minification() -> None:
    root = Path(__file__).parents[1]
    reader_path = (
        root
        / "android"
        / "health-connect-companion"
        / "app"
        / "src"
        / "samsungSdk"
        / "java"
        / "ai"
        / "clinicianassist"
        / "personalstate"
        / "SamsungHealthDataSdkAdapter.kt"
    )
    reader = reader_path.read_text(encoding="utf-8")
    rules = (
        root / "android" / "health-connect-companion" / "app" / "proguard-rules.pro"
    ).read_text(encoding="utf-8")

    assert "readChanges(builder.build())" in reader
    assert "readAssociatedData(request)" in reader
    assert "ChangeType.DELETE" in reader
    assert "CHANGE_OVERLAP_MINUTES = 5L" in reader
    assert "DataLimitExceeded" in reader
    assert ".take(MAX_SERIES_ITEMS)" not in reader
    assert ".take(MAX_SOURCE_MEMBERS)" not in reader
    assert "SamsungHealthDataSdkAdapter {\n    *;\n}" in rules


def test_fake_samsung_adapter_is_deterministic_and_contract_valid() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    first = build_fake_samsung_batch(now)
    second = build_fake_samsung_batch(now)
    assert first == second
    validate_batch_v2(first, now)


def test_migration_three_is_idempotent_and_rebuilds_adapter_scoped_tombstones() -> None:
    with tempfile.TemporaryDirectory() as directory:
        db_path = Path(directory) / "state.db"
        connection = sqlite3.connect(db_path)
        connection.executescript(SCHEMA)
        connection.executescript(WATCH_SCHEMA)
        connection.execute(
            "INSERT INTO schema_migrations (version, applied_at_utc) VALUES (2, '2026-09-26T00:00:00Z')"
        )
        connection.commit()
        connection.close()

        StateStore(db_path)
        StateStore(db_path)

        assert db_path.with_suffix(".db.pre-samsung-v3.bak").exists()
        connection = sqlite3.connect(db_path)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(health_observations)")}
        tombstone_pk = {
            row[1]: row[5]
            for row in connection.execute("PRAGMA table_info(health_tombstones)")
            if row[5]
        }
        versions = [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")]
        connection.close()

        assert {"adapter_id", "adapter_version", "identity_namespace_id", "association_hash", "local_date"} <= columns
        assert tombstone_pk == {"identity_namespace_id": 1, "adapter_id": 2, "observation_id": 3}
        assert versions == [2, 3]
