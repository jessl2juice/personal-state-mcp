from __future__ import annotations

from typing import Any

from .watch_contract import FITBIT_HEALTH_CONNECT_ADAPTER_ID


WATCH_SOURCE_GROUPS: dict[str, dict[str, Any]] = {
    "galaxy_direct_live": {
        "label": "Direct Galaxy Watch",
        "source_class": "direct_live_vital",
        "adapter_ids": ("wear_health_services",),
        "limitations": ["Live applies only when the direct watch sample is within the live cutoff."],
    },
    "samsung_health_history": {
        "label": "Samsung Health history",
        "source_class": "recorded_vendor_history",
        "adapter_ids": ("android_samsung_health_data",),
        "limitations": ["Samsung Health records are recorded history, not direct live data."],
    },
    "health_connect_history": {
        "label": "Health Connect history",
        "source_class": "recorded_health_connect_history",
        "adapter_ids": ("android_health_connect",),
        "limitations": ["Health Connect records are grouped separately because their app origin may vary by metric."],
    },
    "google_health_sync": {
        "label": "Google Health synchronized wearable history",
        "source_class": "synchronized_wearable_history",
        "adapter_ids": ("google_health_fitbit",),
        "limitations": ["Google Health/Fitbit records are synchronized history and are never direct live data."],
    },
    "fitbit_health_connect": {
        "label": "Fitbit via Health Connect",
        "source_class": "phone_synchronized_fitbit_history",
        "adapter_ids": (FITBIT_HEALTH_CONNECT_ADAPTER_ID,),
        "limitations": ["Fitbit Health Connect records depend on phone and Fitbit app sync cadence; Casey biofeedback requires a fresh Fitbit heart-rate record."],
    },
    "historical_imports": {
        "label": "Historical imports",
        "source_class": "historical_import",
        "adapter_ids": ("google_fit_takeout",),
        "limitations": ["Imported archive records cannot occupy a live/current card."],
    },
}


WATCH_SOURCE_FILTERS: dict[str, tuple[str, ...]] = {
    **{key: tuple(spec["adapter_ids"]) for key, spec in WATCH_SOURCE_GROUPS.items()},
    "galaxy": ("wear_health_services", "android_samsung_health_data", "android_health_connect"),
    "fitbit": ("google_health_fitbit", FITBIT_HEALTH_CONNECT_ADAPTER_ID),
}


def adapter_ids_for_watch_source(source: str | None) -> tuple[str, ...] | None:
    if source is None or not source.strip():
        return None
    return WATCH_SOURCE_FILTERS.get(source.strip())


def public_watch_source_filters() -> list[str]:
    return sorted(WATCH_SOURCE_FILTERS)
