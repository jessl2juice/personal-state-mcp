from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
from zoneinfo import ZoneInfo


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def _optional_int_env(name: str) -> int | None:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return None
    return int(value)


def _csv_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.environ.get(name)
    if value is None:
        return default
    parsed = tuple(part.strip() for part in value.split(",") if part.strip())
    return parsed or default


def default_data_dir() -> Path:
    override = os.environ.get("PERSONAL_STATE_MCP_DATA_DIR")
    if override:
        return Path(override).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "PersonalStateMCP"
    return Path.home() / ".local" / "share" / "personal-state-mcp"


@dataclass(frozen=True)
class AppConfig:
    db_path: Path
    host_id: str
    allowed_hosts: tuple[str, ...]
    rate_limit_per_minute: int
    opportunistic_refresh_enabled: bool
    min_poll_interval_seconds: int
    glucose_threshold_mg_dl: int
    near_threshold_margin_mg_dl: int
    fresh_max_age_seconds: int
    recent_max_age_seconds: int
    future_skew_seconds: int
    retention_days: int | None
    source_timezone: str | None
    libre_email: str | None
    libre_version: str
    libre_product: str
    libre_base_url: str
    watch_enabled: bool = False
    watch_ingest_hosts: tuple[str, ...] = ()
    watch_device_id: str | None = None
    watch_device_secret: str | None = None
    watch_identifier_key: str | None = None
    watch_retention_days: int = 3650
    watch_live_polling_enabled: bool = False
    google_health_enabled: bool = False
    google_health_sync_interval_seconds: int = 300
    google_health_recent_hours: int = 36
    google_health_request_timeout_seconds: int = 12
    google_health_collect_budget_seconds: int = 35
    fitbit_ble_enabled: bool = False
    fitbit_ble_device_address: str | None = None
    fitbit_ble_name_contains: str | None = None
    watch_mcp_metrics: tuple[str, ...] = (
        "activity.steps",
        "activity.exercise_session",
        "vitals.heart_rate",
        "vitals.oxygen_saturation",
        "sleep.session",
        "vitals.resting_heart_rate",
        "vitals.heart_rate_variability",
        "vitals.respiratory_rate",
        "vitals.skin_temperature",
        "activity.active_zone_minutes",
        "activity.total_calories",
    )

    @property
    def source_tzinfo(self):
        if self.source_timezone:
            return ZoneInfo(self.source_timezone)
        return None


def load_config() -> AppConfig:
    data_dir = default_data_dir()
    db_override = os.environ.get("PERSONAL_STATE_MCP_DB")
    db_path = Path(db_override).expanduser() if db_override else data_dir / "state.db"
    watch_device_id = os.environ.get("PERSONAL_STATE_WATCH_DEVICE_ID")
    watch_device_secret = os.environ.get("PERSONAL_STATE_WATCH_DEVICE_SECRET")
    watch_identifier_key = os.environ.get("PERSONAL_STATE_WATCH_IDENTIFIER_KEY")
    if watch_device_id and (not watch_device_secret or not watch_identifier_key):
        try:
            from .secrets import get_watch_device_secret, get_watch_identifier_key

            watch_device_secret = watch_device_secret or get_watch_device_secret(watch_device_id)
            watch_identifier_key = watch_identifier_key or get_watch_identifier_key()
        except Exception:
            pass
    google_health_enabled_env = os.environ.get("PERSONAL_STATE_GOOGLE_HEALTH_ENABLED")
    google_health_connected = False
    if google_health_enabled_env is None:
        try:
            from .secrets import get_google_health_credentials

            google_health_connected = get_google_health_credentials() is not None
        except Exception:
            pass
    return AppConfig(
        db_path=db_path,
        host_id=os.environ.get("PERSONAL_STATE_MCP_HOST_ID", "default-local"),
        allowed_hosts=_csv_env("PERSONAL_STATE_MCP_ALLOWED_HOSTS", ("default-local",)),
        rate_limit_per_minute=_int_env("PERSONAL_STATE_MCP_RATE_LIMIT_PER_MINUTE", 30),
        opportunistic_refresh_enabled=_bool_env("PERSONAL_STATE_MCP_OPPORTUNISTIC_REFRESH", False),
        min_poll_interval_seconds=_int_env("PERSONAL_STATE_MCP_MIN_POLL_SECONDS", 60),
        glucose_threshold_mg_dl=_int_env("PERSONAL_STATE_MCP_GLUCOSE_THRESHOLD", 80),
        near_threshold_margin_mg_dl=_int_env("PERSONAL_STATE_MCP_NEAR_THRESHOLD_MARGIN", 10),
        fresh_max_age_seconds=_int_env("PERSONAL_STATE_MCP_FRESH_SECONDS", 600),
        recent_max_age_seconds=_int_env("PERSONAL_STATE_MCP_RECENT_SECONDS", 1800),
        future_skew_seconds=_int_env("PERSONAL_STATE_MCP_FUTURE_SKEW_SECONDS", 300),
        retention_days=_optional_int_env("PERSONAL_STATE_MCP_RETENTION_DAYS"),
        source_timezone=os.environ.get("PERSONAL_STATE_MCP_SOURCE_TIMEZONE"),
        libre_email=os.environ.get("LIBRELINKUP_EMAIL"),
        libre_version=os.environ.get("LIBRELINKUP_VERSION", "4.16.0"),
        libre_product=os.environ.get("LIBRELINKUP_PRODUCT", "llu.android"),
        libre_base_url=os.environ.get("LIBRELINKUP_BASE_URL", "https://api.libreview.io"),
        watch_enabled=_bool_env("PERSONAL_STATE_WATCH_ENABLED", False),
        watch_ingest_hosts=_csv_env("PERSONAL_STATE_WATCH_INGEST_HOSTS", ()),
        watch_device_id=watch_device_id,
        watch_device_secret=watch_device_secret,
        watch_identifier_key=watch_identifier_key,
        watch_retention_days=_int_env("PERSONAL_STATE_WATCH_RETENTION_DAYS", 3650),
        watch_live_polling_enabled=_bool_env("PERSONAL_STATE_WATCH_LIVE_POLLING_ENABLED", False),
        google_health_enabled=_bool_env("PERSONAL_STATE_GOOGLE_HEALTH_ENABLED", google_health_connected),
        google_health_sync_interval_seconds=_int_env("PERSONAL_STATE_GOOGLE_HEALTH_SYNC_SECONDS", 300),
        google_health_recent_hours=_int_env("PERSONAL_STATE_GOOGLE_HEALTH_RECENT_HOURS", 36),
        google_health_request_timeout_seconds=_int_env("PERSONAL_STATE_GOOGLE_HEALTH_REQUEST_TIMEOUT_SECONDS", 12),
        google_health_collect_budget_seconds=_int_env("PERSONAL_STATE_GOOGLE_HEALTH_COLLECT_BUDGET_SECONDS", 35),
        fitbit_ble_enabled=_bool_env("PERSONAL_STATE_FITBIT_BLE_ENABLED", False),
        fitbit_ble_device_address=os.environ.get("PERSONAL_STATE_FITBIT_BLE_ADDRESS"),
        fitbit_ble_name_contains=os.environ.get("PERSONAL_STATE_FITBIT_BLE_NAME"),
        watch_mcp_metrics=_csv_env(
            "PERSONAL_STATE_WATCH_MCP_METRICS",
            (
                "activity.steps",
                "activity.exercise_session",
                "vitals.heart_rate",
                "vitals.oxygen_saturation",
                "sleep.session",
                "vitals.resting_heart_rate",
                "vitals.heart_rate_variability",
                "vitals.respiratory_rate",
                "vitals.skin_temperature",
                "activity.active_zone_minutes",
                "activity.total_calories",
            ),
        ),
    )
