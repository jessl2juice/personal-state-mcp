from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

from personal_state_mcp.adapters.fitbit_ble import (
    heart_rate_observation_from_ble,
    parse_heart_rate_measurement,
)
from personal_state_mcp.config import AppConfig
from personal_state_mcp.dashboard import DashboardApp
from personal_state_mcp.watch_contract import FITBIT_BLE_ADAPTER_ID, observation_recency


def make_config(db_path: Path) -> AppConfig:
    return AppConfig(
        db_path=db_path,
        host_id="fitbit-ble-test",
        allowed_hosts=("fitbit-ble-test",),
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
        fitbit_ble_enabled=True,
    )


def test_parse_bluetooth_heart_rate_measurement_uint8_and_rr_intervals() -> None:
    packet = bytes([
        0x16,
        74,
        0x20,
        0x03,
        0x10,
        0x03,
    ])

    measurement = parse_heart_rate_measurement(packet)

    assert measurement.bpm == 74
    assert measurement.sensor_contact == "detected"
    assert measurement.rr_intervals_ms == (781.25, 765.625)
    assert measurement.energy_expended_kj is None


def test_parse_bluetooth_heart_rate_measurement_uint16_with_energy() -> None:
    packet = bytes([
        0x09,
        0x2C,
        0x01,
        0x34,
        0x12,
    ])

    measurement = parse_heart_rate_measurement(packet)

    assert measurement.bpm == 300
    assert measurement.sensor_contact == "unsupported"
    assert measurement.energy_expended_kj == 0x1234


def test_fitbit_ble_observation_is_source_labeled_and_hashes_device_identity() -> None:
    now = datetime(2026, 10, 2, 16, 0, tzinfo=timezone.utc)
    measurement = parse_heart_rate_measurement(bytes([0x06, 71]))

    observation = heart_rate_observation_from_ble(
        measurement,
        measured_at=now,
        received_at=now + timedelta(seconds=1),
        device_address="AA:BB:CC:DD:EE:FF",
        device_name="Fitbit Air",
    )
    public = observation.public_dict()

    assert observation.adapter_id == FITBIT_BLE_ADAPTER_ID
    assert public["provenance"]["source"] == "Fitbit direct Bluetooth heart rate"
    assert public["provenance"]["source_record_hash"] != "AA:BB:CC:DD:EE:FF"
    assert public["payload"]["samples"][0]["value"] == 71
    assert public["payload"]["samples"][0]["sensor_contact"] == "detected"


def test_dashboard_uses_fitbit_ble_as_live_casey_biofeedback_source() -> None:
    with tempfile.TemporaryDirectory() as directory:
        app = DashboardApp(make_config(Path(directory) / "state.db"))
        now = datetime.now(timezone.utc).replace(microsecond=0)
        measurement = parse_heart_rate_measurement(bytes([0x06, 78]))
        observation = heart_rate_observation_from_ble(
            measurement,
            measured_at=now - timedelta(seconds=3),
            received_at=now - timedelta(seconds=2),
            device_name="Fitbit Air",
        )
        app.store.import_health_observations([observation])

        payload = app.watch_dashboard(now, None)

        assert observation_recency(observation, now)["status"] == "live"
        assert payload["direct_heart_rate"]["provenance"]["adapter"] == FITBIT_BLE_ADAPTER_ID
        assert payload["sources"]["fitbit"]["status"] == "live"
        assert payload["fitbit_biofeedback"]["status"] == "live"
        assert payload["fitbit_biofeedback"]["usable"] is True
        assert payload["fitbit_biofeedback"]["realtime"] is True
        assert payload["latest_by_source"]["fitbit"]["vitals.heart_rate"]["provenance"]["adapter"] == FITBIT_BLE_ADAPTER_ID
