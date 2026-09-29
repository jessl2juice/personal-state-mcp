from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from personal_state_mcp.adapters.google_health import (
    DATA_TYPES,
    GOOGLE_HEALTH_SCOPES,
    GoogleHealthAdapter,
    GoogleHealthClient,
    authorization_url,
    normalize_data_point,
    read_oauth_client,
)
from personal_state_mcp.config import AppConfig
from personal_state_mcp.service import HealthService
from personal_state_mcp.storage import StateStore


NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def _spec(name: str):
    return next(item for item in DATA_TYPES if item.data_type == name)


def test_authorization_is_read_only_and_parses_google_client(tmp_path: Path) -> None:
    credentials = tmp_path / "client.json"
    credentials.write_text(
        json.dumps(
            {
                "web": {
                    "client_id": "client.apps.googleusercontent.com",
                    "client_secret": "secret",
                    "redirect_uris": ["https://www.google.com"],
                }
            }
        ),
        encoding="utf-8",
    )
    client = read_oauth_client(credentials)
    query = parse_qs(urlparse(authorization_url(client)).query)
    assert query["access_type"] == ["offline"]
    assert "include_granted_scopes" not in query
    assert set(query["scope"][0].split()) == set(GOOGLE_HEALTH_SCOPES)
    assert all("write" not in scope for scope in GOOGLE_HEALTH_SCOPES)


def test_normalizes_fitbit_air_heart_rate_without_calling_it_live() -> None:
    point = {
        "name": "users/me/dataTypes/heart-rate/dataPoints/air-1",
        "dataSource": {
            "recordingMethod": "ACTIVELY_MEASURED",
            "platform": "FITBIT",
            "device": {"displayName": "Fitbit Air"},
        },
        "heartRate": {
            "sampleTime": {"physicalTime": "2026-09-29T11:59:00Z", "utcOffset": "0s"},
            "beatsPerMinute": "72",
            "metadata": {"motionContext": "SEDENTARY", "sensorLocation": "WRIST"},
        },
    }
    observation = normalize_data_point(_spec("heart-rate"), point, NOW)
    assert observation is not None
    assert observation.metric == "vitals.heart_rate"
    assert observation.payload["samples"][0]["value"] == 72
    assert observation.adapter_id == "google_health_fitbit"
    assert observation.attribution["state"] == "watch_confirmed"
    assert observation.recording_method == "actively_measured"


def test_does_not_overclaim_unknown_google_wearable_as_fitbit_air() -> None:
    point = {
        "name": "users/me/dataTypes/oxygen-saturation/dataPoints/unknown-1",
        "dataSource": {"recordingMethod": "DERIVED", "platform": "FITBIT"},
        "oxygenSaturation": {
            "sampleTime": {"physicalTime": "2026-09-29T05:00:00Z"},
            "percentage": 97.5,
        },
    }
    observation = normalize_data_point(_spec("oxygen-saturation"), point, NOW)
    assert observation is not None
    assert observation.attribution["state"] == "external_device"
    assert "not asserted" in observation.attribution["evidence"]


def test_client_requests_reconciled_google_wearable_stream() -> None:
    calls: list[str] = []

    def request_json(method: str, url: str, headers: dict[str, str], body: bytes | None):
        calls.append(url)
        if "oauth2.googleapis.com/token" in url:
            return {"access_token": "access", "expires_in": 3600}
        return {"dataPoints": []}

    client = GoogleHealthClient(
        {
            "client_id": "id",
            "client_secret": "secret",
            "refresh_token": "refresh",
            "redirect_uri": "https://www.google.com",
            "scopes": " ".join(GOOGLE_HEALTH_SCOPES),
        },
        request_json=request_json,
    )
    client.data_points(_spec("heart-rate"), NOW.replace(hour=11), NOW)
    request_url = calls[-1]
    assert "dataPoints%3Areconcile" not in request_url
    assert "dataPoints:reconcile" in request_url
    query = parse_qs(urlparse(request_url).query)
    assert query["dataSourceFamily"] == ["users/me/dataSourceFamilies/google-wearables"]
    assert "heart_rate.sample_time.physical_time" in query["filter"][0]


def test_adapter_returns_partial_when_one_data_type_fails() -> None:
    class FakeClient:
        def data_points(self, spec, start, end):
            if spec.data_type == "sleep":
                raise RuntimeError("unexpected test failure")
            return []

    config = SimpleNamespace(google_health_sync_interval_seconds=300, google_health_recent_hours=36)
    adapter = GoogleHealthAdapter(config, client=FakeClient(), clock=lambda: NOW)
    # Non-GoogleHealth exceptions are intentionally surfaced as implementation defects.
    try:
        adapter.collect_range(NOW.replace(hour=0), NOW)
    except RuntimeError as exc:
        assert str(exc) == "unexpected test failure"
    else:
        raise AssertionError("Unexpected errors must not be silently converted into vendor failures.")


def test_casey_context_contains_allowlisted_fitbit_context(tmp_path: Path) -> None:
    point = {
        "name": "users/me/dataTypes/heart-rate/dataPoints/air-2",
        "dataSource": {"recordingMethod": "ACTIVELY_MEASURED", "platform": "FITBIT", "device": {"displayName": "Fitbit Air"}},
        "heartRate": {"sampleTime": {"physicalTime": "2026-09-29T11:59:00Z"}, "beatsPerMinute": "71"},
    }
    observation = normalize_data_point(_spec("heart-rate"), point, NOW)
    assert observation is not None
    db = tmp_path / "state.db"
    store = StateStore(db)
    store.import_health_observations([observation])
    config = AppConfig(
        db_path=db,
        host_id="casey",
        allowed_hosts=("casey",),
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
        google_health_enabled=True,
        watch_mcp_metrics=("vitals.heart_rate",),
    )
    response = HealthService(config=config, store=store, clock=lambda: NOW).context()
    assert response["ok"] is True
    fitbit = response["data"]["physiological_context"]["latest"]["vitals.heart_rate"]
    assert fitbit["provenance"]["adapter"] == "google_health_fitbit"
    assert fitbit["observation_recency"]["status"] == "recent_record"
