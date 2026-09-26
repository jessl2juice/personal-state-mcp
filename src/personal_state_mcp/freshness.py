from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .models import GlucoseReading


@dataclass(frozen=True)
class FreshnessPolicy:
    fresh_max_age_seconds: int = 600
    recent_max_age_seconds: int = 1800
    future_skew_seconds: int = 300


def _age_seconds(now: datetime, then: datetime | None) -> int | None:
    if then is None:
        return None
    return int((now.astimezone(timezone.utc) - then.astimezone(timezone.utc)).total_seconds())


def classify_freshness(
    reading: GlucoseReading | None,
    now: datetime,
    policy: FreshnessPolicy,
) -> dict[str, Any]:
    if reading is None:
        return {
            "status": "unavailable",
            "reason": "no glucose reading is stored",
            "measurement_age_seconds": None,
            "received_age_seconds": None,
            "max_age_seconds": policy.fresh_max_age_seconds,
        }

    measurement_age = _age_seconds(now, reading.measured_at)
    received_age = _age_seconds(now, reading.received_at)
    if measurement_age is not None and measurement_age < -policy.future_skew_seconds:
        status = "stale"
        reason = "future_measurement_clock_skew"
    elif received_age is not None and received_age < -policy.future_skew_seconds:
        status = "stale"
        reason = "future_received_clock_skew"
    elif measurement_age is None:
        status = "unavailable"
        reason = "missing measurement timestamp"
    elif measurement_age <= policy.fresh_max_age_seconds and (received_age is None or received_age <= policy.fresh_max_age_seconds):
        status = "fresh"
        reason = "latest measurement is within the fresh window"
    elif measurement_age <= policy.recent_max_age_seconds:
        status = "recent"
        reason = "latest measurement is recent but outside the fresh window"
    else:
        status = "stale"
        reason = "latest measurement is older than the recent window"

    return {
        "status": status,
        "reason": reason,
        "measurement_age_seconds": measurement_age,
        "received_age_seconds": received_age,
        "max_age_seconds": policy.fresh_max_age_seconds,
    }


def classify_threshold(
    reading: GlucoseReading | None,
    freshness: dict[str, Any],
    threshold_mg_dl: int = 80,
    near_margin_mg_dl: int = 10,
) -> dict[str, Any]:
    status = freshness.get("status")
    if reading is None:
        return {
            "state": "unavailable",
            "threshold_mg_dl": threshold_mg_dl,
            "value_mg_dl": None,
            "message": "No glucose reading is available.",
        }
    if status not in {"fresh", "recent"}:
        return {
            "state": "unknown_stale",
            "threshold_mg_dl": threshold_mg_dl,
            "value_mg_dl": reading.value_mg_dl,
            "message": "The latest stored glucose data is too old to interpret against the threshold.",
        }
    if reading.value_mg_dl < threshold_mg_dl:
        state = "below"
        message = "Latest glucose is below the configured context threshold."
    elif reading.value_mg_dl < threshold_mg_dl + near_margin_mg_dl:
        state = "near"
        message = "Latest glucose is near the configured context threshold."
    else:
        state = "above"
        message = "Latest glucose is above the configured context threshold."
    return {
        "state": state,
        "threshold_mg_dl": threshold_mg_dl,
        "near_margin_mg_dl": near_margin_mg_dl,
        "value_mg_dl": reading.value_mg_dl,
        "message": message,
    }

