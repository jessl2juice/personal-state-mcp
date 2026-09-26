from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from zoneinfo import ZoneInfo


def _local_timezone():
    return datetime.now().astimezone().tzinfo or timezone.utc


def parse_vendor_timestamp(value: Any, source_timezone: ZoneInfo | None = None) -> tuple[datetime, str]:
    """Return a UTC datetime and a source label for a vendor timestamp."""
    if value is None:
        raise ValueError("missing timestamp")

    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric = numeric / 1000.0
        return datetime.fromtimestamp(numeric, timezone.utc), "unix_timestamp"

    text = str(value).strip()
    if not text:
        raise ValueError("empty timestamp")

    if text.isdigit():
        return parse_vendor_timestamp(float(text), source_timezone)

    normalized = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
        source = "iso_timestamp"
    except ValueError:
        try:
            dt = parsedate_to_datetime(text)
            source = "rfc2822_timestamp"
        except (TypeError, ValueError):
            for pattern in (
                "%m/%d/%Y %I:%M:%S %p",
                "%m/%d/%Y %H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S",
            ):
                try:
                    dt = datetime.strptime(text, pattern)
                    source = "local_timestamp_configured_timezone" if source_timezone else "local_timestamp_machine_timezone"
                    break
                except ValueError:
                    continue
            else:
                raise ValueError(f"unrecognized timestamp: {text!r}") from None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=source_timezone or _local_timezone())
    return dt.astimezone(timezone.utc), source


def pick_measurement_timestamp(payload: dict[str, Any], source_timezone: ZoneInfo | None = None) -> tuple[datetime, str]:
    for key in (
        "unixTimestamp",
        "UnixTimestamp",
        "timestamp",
        "Timestamp",
        "FactoryTimestamp",
        "factoryTimestamp",
        "date",
        "Date",
    ):
        if key in payload and payload[key] not in (None, ""):
            dt, source = parse_vendor_timestamp(payload[key], source_timezone)
            return dt, f"{key}:{source}"
    raise ValueError("payload has no supported measurement timestamp")

