from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import time
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from .base import CollectionResult
from ..models import ErrorInfo, HealthObservation, iso_utc, stable_hash, utc_now
from ..secrets import get_google_health_credentials, set_google_health_credentials


API_ROOT = "https://health.googleapis.com/v4"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_WEARABLES = "users/me/dataSourceFamilies/google-wearables"
GOOGLE_HEALTH_SCOPES = (
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
)
DEFAULT_REQUEST_TIMEOUT_SECONDS = 12
DEFAULT_COLLECT_BUDGET_SECONDS = 35
GOOGLE_HEALTH_COLLECTION_POLICY = "bounded_recent_samples_for_timeline_context"


class GoogleHealthError(RuntimeError):
    pass


@dataclass(frozen=True)
class DataTypeSpec:
    data_type: str
    body_key: str
    metric: str
    record_kind: str
    unit: str
    value_paths: tuple[tuple[str, ...], ...]
    time_kind: str


DATA_TYPES = (
    DataTypeSpec("heart-rate", "heartRate", "vitals.heart_rate", "series", "bpm", (("beatsPerMinute",),), "sample"),
    DataTypeSpec("daily-resting-heart-rate", "dailyRestingHeartRate", "vitals.resting_heart_rate", "point", "bpm", (("beatsPerMinute",), ("restingHeartRate",)), "daily"),
    DataTypeSpec("heart-rate-variability", "heartRateVariability", "vitals.heart_rate_variability", "point", "ms RMSSD", (("rmssdMillis",), ("rmssd",)), "sample"),
    DataTypeSpec("daily-heart-rate-variability", "dailyHeartRateVariability", "vitals.heart_rate_variability", "point", "ms RMSSD", (("rmssdMillis",), ("dailyRmssdMillis",), ("rmssd",)), "daily"),
    DataTypeSpec("oxygen-saturation", "oxygenSaturation", "vitals.oxygen_saturation", "point", "%", (("percentage",),), "sample"),
    DataTypeSpec("daily-oxygen-saturation", "dailyOxygenSaturation", "vitals.oxygen_saturation", "point", "%", (("averagePercentage",), ("percentage",), ("average",)), "daily"),
    DataTypeSpec("respiratory-rate", "respiratoryRate", "vitals.respiratory_rate", "point", "breaths/min", (("breathsPerMinute",), ("rate",)), "sample"),
    DataTypeSpec("daily-respiratory-rate", "dailyRespiratoryRate", "vitals.respiratory_rate", "point", "breaths/min", (("breathsPerMinute",), ("averageBreathsPerMinute",), ("rate",)), "daily"),
    DataTypeSpec("skin-temperature", "skinTemperature", "vitals.skin_temperature", "point", "degC", (("temperatureCelsius",), ("temperatureDeltaCelsius",)), "sample"),
    DataTypeSpec("daily-sleep-temperature-derivations", "dailySleepTemperatureDerivations", "vitals.skin_temperature", "point", "degC delta", (("temperatureDeltaCelsius",), ("averageTemperatureDeltaCelsius",)), "daily"),
    DataTypeSpec("steps", "steps", "activity.steps", "interval", "count", (("count",), ("steps",)), "interval"),
    DataTypeSpec("active-zone-minutes", "activeZoneMinutes", "activity.active_zone_minutes", "interval", "min", (("minutes",), ("activeZoneMinutes",)), "interval"),
    DataTypeSpec("distance", "distance", "activity.distance", "interval", "m", (("meters",), ("distanceMeters",)), "interval"),
    DataTypeSpec("total-calories", "totalCalories", "activity.total_calories", "interval", "kcal", (("kilocalories",), ("calories",)), "interval"),
    DataTypeSpec("vo2-max", "vo2Max", "activity.vo2_max", "point", "mL/kg/min", (("millilitersPerKilogramPerMinute",), ("value",)), "sample"),
    DataTypeSpec("daily-vo2-max", "dailyVo2Max", "activity.vo2_max", "point", "mL/kg/min", (("millilitersPerKilogramPerMinute",), ("value",)), "daily"),
    DataTypeSpec("exercise", "exercise", "activity.exercise_session", "session", "", (), "session"),
    DataTypeSpec("sleep", "sleep", "sleep.session", "session", "", (), "sleep"),
)


def _default_json_request(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    *,
    timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - fixed HTTPS Google endpoints only
            raw = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise GoogleHealthError(f"Google Health HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise GoogleHealthError(f"Google Health network error: {exc.reason}") from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GoogleHealthError("Google Health returned an invalid JSON response.") from exc
    if not isinstance(value, dict):
        raise GoogleHealthError("Google Health returned an unexpected response shape.")
    return value


def read_oauth_client(path: Path) -> dict[str, str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    client = value.get("web") or value.get("installed")
    if not isinstance(client, dict):
        raise GoogleHealthError("The credentials file does not contain a Google OAuth client.")
    redirects = client.get("redirect_uris")
    if not isinstance(redirects, list) or not redirects:
        raise GoogleHealthError("The OAuth client has no authorized redirect URI.")
    required = ("client_id", "client_secret")
    if any(not isinstance(client.get(key), str) or not client[key] for key in required):
        raise GoogleHealthError("The OAuth client credentials are incomplete.")
    return {
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "redirect_uri": str(redirects[0]),
    }


def authorization_url(client: dict[str, str]) -> str:
    params = {
        "client_id": client["client_id"],
        "redirect_uri": client["redirect_uri"],
        "response_type": "code",
        "scope": " ".join(GOOGLE_HEALTH_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def _authorization_code(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme and parsed.query:
        code = parse_qs(parsed.query).get("code", [""])[0]
        if code:
            return code
    if value.strip():
        return value.strip()
    raise GoogleHealthError("The authorization code is empty.")


def complete_authorization(
    client: dict[str, str],
    code_or_redirect_url: str,
    *,
    request_json: Callable[[str, str, dict[str, str], bytes | None], dict[str, Any]] = _default_json_request,
) -> dict[str, Any]:
    body = urlencode(
        {
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "code": _authorization_code(code_or_redirect_url),
            "redirect_uri": client["redirect_uri"],
            "grant_type": "authorization_code",
        }
    ).encode("ascii")
    payload = request_json("POST", TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, body)
    refresh_token = payload.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        raise GoogleHealthError("Google did not return a refresh token; repeat consent with offline access.")
    scopes = payload.get("scope") if isinstance(payload.get("scope"), str) else " ".join(GOOGLE_HEALTH_SCOPES)
    set_google_health_credentials(
        client_id=client["client_id"],
        client_secret=client["client_secret"],
        refresh_token=refresh_token,
        redirect_uri=client["redirect_uri"],
        scopes=scopes,
    )
    return {"connected": True, "scopes": scopes.split(), "token_type": payload.get("token_type", "Bearer")}


class GoogleHealthClient:
    def __init__(
        self,
        credentials: dict[str, str],
        *,
        request_json: Callable[[str, str, dict[str, str], bytes | None], dict[str, Any]] | None = None,
        timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ):
        self.credentials = credentials
        self.request_json = request_json or _default_json_request
        self.timeout_seconds = timeout_seconds
        self._access_token: str | None = None
        self._access_expires_at = 0.0

    @classmethod
    def from_keyring(cls, *, timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS) -> "GoogleHealthClient":
        credentials = get_google_health_credentials()
        if credentials is None:
            raise GoogleHealthError("Google Health is not connected.")
        return cls(credentials, timeout_seconds=timeout_seconds)

    def _request_json(self, method: str, url: str, headers: dict[str, str], body: bytes | None) -> dict[str, Any]:
        if self.request_json is _default_json_request:
            return _default_json_request(method, url, headers, body, timeout_seconds=self.timeout_seconds)
        return self.request_json(method, url, headers, body)

    def _token(self) -> str:
        if self._access_token and time.time() < self._access_expires_at - 60:
            return self._access_token
        body = urlencode(
            {
                "client_id": self.credentials["client_id"],
                "client_secret": self.credentials["client_secret"],
                "refresh_token": self.credentials["refresh_token"],
                "grant_type": "refresh_token",
            }
        ).encode("ascii")
        payload = self._request_json("POST", TOKEN_URL, {"Content-Type": "application/x-www-form-urlencoded"}, body)
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise GoogleHealthError("Google Health token refresh did not return an access token.")
        self._access_token = token
        self._access_expires_at = time.time() + int(payload.get("expires_in", 3600))
        return token

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        url = f"{API_ROOT}/{path.lstrip('/')}?{urlencode(params)}"
        return self._request_json("GET", url, {"Authorization": f"Bearer {self._token()}", "Accept": "application/json"}, None)

    def paired_devices(self) -> list[dict[str, Any]]:
        payload = self._get("users/me/pairedDevices", {"pageSize": "100"})
        devices = payload.get("pairedDevices", payload.get("devices", []))
        return [item for item in devices if isinstance(item, dict)] if isinstance(devices, list) else []

    def data_points(self, spec: DataTypeSpec, start: datetime, end: datetime) -> list[dict[str, Any]]:
        if spec.time_kind == "daily":
            field = f"{spec.body_key}.date"
            lower, upper = start.date().isoformat(), end.date().isoformat()
        elif spec.time_kind == "sleep":
            field = "sleep.interval.end_time"
            lower, upper = iso_utc(start), iso_utc(end)
        elif spec.time_kind in {"interval", "session"}:
            field = f"{spec.data_type.replace('-', '_')}.interval.start_time"
            lower, upper = iso_utc(start), iso_utc(end)
        else:
            field = f"{spec.data_type.replace('-', '_')}.sample_time.physical_time"
            lower, upper = iso_utc(start), iso_utc(end)
        filter_value = f'{field} >= "{lower}" AND {field} < "{upper}"'
        path = f"users/me/dataTypes/{spec.data_type}/dataPoints:reconcile"
        params = {
            "dataSourceFamily": GOOGLE_WEARABLES,
            "filter": filter_value,
            "pageSize": "25" if spec.time_kind in {"sleep", "session"} else "10000",
        }
        results: list[dict[str, Any]] = []
        while True:
            payload = self._get(path, params)
            points = payload.get("dataPoints", [])
            if isinstance(points, list):
                results.extend(item for item in points if isinstance(item, dict))
            token = payload.get("nextPageToken")
            if not isinstance(token, str) or not token:
                break
            params["pageToken"] = token
        return results


def _dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _number_at(value: dict[str, Any], paths: Iterable[tuple[str, ...]]) -> float | int | None:
    for path in paths:
        current: Any = value
        for part in path:
            if not isinstance(current, dict) or part not in current:
                current = None
                break
            current = current[part]
        if isinstance(current, bool):
            continue
        if isinstance(current, (int, float)):
            return current
        if isinstance(current, str):
            try:
                return float(current) if "." in current else int(current)
            except ValueError:
                continue
    return None


def _times(spec: DataTypeSpec, body: dict[str, Any]) -> tuple[datetime | None, datetime | None, datetime | None, str | None]:
    sample = body.get("sampleTime") if isinstance(body.get("sampleTime"), dict) else {}
    interval = body.get("interval") if isinstance(body.get("interval"), dict) else {}
    if spec.time_kind == "daily":
        day = body.get("date")
        if isinstance(day, str):
            try:
                measured = datetime.combine(date.fromisoformat(day), datetime.min.time(), tzinfo=timezone.utc)
                return measured, None, None, day
            except ValueError:
                pass
    measured = _dt(sample.get("physicalTime"))
    start = _dt(interval.get("startTime"))
    end = _dt(interval.get("endTime"))
    return measured, start, end, None


def _attribution(point: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    source = point.get("dataSource") if isinstance(point.get("dataSource"), dict) else {}
    device = source.get("device") if isinstance(source.get("device"), dict) else {}
    display_name = device.get("displayName") if isinstance(device.get("displayName"), str) else None
    platform = source.get("platform") if isinstance(source.get("platform"), str) else None
    exact_air = bool(display_name and display_name.casefold() in {"air", "fitbit air"})
    state = "watch_confirmed" if platform == "FITBIT" and exact_air else "external_device"
    evidence = "Google Health wearable data source"
    if platform:
        evidence += f"; platform={platform}"
    if display_name:
        evidence += f"; device={display_name}"
    if not exact_air:
        evidence += "; exact Fitbit Air attribution was not asserted"
    return str(source.get("recordingMethod", "unknown")).casefold(), {
        "state": state,
        "device_type": "watch",
        "device_model": display_name,
        "evidence": evidence,
    }


def _sleep_payload(body: dict[str, Any]) -> dict[str, Any]:
    stages: list[dict[str, str]] = []
    stage_map = {"WAKE": "awake", "AWAKE": "awake", "LIGHT": "light", "DEEP": "deep", "REM": "rem", "ASLEEP": "sleeping"}
    raw_stages = body.get("stages")
    if isinstance(raw_stages, list):
        for stage in raw_stages:
            if not isinstance(stage, dict):
                continue
            start, end = _dt(stage.get("startTime")), _dt(stage.get("endTime"))
            if start and end:
                stages.append({"start_at": iso_utc(start) or "", "end_at": iso_utc(end) or "", "stage": stage_map.get(str(stage.get("type", "")).upper(), "unknown")})
    return {"stages": stages, "title": "Fitbit sleep"}


def normalize_data_point(spec: DataTypeSpec, point: dict[str, Any], observed_at: datetime) -> HealthObservation | None:
    body = point.get(spec.body_key)
    if not isinstance(body, dict):
        return None
    measured, start, end, local_date = _times(spec, body)
    if spec.record_kind == "session" and (start is None or end is None):
        return None
    if spec.metric == "sleep.session":
        payload = _sleep_payload(body)
    elif spec.metric == "activity.exercise_session":
        payload = {"exercise_type": str(body.get("type", body.get("exerciseType", "unknown"))), "title": body.get("title")}
    else:
        value = _number_at(body, spec.value_paths)
        event_at = measured or end or start
        if value is None or event_at is None:
            return None
        payload = (
            {"samples": [{"time": iso_utc(event_at), "value": value, "unit": spec.unit}]}
            if spec.record_kind == "series"
            else {"value": value, "unit": spec.unit}
        )
        metadata = body.get("metadata")
        if isinstance(metadata, dict):
            payload["metadata"] = {str(key): value for key, value in metadata.items() if isinstance(value, (str, int, float, bool))}
    recording_method, attribution = _attribution(point)
    canonical = json.dumps({"name": point.get("name"), "metric": spec.metric, "body": body}, separators=(",", ":"), sort_keys=True)
    record_hash = stable_hash(canonical)
    upstream_modified = _dt(point.get("updateTime")) or _dt(body.get("updateTime"))
    return HealthObservation(
        id=stable_hash(f"google_health_fitbit|{record_hash}"),
        metric=spec.metric,
        category=spec.metric.split(".", 1)[0],
        record_kind=spec.record_kind,
        payload=payload,
        measured_at=measured,
        start_at=start,
        end_at=end,
        observed_by_companion_at=observed_at,
        ingested_at_server=observed_at,
        upstream_last_modified_at=upstream_modified,
        source_package="health.googleapis.com",
        recording_method=recording_method,
        attribution=attribution,
        installation_hash="google-health-fitbit",
        source_record_hash=record_hash,
        adapter_id="google_health_fitbit",
        adapter_version="v4",
        identity_namespace_id="google-health-v4",
        local_date=local_date,
        schema_version="personal-state-google-health/v1",
    )


class GoogleHealthAdapter:
    name = "google_health_fitbit"

    def __init__(self, config, client: GoogleHealthClient | None = None, clock=utc_now):
        self.config = config
        self.client = client
        self.clock = clock
        self._last_attempt_monotonic = 0.0

    def supports(self, metric: str) -> bool:
        return any(spec.metric == metric for spec in DATA_TYPES)

    def collect_range(
        self,
        start: datetime,
        end: datetime,
        *,
        data_types: Iterable[str] | None = None,
        collection_policy: str = GOOGLE_HEALTH_COLLECTION_POLICY,
    ) -> CollectionResult:
        started = self.clock()
        deadline = time.monotonic() + self._collect_budget_seconds()
        observations: list[HealthObservation] = []
        errors: list[ErrorInfo] = []
        counts: dict[str, int] = {}
        requested = set(data_types or ())
        specs = tuple(spec for spec in DATA_TYPES if not requested or spec.data_type in requested)
        try:
            client = self.client or GoogleHealthClient.from_keyring(timeout_seconds=self._request_timeout_seconds())
            for spec in specs:
                if time.monotonic() >= deadline:
                    errors.append(
                        ErrorInfo(
                            "google_health_sync_budget_exceeded",
                            f"Google Health sync exceeded {self._collect_budget_seconds()} seconds; remaining data types were deferred.",
                            retryable=True,
                        )
                    )
                    break
                try:
                    points = client.data_points(spec, start, end)
                    normalized = [item for point in points if (item := normalize_data_point(spec, point, started)) is not None]
                    observations.extend(normalized)
                    counts[spec.data_type] = len(normalized)
                except GoogleHealthError as exc:
                    errors.append(ErrorInfo("google_health_data_type_failed", f"{spec.data_type}: {exc}", retryable=True))
        except GoogleHealthError as exc:
            errors.append(ErrorInfo("google_health_not_connected", str(exc), retryable=False))
        finished = self.clock()
        return CollectionResult(
            adapter=self.name,
            started_at=started,
            finished_at=finished,
            observations=observations,
            status="ok" if not errors else ("partial" if observations else "error"),
            errors=errors,
            metadata={
                "window_start": iso_utc(start),
                "window_end": iso_utc(end),
                "counts": counts,
                "source_family": GOOGLE_WEARABLES,
                "collection_policy": collection_policy,
                "requested_data_types": [spec.data_type for spec in specs],
                "raw_api_persisted": False,
            },
        )

    def collect_current(self, window_minutes: int = 180) -> CollectionResult:
        end = self.clock()
        start = end - timedelta(minutes=max(5, min(int(window_minutes), 360)))
        return self.collect_range(
            start,
            end,
            data_types=("heart-rate", "steps", "active-zone-minutes"),
            collection_policy="priority_current_fitbit_biofeedback",
        )

    def collect(self) -> CollectionResult:
        now_monotonic = time.monotonic()
        minimum = max(60, int(self.config.google_health_sync_interval_seconds))
        if self._last_attempt_monotonic and now_monotonic - self._last_attempt_monotonic < minimum:
            now = self.clock()
            return CollectionResult(adapter=self.name, started_at=now, finished_at=now, status="skipped", metadata={"reason": "sync_interval"})
        self._last_attempt_monotonic = now_monotonic
        end = self.clock()
        start = end - timedelta(hours=max(1, int(self.config.google_health_recent_hours)))
        return self.collect_range(start, end)

    def _request_timeout_seconds(self) -> int:
        return max(3, int(getattr(self.config, "google_health_request_timeout_seconds", DEFAULT_REQUEST_TIMEOUT_SECONDS)))

    def _collect_budget_seconds(self) -> int:
        timeout = self._request_timeout_seconds()
        configured = int(getattr(self.config, "google_health_collect_budget_seconds", DEFAULT_COLLECT_BUDGET_SECONDS))
        return max(timeout, configured)
