from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from uuid import uuid4

from personal_state_mcp.config import AppConfig
from personal_state_mcp.dashboard import DashboardApp
from personal_state_mcp.models import HealthObservation
from personal_state_mcp.service import HealthService
from personal_state_mcp.watch_contract import DIRECT_WEAR_PACKAGE, WatchContractError, parse_json_strict, upload_signature, validate_batch


DEVICE_ID = "phone-test-001"
DEVICE_SECRET = "synthetic-device-secret"
IDENTIFIER_KEY = "synthetic-identifier-key"


def config(db_path: Path, *, allowed_metrics: tuple[str, ...] = ("vitals.heart_rate",)) -> AppConfig:
    return AppConfig(
        db_path=db_path,
        host_id="watch-test",
        allowed_hosts=("watch-test",),
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


def coverage(start: datetime, end: datetime) -> dict:
    return {
        "window_start": start.isoformat().replace("+00:00", "Z"),
        "window_end": end.isoformat().replace("+00:00", "Z"),
        "truncated": False,
        "backfill_limited": False,
        "interrupted": False,
        "reconciling": False,
    }


def heart_observation(now: datetime, *, record_id: str = "heart-1", bpm: int = 74, attribution: str = "samsung_health_unattributed") -> dict:
    start = now - timedelta(minutes=5)
    end = now - timedelta(minutes=1)
    return {
        "record_id": record_id,
        "metric": "vitals.heart_rate",
        "record_kind": "series",
        "start_at": start.isoformat().replace("+00:00", "Z"),
        "end_at": end.isoformat().replace("+00:00", "Z"),
        "start_zone_offset": "-07:00",
        "end_zone_offset": "-07:00",
        "observed_by_companion_at": now.isoformat().replace("+00:00", "Z"),
        "upstream_last_modified_at": now.isoformat().replace("+00:00", "Z"),
        "source_package": "com.sec.android.app.shealth",
        "recording_method": "automatic",
        "attribution": {"state": attribution, "evidence": "Samsung Health origin; device metadata did not prove watch source."},
        "payload": {"samples": [{"time": end.isoformat().replace("+00:00", "Z"), "value": bpm, "unit": "bpm"}]},
    }


def direct_heart_observation(now: datetime, *, bpm: int = 76) -> dict:
    value = heart_observation(now, record_id="wear-heart-1", bpm=bpm, attribution="watch_confirmed")
    value["start_at"] = now.isoformat().replace("+00:00", "Z")
    value["end_at"] = now.isoformat().replace("+00:00", "Z")
    value["source_package"] = DIRECT_WEAR_PACKAGE
    value["recording_method"] = "active"
    value["attribution"] = {
        "state": "watch_confirmed",
        "evidence": "Direct Wear OS Health Services measurement from SM-R920.",
        "device_type": "watch",
        "device_model": "SM-R920",
    }
    value["payload"]["samples"][0]["time"] = value["end_at"]
    return value


def batch(now: datetime, changes: list[dict], *, batch_id: str | None = None) -> dict:
    start = now - timedelta(days=1)
    return {
        "schema_version": "personal-state-watch-batch/v1",
        "batch_id": batch_id or str(uuid4()),
        "installation_id": "installation_test_0001",
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "changes": changes,
        "availability": [
            {
                "metric": "vitals.heart_rate",
                "state": "available",
                "evidence": "Read permission granted and Samsung Health records were returned.",
                "checked_at": now.isoformat().replace("+00:00", "Z"),
                "coverage": coverage(start, now),
            },
            {
                "metric": "wellness.stress",
                "state": "not_exported_by_samsung_mapping",
                "evidence": "Not present in Samsung's documented Health Connect mapping.",
                "checked_at": now.isoformat().replace("+00:00", "Z"),
                "coverage": coverage(start, now),
            },
        ],
    }


def signed_request(payload: dict, now: datetime, nonce: str | None = None) -> tuple[bytes, dict[str, str]]:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    timestamp = str(int(now.timestamp()))
    nonce = nonce or f"nonce-{uuid4()}"
    headers = {
        "x-psm-device-id": DEVICE_ID,
        "x-psm-timestamp": timestamp,
        "x-psm-nonce": nonce,
        "x-psm-batch-id": payload["batch_id"],
        "x-psm-signature": upload_signature(DEVICE_SECRET, timestamp, nonce, payload["batch_id"], body),
    }
    return body, headers


def signed_demand_headers(now: datetime) -> dict[str, str]:
    timestamp = str(int(now.timestamp()))
    nonce = f"demand-{uuid4()}"
    batch_id = f"live-demand-{nonce}"
    return {
        "x-psm-device-id": DEVICE_ID,
        "x-psm-timestamp": timestamp,
        "x-psm-nonce": nonce,
        "x-psm-batch-id": batch_id,
        "x-psm-signature": upload_signature(DEVICE_SECRET, timestamp, nonce, batch_id, b""),
    }


def test_mcp_access_creates_a_signed_short_live_heart_lease() -> None:
    with tempfile.TemporaryDirectory() as directory:
        cfg = config(Path(directory) / "state.db")
        app = DashboardApp(cfg)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        service = HealthService(config=cfg, store=app.store, clock=lambda: now)

        before = app.store.live_heart_demand()
        service.watch()
        demand = app.store.live_heart_demand()

        assert demand["revision"] == before["revision"] + 1
        requested_until = datetime.fromisoformat(demand["requested_until_utc"].replace("Z", "+00:00"))
        assert requested_until == now + timedelta(seconds=20)

        status, response = app.poll_live_heart_demand(
            signed_demand_headers(datetime.now(timezone.utc).replace(microsecond=0)),
            after_revision=int(before["revision"]),
        )

        assert status == 200
        assert response["live_requested"] is True
        assert 1 <= response["lease_seconds"] <= 20

        denied_status, _ = app.poll_live_heart_demand({}, after_revision=0)
        assert denied_status == 401


def test_signed_ingest_is_idempotent_and_stress_has_no_observation() -> None:
    with tempfile.TemporaryDirectory() as directory:
        app = DashboardApp(config(Path(directory) / "state.db"))
        now = datetime.now(timezone.utc).replace(microsecond=0)
        payload = batch(now, [{"operation": "upsert", "observation": heart_observation(now)}])
        body, headers = signed_request(payload, now)

        status, result = app.ingest_watch(body, headers)
        replay_status, replay = app.ingest_watch(body, headers)

        assert status == 200
        assert result["counts"]["inserted"] == 1
        assert replay_status == 200
        assert replay["status"] == "already_committed"
        assert app.store.watch_history_overview()["count"] == 1
        assert app.store.watch_observations(metric="wellness.stress") == []
        availability = {item["metric"]: item for item in app.store.watch_availability()}
        assert availability["wellness.stress"]["state"] == "not_exported_by_samsung_mapping"


def test_direct_wear_heart_rate_is_accepted_with_explicit_provenance() -> None:
    with tempfile.TemporaryDirectory() as directory:
        app = DashboardApp(config(Path(directory) / "state.db"))
        now = datetime.now(timezone.utc).replace(microsecond=0)
        payload = batch(now, [{"operation": "upsert", "observation": direct_heart_observation(now)}])
        body, headers = signed_request(payload, now)

        status, result = app.ingest_watch(body, headers)

        assert status == 200
        assert result["counts"]["inserted"] == 1
        latest = app.store.latest_watch_by_metric()["vitals.heart_rate"]
        assert latest.source_package == DIRECT_WEAR_PACKAGE
        assert latest.attribution["state"] == "watch_confirmed"


def test_dashboard_keeps_live_galaxy_heart_separate_from_newer_fitbit_heart() -> None:
    with tempfile.TemporaryDirectory() as directory:
        app = DashboardApp(config(Path(directory) / "state.db"))
        now = datetime.now(timezone.utc).replace(microsecond=0)
        payload = batch(now, [{"operation": "upsert", "observation": direct_heart_observation(now, bpm=76)}])
        body, headers = signed_request(payload, now)
        assert app.ingest_watch(body, headers)[0] == 200

        fitbit_time = now + timedelta(seconds=1)
        app.store.import_health_observations(
            [
                HealthObservation(
                    id="fitbit-heart-newer",
                    metric="vitals.heart_rate",
                    category="vitals",
                    record_kind="series",
                    payload={"samples": [{"time": fitbit_time.isoformat().replace("+00:00", "Z"), "value": 68, "unit": "bpm"}]},
                    observed_by_companion_at=fitbit_time,
                    ingested_at_server=fitbit_time,
                    source_package="google.health.fitbit",
                    recording_method="actively_measured",
                    attribution={"state": "external_device", "device_name": "Fitbit Air"},
                    installation_hash="google-health",
                    source_record_hash="fitbit-heart-newer",
                    adapter_id="google_health_fitbit",
                    adapter_version="v1",
                    identity_namespace_id="google-health-user",
                    measured_at=fitbit_time,
                )
            ]
        )

        dashboard = app.watch_dashboard(now + timedelta(seconds=2), None)

        assert dashboard["latest"]["vitals.heart_rate"]["provenance"]["adapter"] == "google_health_fitbit"
        assert dashboard["direct_heart_rate"]["provenance"]["adapter"] == "wear_health_services"
        assert dashboard["latest_by_source"]["galaxy"]["vitals.heart_rate"]["payload"]["samples"][-1]["value"] == 76
        assert dashboard["latest_by_source"]["fitbit"]["vitals.heart_rate"]["payload"]["samples"][-1]["value"] == 68
        assert dashboard["sources"]["galaxy"]["status"] == "live"
        assert dashboard["sources"]["fitbit"]["status"] == "synced"


def test_upstream_delete_removes_observation_and_keeps_tombstone_behavior() -> None:
    with tempfile.TemporaryDirectory() as directory:
        app = DashboardApp(config(Path(directory) / "state.db"))
        now = datetime.now(timezone.utc).replace(microsecond=0)
        first = batch(now, [{"operation": "upsert", "observation": heart_observation(now)}])
        body, headers = signed_request(first, now)
        assert app.ingest_watch(body, headers)[0] == 200

        later = now + timedelta(seconds=1)
        deletion = batch(
            later,
            [{
                "operation": "delete",
                "record_id": "heart-1",
                "metric": "vitals.heart_rate",
                "source_package": "com.sec.android.app.shealth",
                "upstream_last_modified_at": later.isoformat().replace("+00:00", "Z"),
            }],
        )
        body, headers = signed_request(deletion, later)
        status, result = app.ingest_watch(body, headers)
        assert status == 200
        assert result["counts"]["deleted"] == 1
        assert app.store.watch_observations(metric="vitals.heart_rate") == []


def test_watch_attribution_requires_positive_watch5_evidence() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    observation = heart_observation(now, attribution="watch_confirmed")
    payload = batch(now, [{"operation": "upsert", "observation": observation}])
    try:
        validate_batch(payload, now)
    except WatchContractError as exc:
        assert exc.code == "unproven_watch_attribution"
    else:
        raise AssertionError("watch_confirmed must require positive device evidence")


def test_location_fields_and_duplicate_json_keys_are_rejected() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    payload = batch(now, [{"operation": "upsert", "observation": heart_observation(now)}])
    payload["changes"][0]["observation"]["payload"]["route"] = [{"latitude": 1, "longitude": 2}]
    body = json.dumps(payload).encode("utf-8")
    try:
        parse_json_strict(body)
    except WatchContractError as exc:
        assert exc.code == "location_data_forbidden"
    else:
        raise AssertionError("route data must be rejected")

    try:
        parse_json_strict(b'{"schema_version":"a","schema_version":"b"}')
    except WatchContractError as exc:
        assert exc.code == "duplicate_json_key"
    else:
        raise AssertionError("duplicate JSON keys must be rejected")


def test_mcp_exposure_policy_hides_collected_body_data() -> None:
    with tempfile.TemporaryDirectory() as directory:
        cfg = config(Path(directory) / "state.db", allowed_metrics=("vitals.heart_rate",))
        app = DashboardApp(cfg)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        payload = batch(now, [{"operation": "upsert", "observation": heart_observation(now)}])
        body, headers = signed_request(payload, now)
        assert app.ingest_watch(body, headers)[0] == 200

        service = HealthService(config=cfg, store=app.store, clock=lambda: now)
        allowed = service.watch()
        denied = service.watch_recent("body.weight")
        assert allowed["ok"]
        assert set(allowed["data"]["latest"]) == {"vitals.heart_rate"}
        assert not denied["ok"]
        assert denied["errors"][0]["code"] == "metric_not_authorized"


def test_dashboard_heart_samples_follow_selected_range() -> None:
    with tempfile.TemporaryDirectory() as directory:
        app = DashboardApp(config(Path(directory) / "state.db"))
        now = datetime.now(timezone.utc).replace(microsecond=0)
        recent = heart_observation(now, record_id="heart-recent", bpm=76)
        older = heart_observation(now - timedelta(days=2), record_id="heart-older", bpm=82)
        payload = batch(now, [
            {"operation": "upsert", "observation": recent},
            {"operation": "upsert", "observation": older},
        ])
        body, headers = signed_request(payload, now)
        assert app.ingest_watch(body, headers)[0] == 200

        day_samples = app.dashboard("24h")["watch"]["heart_rate_samples"]
        three_day_watch = app.dashboard("3d")["watch"]
        three_day_samples = three_day_watch["heart_rate_samples"]

        assert [sample["value"] for sample in day_samples] == [76]
        assert [sample["value"] for sample in three_day_samples] == [82, 76]
        assert three_day_watch["heart_rate_summary"]["count"] == 2
        assert three_day_watch["heart_rate_summary"]["minimum"] == 76.0
        assert three_day_watch["heart_rate_summary"]["maximum"] == 82.0


def test_android_manifest_is_read_only_and_has_no_route_or_location_permission() -> None:
    manifest = (
        Path(__file__).parents[1]
        / "android"
        / "health-connect-companion"
        / "app"
        / "src"
        / "main"
        / "AndroidManifest.xml"
    ).read_text(encoding="utf-8")
    assert "android.permission.health.READ_" in manifest
    assert "android.permission.health.WRITE_" not in manifest
    assert "READ_EXERCISE_ROUTES" not in manifest
    assert "ACCESS_FINE_LOCATION" not in manifest
    assert "ACCESS_COARSE_LOCATION" not in manifest
    assert 'android:allowBackup="false"' in manifest


def test_watch_csv_export_contains_source_labeled_observations() -> None:
    with tempfile.TemporaryDirectory() as directory:
        app = DashboardApp(config(Path(directory) / "state.db"))
        now = datetime.now(timezone.utc).replace(microsecond=0)
        payload = batch(now, [{"operation": "upsert", "observation": heart_observation(now, bpm=79)}])
        body, headers = signed_request(payload, now)
        assert app.ingest_watch(body, headers)[0] == 200

        exported = app.watch_csv_export("24h").decode("utf-8")

        assert "vitals.heart_rate" in exported
        assert "watch_confirmed" not in exported
        assert "samsung_health_unattributed" in exported
        assert '""value"":79' in exported
