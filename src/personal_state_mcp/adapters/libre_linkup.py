from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from http.client import HTTPResponse
import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .base import CollectionResult
from personal_state_mcp.config import AppConfig
from personal_state_mcp.models import ErrorInfo, GlucoseReading, Provenance, stable_hash, utc_now
from personal_state_mcp.secrets import get_libre_password
from personal_state_mcp.time_utils import pick_measurement_timestamp


REGION_RE = re.compile(r"^[a-z0-9-]{1,20}$", re.IGNORECASE)
BASE_HOST_RE = re.compile(r"^api(?:-[a-z0-9-]{1,20})?\.libreview\.io$", re.IGNORECASE)


class LibreLinkUpError(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        raise HTTPError(req.full_url, code, f"redirect rejected: {newurl}", headers, fp)


def validate_libreview_base_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise LibreLinkUpError("invalid_host", "LibreView host must use HTTPS.")
    if not parsed.hostname or not BASE_HOST_RE.match(parsed.hostname):
        raise LibreLinkUpError("invalid_host", "LibreView host is not an expected api.libreview.io host.")
    return f"https://{parsed.hostname}"


def region_host(region: str) -> str:
    if not REGION_RE.match(region):
        raise LibreLinkUpError("invalid_region", "LibreView returned an invalid region token.")
    return validate_libreview_base_url(f"https://api-{region.lower()}.libreview.io")


def libreview_account_id_hash(account_id: str) -> str:
    return hashlib.sha256(account_id.encode("utf-8")).hexdigest()


def _value_mg_dl(item: dict[str, Any]) -> int:
    for key in ("ValueInMgPerDl", "valueInMgPerDl", "valueInMgDl", "ValueInMgDl", "value_mg_dl"):
        if key in item and item[key] is not None:
            return int(round(float(item[key])))
    if "Value" in item and item.get("GlucoseUnits") == 1:
        return int(round(float(item["Value"])))
    if "value" in item:
        value = float(item["value"])
        if value < 40:
            return int(round(value * 18.0182))
        return int(round(value))
    raise ValueError("payload has no glucose value in mg/dL")


def _redacted_raw(item: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "ValueInMgPerDl",
        "valueInMgPerDl",
        "Value",
        "value",
        "TrendArrow",
        "trendArrow",
        "TrendMessage",
        "MeasurementColor",
        "GlucoseUnits",
        "Timestamp",
        "timestamp",
        "FactoryTimestamp",
        "UnixTimestamp",
        "type",
    }
    return {key: item[key] for key in allowed if key in item}


def map_glucose_item(
    item: dict[str, Any],
    *,
    adapter: str,
    source_patient_id: str | None,
    received_at: datetime,
    stored_at: datetime,
    sample_type: str,
    selected_region_host: str | None,
    source_timezone,
) -> GlucoseReading:
    measured_at, ts_source = pick_measurement_timestamp(item, source_timezone)
    source_patient_hash = stable_hash(source_patient_id) if source_patient_id else None
    sensor = item.get("SensorId") or item.get("sensorId") or item.get("serialNumber")
    trend_raw = item.get("TrendArrow", item.get("trendArrow"))
    return GlucoseReading(
        adapter=adapter,
        value_mg_dl=_value_mg_dl(item),
        trend=item.get("TrendMessage") or item.get("trendMessage"),
        trend_raw=trend_raw,
        sample_type=sample_type,
        measured_at=measured_at,
        received_at=received_at,
        stored_at=stored_at,
        provenance=Provenance(
            adapter=adapter,
            vendor="abbott_libreview",
            source="libre_linkup_follower",
            source_patient_hash=source_patient_hash,
            sensor_hash=stable_hash(str(sensor)) if sensor else None,
            measurement_timestamp_source=ts_source,
            received_timestamp_source="collector_clock",
            selected_region_host=selected_region_host,
        ),
        raw=_redacted_raw(item),
    ).with_id()


class LibreLinkUpClient:
    def __init__(
        self,
        *,
        email: str,
        password: str,
        version: str = "4.16.0",
        product: str = "llu.android",
        base_url: str = "https://api.libreview.io",
        timeout_seconds: int = 30,
    ):
        self.email = email
        self.password = password
        self.version = version
        self.product = product
        self.base_url = validate_libreview_base_url(base_url)
        self.timeout_seconds = timeout_seconds
        self.token: str | None = None
        self.account_id: str | None = None
        self._opener = build_opener(NoRedirectHandler)

    def _headers(self, authenticated: bool = False) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "version": self.version,
            "product": self.product,
        }
        if authenticated:
            if not self.token:
                raise LibreLinkUpError("not_authenticated", "LibreLinkUp client is not authenticated.")
            headers["authorization"] = f"Bearer {self.token}"
            if self.account_id:
                headers["Account-Id"] = libreview_account_id_hash(self.account_id)
        return headers

    def _request_json(self, method: str, path: str, body: dict[str, Any] | None = None, authenticated: bool = False) -> dict[str, Any]:
        url = urljoin(self.base_url, path)
        validate_libreview_base_url(url)
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(url, data=data, headers=self._headers(authenticated), method=method)
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                return self._decode_response(response)
        except HTTPError as exc:
            retryable = exc.code in {429, 430, 500, 502, 503, 504}
            raise LibreLinkUpError("upstream_http_error", f"LibreLinkUp HTTP {exc.code}.", retryable=retryable) from exc
        except URLError as exc:
            raise LibreLinkUpError("upstream_network_error", str(exc.reason), retryable=True) from exc

    @staticmethod
    def _decode_response(response: HTTPResponse) -> dict[str, Any]:
        payload = response.read().decode("utf-8")
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise LibreLinkUpError("invalid_json", "LibreLinkUp returned invalid JSON.", retryable=False) from exc

    def login(self) -> None:
        payload = {"email": self.email, "password": self.password}
        response = self._request_json("POST", "/llu/auth/login", payload, authenticated=False)
        data = response.get("data") or response.get("result") or {}
        if data.get("redirect") and data.get("region"):
            self.base_url = region_host(str(data["region"]))
            response = self._request_json("POST", "/llu/auth/login", payload, authenticated=False)
            data = response.get("data") or response.get("result") or {}
        auth_ticket = data.get("authTicket") or data.get("AuthTicket") or {}
        token = auth_ticket.get("token") or data.get("UserToken")
        user = data.get("user") or data
        account_id = user.get("id") or user.get("AccountId")
        if not token or not account_id:
            raise LibreLinkUpError("auth_failed", "LibreLinkUp login did not return a token and account id.")
        self.token = token
        self.account_id = str(account_id)

    def connections(self) -> dict[str, Any]:
        if not self.token:
            self.login()
        return self._request_json("GET", "/llu/connections", authenticated=True)

    def graph(self, patient_id: str, minutes: int = 720) -> dict[str, Any]:
        if not self.token:
            self.login()
        return self._request_json("GET", f"/llu/connections/{patient_id}/graph?minutes={int(minutes)}", authenticated=True)


class LibreLinkUpAdapter:
    name = "libre_linkup"

    def __init__(self, config: AppConfig, client: LibreLinkUpClient | None = None):
        self.config = config
        self.client = client

    def supports(self, metric: str) -> bool:
        return metric == "glucose"

    def _client(self) -> LibreLinkUpClient:
        if self.client:
            return self.client
        if not self.config.libre_email:
            raise LibreLinkUpError("missing_credentials", "LIBRELINKUP_EMAIL is not configured.")
        password = get_libre_password(self.config.libre_email)
        if not password:
            raise LibreLinkUpError(
                "missing_credentials",
                "LibreLinkUp password is not in the OS keychain and LIBRELINKUP_PASSWORD is not set.",
            )
        self.client = LibreLinkUpClient(
            email=self.config.libre_email,
            password=password,
            version=self.config.libre_version,
            product=self.config.libre_product,
            base_url=self.config.libre_base_url,
        )
        return self.client

    def collect(self) -> CollectionResult:
        started = utc_now()
        readings: list[GlucoseReading] = []
        metadata: dict[str, object] = {}
        try:
            client = self._client()
            connections_payload = client.connections()
            received_at = utc_now()
            stored_at = received_at
            data = connections_payload.get("data") or connections_payload.get("result") or []
            connections = data if isinstance(data, list) else data.get("connections", [])
            if not connections:
                raise LibreLinkUpError("no_connections", "LibreLinkUp returned no followed connections.")
            connection = connections[0]
            patient_id = str(connection.get("patientId") or connection.get("id") or connection.get("PatientId"))
            metadata["selected_region_host"] = client.base_url
            metadata["connection_count"] = len(connections)

            current = connection.get("glucoseMeasurement") or connection.get("glucoseItem")
            if isinstance(current, dict):
                readings.append(
                    map_glucose_item(
                        current,
                        adapter=self.name,
                        source_patient_id=patient_id,
                        received_at=received_at,
                        stored_at=stored_at,
                        sample_type="current",
                        selected_region_host=client.base_url,
                        source_timezone=self.config.source_tzinfo,
                    )
                )

            graph_payload = client.graph(patient_id)
            graph_data = (graph_payload.get("data") or graph_payload.get("result") or {}).get("graphData", [])
            if isinstance(graph_data, list):
                for item in graph_data:
                    if not isinstance(item, dict):
                        continue
                    try:
                        readings.append(
                            map_glucose_item(
                                item,
                                adapter=self.name,
                                source_patient_id=patient_id,
                                received_at=received_at,
                                stored_at=stored_at,
                                sample_type="history",
                                selected_region_host=client.base_url,
                                source_timezone=self.config.source_tzinfo,
                            )
                        )
                    except ValueError:
                        continue
            finished = utc_now()
            return CollectionResult(
                adapter=self.name,
                started_at=started,
                finished_at=finished,
                readings=readings,
                status="ok",
                metadata=metadata,
            )
        except LibreLinkUpError as exc:
            finished = utc_now()
            return CollectionResult(
                adapter=self.name,
                started_at=started,
                finished_at=finished,
                status="error",
                errors=[ErrorInfo(exc.code, str(exc), retryable=exc.retryable)],
                metadata=metadata,
            )
        except Exception as exc:
            finished = utc_now()
            return CollectionResult(
                adapter=self.name,
                started_at=started,
                finished_at=finished,
                status="error",
                errors=[ErrorInfo("adapter_error", str(exc), retryable=False)],
                metadata=metadata,
            )
