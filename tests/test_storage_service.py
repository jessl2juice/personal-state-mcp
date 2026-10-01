from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from personal_state_mcp.config import AppConfig
from personal_state_mcp.models import GlucoseReading, HealthObservation, Provenance
from personal_state_mcp.service import HealthService
from personal_state_mcp.storage import StateStore


def config(db_path: Path, **overrides) -> AppConfig:
    base = AppConfig(
        db_path=db_path,
        host_id="default-local",
        allowed_hosts=("default-local",),
        rate_limit_per_minute=30,
        opportunistic_refresh_enabled=False,
        min_poll_interval_seconds=60,
        glucose_threshold_mg_dl=80,
        near_threshold_margin_mg_dl=10,
        fresh_max_age_seconds=600,
        recent_max_age_seconds=1800,
        future_skew_seconds=300,
        retention_days=None,
        source_timezone=None,
        libre_email=None,
        libre_version="4.16.0",
        libre_product="llu.android",
        libre_base_url="https://api.libreview.io",
    )
    return replace(base, **overrides)


def reading(now: datetime, value: int = 100, minutes_old: int = 3) -> GlucoseReading:
    measured = now - timedelta(minutes=minutes_old)
    return GlucoseReading(
        adapter="test",
        value_mg_dl=value,
        trend="flat",
        trend_raw=3,
        sample_type="current",
        measured_at=measured,
        received_at=measured + timedelta(seconds=30),
        stored_at=measured + timedelta(seconds=30),
        provenance=Provenance(
            adapter="test",
            vendor="synthetic",
            source="unit_test",
            measurement_timestamp_source="unit_test",
        ),
    ).with_id()


def watch_observation(now: datetime, *, record_id: str, adapter_id: str, value: int) -> HealthObservation:
    return HealthObservation(
        id=record_id,
        metric="vitals.heart_rate",
        category="vitals",
        record_kind="series",
        payload={"samples": [{"time": now.isoformat().replace("+00:00", "Z"), "value": value, "unit": "bpm"}]},
        start_at=now - timedelta(seconds=30),
        end_at=now,
        observed_by_companion_at=now,
        ingested_at_server=now,
        source_package="health.googleapis.com" if adapter_id == "google_health_fitbit" else "com.sec.android.app.shealth",
        recording_method="automatic",
        attribution={"state": "external_device"},
        installation_hash=adapter_id,
        source_record_hash=record_id,
        adapter_id=adapter_id,
        adapter_version="test",
        identity_namespace_id=adapter_id,
        schema_version="personal-state-test/v1",
    )


class StorageServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "state.db"
        self.now = datetime(2026, 9, 24, 20, 0, tzinfo=timezone.utc)
        self.store = StateStore(self.db_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_glucose_response_contains_freshness_provenance_and_safety(self) -> None:
        self.store.upsert_glucose_readings([reading(self.now, value=102)])
        service = HealthService(config=config(self.db_path), store=self.store, clock=lambda: self.now)
        response = service.glucose()
        self.assertTrue(response["ok"])
        self.assertEqual("fresh", response["freshness"]["status"])
        self.assertEqual("test", response["provenance"]["adapter"])
        self.assertIn("diagnosis", response["safety"]["not_for"])

    def test_denied_host_gets_no_health_data(self) -> None:
        self.store.upsert_glucose_readings([reading(self.now, value=102)])
        service = HealthService(
            config=config(self.db_path, host_id="unknown-host", allowed_hosts=("default-local",)),
            store=self.store,
            clock=lambda: self.now,
        )
        response = service.glucose()
        self.assertFalse(response["ok"])
        self.assertEqual({}, response["data"])
        self.assertEqual("host_id_not_allowed", response["errors"][0]["code"])

    def test_rate_limit_is_enforced(self) -> None:
        self.store.upsert_glucose_readings([reading(self.now, value=102)])
        service = HealthService(
            config=config(self.db_path, rate_limit_per_minute=1),
            store=self.store,
            clock=lambda: self.now,
        )
        first = service.glucose()
        second = service.context()
        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertEqual("per_host_rate_limit_exceeded", second["errors"][0]["code"])

    def test_history_export_and_delete(self) -> None:
        self.store.upsert_glucose_readings([reading(self.now, value=102)])
        output = Path(self.tmp.name) / "history.jsonl"
        exported = self.store.export_history_jsonl(output)
        deleted = self.store.delete_history()
        self.assertEqual(1, exported)
        self.assertEqual(1, deleted)
        self.assertTrue(output.read_text(encoding="utf-8").strip())

    def test_delete_watch_source_history_only_removes_selected_adapter(self) -> None:
        self.store.import_health_observations([
            watch_observation(self.now, record_id="fitbit-heart", adapter_id="google_health_fitbit", value=68),
            watch_observation(self.now, record_id="galaxy-heart", adapter_id="android_health_connect", value=76),
        ])

        deleted = self.store.delete_watch_source_history(("google_health_fitbit",))
        remaining = self.store.watch_observations(metric="vitals.heart_rate")

        self.assertEqual(1, deleted)
        self.assertEqual(1, len(remaining))
        self.assertEqual("android_health_connect", remaining[0].adapter_id)


if __name__ == "__main__":
    unittest.main()

