from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

from personal_state_mcp.config import AppConfig
from personal_state_mcp.dashboard import (
    DashboardApp,
    _comparison_summary,
    _downsample,
    _heart_sync_status,
    _numeric_point_stats,
)
from personal_state_mcp.models import GlucoseReading, Provenance


def make_config(db_path: Path) -> AppConfig:
    return AppConfig(
        db_path=db_path,
        host_id="dashboard-test",
        allowed_hosts=("dashboard-test",),
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
    )


def make_reading(measured_at: datetime, value: int) -> GlucoseReading:
    return GlucoseReading(
        adapter="test",
        value_mg_dl=value,
        measured_at=measured_at,
        received_at=measured_at + timedelta(seconds=20),
        stored_at=measured_at + timedelta(seconds=20),
        provenance=Provenance(
            adapter="test",
            vendor="synthetic",
            source="unit_test",
            measurement_timestamp_source="unit_test",
        ),
        sample_type="history",
    ).with_id()


def test_dashboard_summary_and_export() -> None:
    with tempfile.TemporaryDirectory() as directory:
        app = DashboardApp(make_config(Path(directory) / "state.db"))
        now = datetime.now(timezone.utc)
        app.store.upsert_glucose_readings(
            [
                make_reading(now - timedelta(minutes=20), 75),
                make_reading(now - timedelta(minutes=15), 85),
                make_reading(now - timedelta(minutes=10), 105),
                make_reading(now - timedelta(minutes=5), 115),
            ]
        )

        payload = app.dashboard("3h")

        assert payload["stats"]["count"] == 4
        assert payload["stats"]["below_threshold"] == 1
        assert payload["stats"]["near_threshold"] == 1
        assert payload["stats"]["above_near_band"] == 2
        assert payload["current"]["reading"]["value_mg_dl"] == 115
        assert payload["safety"]["use"] == "informational_context_only"

        export = app.csv_export("3h").decode("utf-8")
        assert "measured_at_utc,received_at_utc,value_mg_dl" in export
        assert ",115," in export


def test_live_state_is_compact_and_contains_current_reading() -> None:
    with tempfile.TemporaryDirectory() as directory:
        app = DashboardApp(make_config(Path(directory) / "state.db"))
        now = datetime.now(timezone.utc)
        app.store.upsert_glucose_readings([make_reading(now - timedelta(seconds=10), 104)])

        payload = app.live_state()

        assert payload["current"]["reading"]["value_mg_dl"] == 104
        assert payload["watch"]["latest"] == {}
        assert "chart" not in payload
        assert "table" not in payload


def test_downsample_preserves_extrema() -> None:
    now = datetime.now(timezone.utc)
    readings = [make_reading(now + timedelta(minutes=index), 100) for index in range(200)]
    readings[80] = make_reading(now + timedelta(minutes=80), 42)
    readings[120] = make_reading(now + timedelta(minutes=120), 240)

    sampled = _downsample(readings, max_points=40)
    values = [reading.value_mg_dl for reading in sampled]

    assert len(sampled) <= 40
    assert 42 in values
    assert 240 in values


def test_heart_summary_and_comparison_report_actual_overlap() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    glucose = [
        make_reading(now + timedelta(minutes=offset), 100 + offset)
        for offset in (0, 5, 10)
    ]
    heart = [
        {"time": (now + timedelta(minutes=offset)).isoformat(), "value": value}
        for offset, value in ((1, 70), (6, 74), (11, 72))
    ]

    summary = _numeric_point_stats(heart)
    comparison = _comparison_summary(glucose, heart)

    assert summary["count"] == 3
    assert summary["average"] == 72.0
    assert summary["minimum"] == 70.0
    assert summary["maximum"] == 74.0
    assert comparison["status"] == "comparable"
    assert comparison["paired_count"] == 3


def test_comparison_does_not_imply_alignment_across_separate_periods() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    glucose = [make_reading(now, 100)]
    heart = [{"time": (now - timedelta(days=2)).isoformat(), "value": 72}]

    comparison = _comparison_summary(glucose, heart)

    assert comparison["status"] == "no_overlap"
    assert comparison["paired_count"] == 0


def test_recent_upload_with_old_heart_sample_is_a_sync_failure() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    summary = {"last_at": (now - timedelta(days=7)).isoformat()}
    upload = {"received_at_utc": now.isoformat()}

    status = _heart_sync_status(summary, upload, now)

    assert status["status"] == "failure"
    assert "transfer is failing" in status["message"]


def test_heart_rate_is_live_only_within_one_minute() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    upload = {"received_at_utc": now.isoformat()}

    at_cutoff = _heart_sync_status({"last_at": (now - timedelta(seconds=60)).isoformat()}, upload, now)
    over_cutoff = _heart_sync_status({"last_at": (now - timedelta(seconds=61)).isoformat()}, upload, now)

    assert at_cutoff["status"] == "current"
    assert over_cutoff["status"] == "failure"


def test_dashboard_supports_year_window() -> None:
    with tempfile.TemporaryDirectory() as directory:
        app = DashboardApp(make_config(Path(directory) / "state.db"))
        payload = app.dashboard("1y")

        assert payload["range"]["key"] == "1y"
        assert payload["range"]["hours"] == 24 * 365
        assert payload["range"]["window_start"] is not None
        assert payload["range"]["window_end"] is not None


def test_dashboard_host_allowlist(monkeypatch) -> None:
    monkeypatch.setenv("PERSONAL_STATE_DASHBOARD_ALLOWED_HOSTS", "health.example.test")
    with tempfile.TemporaryDirectory() as directory:
        app = DashboardApp(make_config(Path(directory) / "state.db"))

        assert app.host_allowed("127.0.0.1:8766")
        assert app.host_allowed("localhost:8766")
        assert app.host_allowed("health.example.test")
        assert not app.host_allowed("attacker.example")


def test_dashboard_companion_path_can_be_installed_outside_source(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as directory:
        companion = Path(directory) / "personal-state-phone.apk"
        monkeypatch.setenv("PERSONAL_STATE_COMPANION_APK", str(companion))
        app = DashboardApp(make_config(Path(directory) / "state.db"))

        assert app.companion_apk == companion


def test_dashboard_places_all_current_signals_before_history_charts() -> None:
    static_dir = Path(__file__).parents[1] / "src" / "personal_state_mcp" / "dashboard_static"
    html = (static_dir / "index.html").read_text(encoding="utf-8")

    assert html.count('id="watch-latest-grid"') == 1
    assert html.count('id="glucose-chart"') == 1
    assert html.index('id="watch-latest-grid"') < html.index('id="glucose-chart"')
    assert 'id="signal-count"' in html
