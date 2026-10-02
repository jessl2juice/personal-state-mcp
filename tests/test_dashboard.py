from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

from personal_state_mcp.config import AppConfig
from personal_state_mcp.dashboard import (
    DashboardApp,
    _comparison_summary,
    _downsample,
    _glucose_stream_status,
    _heart_sync_status,
    _mark_numeric_segments,
    _numeric_point_stats,
)
from personal_state_mcp.models import GlucoseReading, HealthObservation, Provenance
from personal_state_mcp.watch_contract import FITBIT_HEALTH_CONNECT_ADAPTER_ID


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
        assert payload["current"]["stream"]["status"] == "current"
        assert payload["current"]["stream"]["current"] is True
        assert payload["watch"]["latest"] == {}
        assert "chart" not in payload
        assert "table" not in payload


def test_live_state_includes_fitbit_source_summary() -> None:
    with tempfile.TemporaryDirectory() as directory:
        config = make_config(Path(directory) / "state.db")
        config = AppConfig(**{**config.__dict__, "google_health_enabled": True})
        app = DashboardApp(config)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        app.store.import_health_observations(
            [
                HealthObservation(
                    id="fitbit-steps-1",
                    metric="activity.steps",
                    category="activity",
                    record_kind="interval",
                    payload={"value": 42, "unit": "count"},
                    start_at=now - timedelta(minutes=5),
                    end_at=now - timedelta(minutes=4),
                    observed_by_companion_at=now,
                    ingested_at_server=now,
                    source_package="health.googleapis.com",
                    recording_method="unknown",
                    attribution={"state": "external_device", "device_type": "watch"},
                    installation_hash="google-health-fitbit",
                    source_record_hash="fitbit-steps-1",
                    adapter_id="google_health_fitbit",
                    adapter_version="v4",
                    identity_namespace_id="google-health-v4",
                    schema_version="personal-state-google-health/v1",
                )
            ]
        )

        payload = app.live_state()

        assert payload["watch"]["sources"]["fitbit"]["status"] == "synced"
        assert payload["watch"]["sources"]["fitbit"]["configured"] is True
        assert payload["watch"]["sources"]["fitbit"]["metric_count"] == 1
        assert payload["watch"]["latest_by_source"]["fitbit"]["activity.steps"]["provenance"]["adapter"] == "google_health_fitbit"
        assert "chart" not in payload
        assert "table" not in payload


def test_fitbit_source_summary_marks_cached_records_stale_or_sync_error() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)

    def add_fitbit_observation(app: DashboardApp) -> None:
        observed_at = now - timedelta(hours=3)
        app.store.import_health_observations(
            [
                HealthObservation(
                    id="fitbit-steps-stale",
                    metric="activity.steps",
                    category="activity",
                    record_kind="interval",
                    payload={"value": 42, "unit": "count"},
                    start_at=observed_at - timedelta(minutes=1),
                    end_at=observed_at,
                    observed_by_companion_at=observed_at,
                    ingested_at_server=observed_at,
                    source_package="health.googleapis.com",
                    recording_method="unknown",
                    attribution={"state": "external_device", "device_type": "watch"},
                    installation_hash="google-health-fitbit",
                    source_record_hash="fitbit-steps-stale",
                    adapter_id="google_health_fitbit",
                    adapter_version="v4",
                    identity_namespace_id="google-health-v4",
                    schema_version="personal-state-google-health/v1",
                )
            ]
        )

    with tempfile.TemporaryDirectory() as directory:
        config = AppConfig(**{**make_config(Path(directory) / "state.db").__dict__, "google_health_enabled": True})
        app = DashboardApp(config)
        add_fitbit_observation(app)

        payload = app.watch_dashboard(now, None)

        assert payload["sources"]["fitbit"]["status"] == "stale"
        assert payload["sources"]["fitbit"]["metric_count"] == 1

    with tempfile.TemporaryDirectory() as directory:
        config = AppConfig(**{**make_config(Path(directory) / "state.db").__dict__, "google_health_enabled": True})
        app = DashboardApp(config)
        add_fitbit_observation(app)
        app.store.record_collector_run(
            adapter="google_health_fitbit",
            started_at=now - timedelta(minutes=1),
            finished_at=now,
            status="error",
            readings_seen=0,
            readings_inserted=0,
            error_code="google_health_data_type_failed",
            error_message="Google Health refresh failed.",
        )

        payload = app.watch_dashboard(now, None)

        assert payload["sources"]["fitbit"]["status"] == "sync_error"
        assert payload["sources"]["fitbit"]["last_run"]["error_code"] == "google_health_data_type_failed"
        assert payload["latest_by_source"]["fitbit"]["activity.steps"]["provenance"]["adapter"] == "google_health_fitbit"


def test_fitbit_biofeedback_requires_current_fitbit_heart_rate_without_fallback() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)

    def heart_observation(record_id: str, adapter_id: str, observed_at: datetime, bpm: int) -> HealthObservation:
        source_package = {
            "google_health_fitbit": "health.googleapis.com",
            FITBIT_HEALTH_CONNECT_ADAPTER_ID: "com.fitbit.FitbitMobile",
            "wear_health_services": "com.google.android.wearable.healthservices",
        }[adapter_id]
        return HealthObservation(
            id=record_id,
            metric="vitals.heart_rate",
            category="vitals",
            record_kind="series",
            payload={"samples": [{"time": observed_at.isoformat().replace("+00:00", "Z"), "value": bpm, "unit": "bpm"}]},
            observed_by_companion_at=observed_at,
            ingested_at_server=observed_at,
            source_package=source_package,
            recording_method="actively_measured",
            attribution={"state": "watch_confirmed" if adapter_id == "wear_health_services" else "external_device"},
            installation_hash=adapter_id,
            source_record_hash=record_id,
            adapter_id=adapter_id,
            adapter_version="test",
            identity_namespace_id=adapter_id,
            schema_version="personal-state-test/v1",
        )

    with tempfile.TemporaryDirectory() as directory:
        config = AppConfig(**{**make_config(Path(directory) / "state.db").__dict__, "google_health_enabled": True})
        app = DashboardApp(config)
        app.store.import_health_observations([heart_observation("fitbit-current", "google_health_fitbit", now - timedelta(seconds=30), 72)])

        payload = app.watch_dashboard(now, None)

        assert payload["fitbit_biofeedback"]["usable"] is True
        assert payload["fitbit_biofeedback"]["status"] == "current"
        assert payload["fitbit_biofeedback"]["max_age_seconds"] == 120

    with tempfile.TemporaryDirectory() as directory:
        config = AppConfig(**{**make_config(Path(directory) / "state.db").__dict__, "google_health_enabled": True})
        app = DashboardApp(config)
        app.store.import_health_observations(
            [heart_observation("fitbit-phone-current", FITBIT_HEALTH_CONNECT_ADAPTER_ID, now - timedelta(seconds=30), 72)]
        )
        app.store.record_collector_run(
            adapter="google_health_fitbit",
            started_at=now - timedelta(minutes=1),
            finished_at=now,
            status="error",
            readings_seen=0,
            readings_inserted=0,
            error_code="google_health_data_type_failed",
            error_message="Google Health refresh failed.",
        )

        payload = app.watch_dashboard(now, None)

        assert payload["sources"]["fitbit"]["status"] == "synced"
        assert payload["fitbit_biofeedback"]["usable"] is True
        assert payload["fitbit_biofeedback"]["latest_heart_rate"]["provenance"]["adapter"] == FITBIT_HEALTH_CONNECT_ADAPTER_ID

    with tempfile.TemporaryDirectory() as directory:
        config = AppConfig(**{**make_config(Path(directory) / "state.db").__dict__, "google_health_enabled": True})
        app = DashboardApp(config)
        app.store.import_health_observations(
            [
                heart_observation("fitbit-stale", "google_health_fitbit", now - timedelta(minutes=10), 72),
                heart_observation("galaxy-current", "wear_health_services", now - timedelta(seconds=2), 74),
            ]
        )

        payload = app.watch_dashboard(now, None)

        assert payload["sources"]["galaxy"]["status"] == "live"
        assert payload["fitbit_biofeedback"]["usable"] is False
        assert payload["fitbit_biofeedback"]["status"] == "stale"
        assert payload["fitbit_biofeedback"]["fallback_source"] is None
        assert payload["fitbit_biofeedback"]["latest_heart_rate"]["provenance"]["adapter"] == "google_health_fitbit"


def test_manual_refresh_reports_libre_scope() -> None:
    class FakeCollector:
        def collect_once(self):
            return [{"adapter": "libre_linkup", "status": "ok"}]

    with tempfile.TemporaryDirectory() as directory:
        app = DashboardApp(make_config(Path(directory) / "state.db"))
        app.libre_collector = FakeCollector()  # type: ignore[assignment]

        result = app.refresh()

        assert result["status"] == "ok"
        assert result["source"] == "libre"
        assert result["sources"] == ["libre"]
        assert "LibreLinkUp" in result["message"]
        assert result["results"] == [{"adapter": "libre_linkup", "status": "ok"}]


def test_manual_refresh_skip_reports_libre_scope() -> None:
    with tempfile.TemporaryDirectory() as directory:
        app = DashboardApp(make_config(Path(directory) / "state.db"))
        now = datetime.now(timezone.utc).replace(microsecond=0)
        app.store.last_collector_run = lambda: {"finished_at_utc": now.isoformat().replace("+00:00", "Z")}  # type: ignore[method-assign]

        result = app.refresh()

        assert result["status"] == "skipped"
        assert result["source"] == "libre"
        assert result["sources"] == ["libre"]
        assert "LibreLinkUp" in result["message"]


def test_dashboard_exposes_top_level_sync_degraded_state() -> None:
    with tempfile.TemporaryDirectory() as directory:
        app = DashboardApp(make_config(Path(directory) / "state.db"))
        now = datetime.now(timezone.utc).replace(microsecond=0)
        app.store.upsert_glucose_readings([make_reading(now - timedelta(minutes=4), 112)])
        app.store.record_collector_run(
            adapter="libre_linkup",
            started_at=now - timedelta(seconds=45),
            finished_at=now - timedelta(seconds=1),
            status="error",
            readings_seen=0,
            readings_inserted=0,
            error_code="adapter_timeout",
            error_message="libre_linkup did not finish within 45 seconds.",
        )

        dashboard = app.dashboard("24h")
        live = app.live_state()

        assert dashboard["sync"]["degraded"] is True
        assert dashboard["sync"]["status"] == "stored_history_only"
        assert dashboard["sync"]["last_run"]["error_code"] == "adapter_timeout"
        assert live["sync"]["degraded"] is True
        assert live["sync"]["status"] == "stored_history_only"


def test_steps_source_cards_prefer_current_local_day_total() -> None:
    with tempfile.TemporaryDirectory() as directory:
        config = make_config(Path(directory) / "state.db")
        config = AppConfig(**{**config.__dict__, "google_health_enabled": True})
        app = DashboardApp(config)
        now = datetime(2026, 10, 1, 16, 0, tzinfo=timezone.utc)

        def steps_observation(
            record_id: str,
            value: int,
            start_at: datetime,
            end_at: datetime,
            *,
            adapter_id: str,
            source_package: str,
            aggregation: str | None = None,
        ) -> HealthObservation:
            return HealthObservation(
                id=record_id,
                metric="activity.steps",
                category="activity",
                record_kind="aggregate" if aggregation else "interval",
                payload={"value": value, "unit": "count", **({"aggregation": aggregation} if aggregation else {})},
                start_at=start_at,
                end_at=end_at,
                observed_by_companion_at=now,
                ingested_at_server=now,
                source_package=source_package,
                recording_method="unknown",
                attribution={"state": "external_device"},
                installation_hash=adapter_id,
                source_record_hash=record_id,
                adapter_id=adapter_id,
                adapter_version="test",
                identity_namespace_id=adapter_id,
                schema_version="personal-state-test/v1",
            )

        app.store.import_health_observations(
            [
                steps_observation(
                    "fitbit-steps-120",
                    120,
                    datetime(2026, 10, 1, 13, 0, tzinfo=timezone.utc),
                    datetime(2026, 10, 1, 13, 1, tzinfo=timezone.utc),
                    adapter_id="google_health_fitbit",
                    source_package="health.googleapis.com",
                ),
                steps_observation(
                    "fitbit-steps-310",
                    310,
                    datetime(2026, 10, 1, 14, 0, tzinfo=timezone.utc),
                    datetime(2026, 10, 1, 14, 1, tzinfo=timezone.utc),
                    adapter_id="google_health_fitbit",
                    source_package="health.googleapis.com",
                ),
                steps_observation(
                    "galaxy-rolling-steps",
                    3994,
                    datetime(2026, 9, 30, 13, 10, tzinfo=timezone.utc),
                    datetime(2026, 10, 1, 13, 10, tzinfo=timezone.utc),
                    adapter_id="android_health_connect",
                    source_package="com.sec.android.app.shealth",
                    aggregation="health_connect",
                ),
            ]
        )

        payload = app.watch_dashboard(now, None)

        fitbit_steps = payload["latest_by_source"]["fitbit"]["activity.steps"]
        assert fitbit_steps["payload"]["value"] == 430
        assert fitbit_steps["payload"]["aggregation"] == "today_so_far"
        assert fitbit_steps["payload"]["source_observation_count"] == 2
        assert fitbit_steps["local_date"] == "2026-10-01"
        assert "activity.steps" not in payload["latest_by_source"]["galaxy"]


def test_glucose_stream_status_never_treats_stale_reading_as_current() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    reading = make_reading(now - timedelta(hours=5), 121)
    freshness = {
        "status": "stale",
        "measurement_age_seconds": 18_000,
        "max_age_seconds": 600,
    }

    status = _glucose_stream_status(reading, freshness)

    assert status["status"] == "stopped"
    assert status["current"] is False
    assert status["last_measurement_at"] == reading.measured_at.isoformat().replace("+00:00", "Z")
    assert "may have ended" in status["message"]


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


def test_heart_series_segments_preserve_real_gaps_before_downsampling() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    points = [
        {"time": now.isoformat(), "value": 72},
        {"time": (now + timedelta(seconds=55)).isoformat(), "value": 74},
        {"time": (now + timedelta(seconds=116)).isoformat(), "value": 76},
    ]

    marked = _mark_numeric_segments(points, max_gap_seconds=60)

    assert [point["segment"] for point in marked] == [0, 0, 1]
    assert all("segment" not in point for point in points)


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


def test_heart_rate_is_live_only_within_ten_seconds() -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    upload = {"received_at_utc": now.isoformat()}

    at_cutoff = _heart_sync_status({"last_at": (now - timedelta(seconds=10)).isoformat()}, upload, now)
    over_cutoff = _heart_sync_status({"last_at": (now - timedelta(seconds=11)).isoformat()}, upload, now)

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
    assert 'class="current-context"' not in html
    assert 'class="threshold-note"' in html
    assert 'id="metric-dialog"' in html
    assert 'id="metric-history-chart"' in html
    assert 'id="timeline-panel"' in html
    assert 'id="glucose-stream-alert"' in html
    assert 'id="current-glucose-unit"' in html
    assert 'class="source-strip"' in html
    assert 'id="source-libre-status"' in html
    assert 'id="source-galaxy-status"' in html
    assert 'id="source-fitbit-status"' in html
    assert 'id="fitbit-refresh-button"' in html
    assert 'id="fitbit-biofeedback-status"' in html

    javascript = (static_dir / "app.js").read_text(encoding="utf-8")
    assert 'streamStatus === "stopped"' in javascript
    assert "const glucoseSource = reading ? adapterLabel(reading) : \"LibreLinkUp follower\"" in javascript
    assert "heartSource" in javascript
    assert "· measured" in javascript
    assert 'source === "Fitbit Air via Google Health"' in javascript
    assert 'source === "libre_linkup_follower"' in javascript
    assert 'adapter === "wear_health_services"' in javascript
    assert "renderSyncRail(payload)" in javascript
    assert "Stored history only" in javascript
    assert "Live refresh failed" in javascript
    assert "sourceFilterForObservation" in javascript
    assert 'google_health_fitbit: "google_health_sync"' in javascript
    assert 'data-source="${escapeHtml(sourceFilter)}"' in javascript
    assert "&source=${encodeURIComponent(state.detailSource)}" in javascript
    assert "openMetricHistory(card.dataset.metric, card.dataset.source || null)" in javascript
    assert "renderCurrent(state.payload)" in javascript
    assert "renderWatchMetricCards(state.payload.watch.latest, state.payload.watch.latest_by_source || {})" in javascript
    assert "...(liveWatch.latest_by_source?.galaxy || {})" in javascript
    assert "...(liveWatch.latest_by_source?.fitbit || {})" in javascript
    assert "Sync failing" in javascript
    assert "Stale sync" in javascript
    assert "Google Health refresh failing" in javascript
    assert "refresh overdue" in javascript
    assert "refreshFitbitCurrentness" in javascript
    assert "/api/fitbit/refresh" in javascript
    assert "Casey biofeedback unavailable" in javascript
    assert "result.current_biofeedback_status?.usable" in javascript
    assert "Direct heart live" in javascript
    assert "recorded metrics" in javascript
    assert "Live · ${metricCount" not in javascript
    assert '"No current data"' in javascript
    assert "Last recorded:" in javascript
    assert '"Glucose unavailable"' in javascript
    assert "collapseSegmentForDisplay" in javascript
    assert "medianSmoothSegment" in javascript
    assert '"Heart rate · trend"' in javascript
    assert 'payload.watch?.direct_heart_rate' in javascript
    assert 'google_health_fitbit: "Google Health synchronized wearable"' in javascript
    assert 'fitbit_ble_heart_rate: "Fitbit direct Bluetooth"' in javascript
    assert 'fitbit_ble_heart_rate: "fitbit_ble_live"' in javascript
    assert "Fitbit Air · Google Health" not in javascript
    assert "Treat direct Galaxy and Fitbit Bluetooth heart rate as live only when measured within ten seconds." in javascript
