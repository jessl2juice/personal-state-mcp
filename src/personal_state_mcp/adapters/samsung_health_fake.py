from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any
from uuid import UUID, uuid5


FAKE_NAMESPACE = UUID("1f615e1e-a64c-4d85-ab9a-55b91706a1db")
FAKE_IDENTITY_NAMESPACE = "1fa40afd-eb95-4267-a8e8-7941cf751597"
FAKE_ADAPTER = {"id": "synthetic_test", "version": "samsung-fixture-v1"}


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _hash(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def build_fake_samsung_batch(now: datetime | None = None) -> dict[str, Any]:
    """Build deterministic non-production Samsung-shaped data for contract tests and demos."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    start = now - timedelta(hours=7, minutes=20)
    association = _hash("fake-sleep-association")
    summary_hash = _hash("fake-sleep-summary")
    score_hash = _hash("fake-sleep-score")
    observed = _iso(now)
    attribution = {
        "state": "watch_confirmed",
        "evidence": "Synthetic Galaxy Watch5 Pro SM-R920 fixture; never accepted by production ingestion.",
        "device_type": "watch",
        "device_model": "SM-R920",
    }
    adapter = dict(FAKE_ADAPTER)
    observations = [
        {
            "adapter": adapter,
            "record_hash": summary_hash,
            "association_hash": association,
            "metric": "sleep.summary",
            "record_kind": "session",
            "start_at": _iso(start),
            "end_at": observed,
            "start_zone_offset": "-07:00",
            "end_zone_offset": "-07:00",
            "observed_by_companion_at": observed,
            "upstream_last_modified_at": observed,
            "source_package": "com.sec.android.app.shealth",
            "recording_method": "automatic",
            "attribution": attribution,
            "payload": {"duration_minutes": 440, "session_record_hashes": []},
        },
        {
            "adapter": adapter,
            "record_hash": score_hash,
            "association_hash": association,
            "metric": "sleep.score",
            "record_kind": "point",
            "measured_at": observed,
            "zone_offset": "-07:00",
            "observed_by_companion_at": observed,
            "upstream_last_modified_at": observed,
            "source_package": "com.sec.android.app.shealth",
            "recording_method": "automatic",
            "attribution": attribution,
            "payload": {"value": 84, "unit": "score"},
        },
    ]
    availability = []
    for metric in ("sleep.summary", "sleep.score"):
        availability.append(
            {
                "adapter": adapter,
                "metric": metric,
                "state": "available",
                "evidence": "Synthetic fixture returned a record.",
                "coverage_checked_at": observed,
                "coverage": {
                    "window_start": _iso(now - timedelta(days=30)),
                    "window_end": observed,
                    "truncated": False,
                    "backfill_limited": True,
                    "interrupted": False,
                    "reconciling": False,
                },
            }
        )
    return {
        "schema_version": "personal-state-watch-batch/v2",
        "adapter": adapter,
        "identity_namespace_id": FAKE_IDENTITY_NAMESPACE,
        "batch_id": str(uuid5(FAKE_NAMESPACE, observed)),
        "installation_id": "synthetic_fixture_installation_0001",
        "generated_at": observed,
        "changes": [
            {
                "operation": "upsert",
                "source_type": "SyntheticSleepType",
                "changed_at": observed,
                "adapter": adapter,
                "observations": observations,
                "deletions": [],
                "association_manifest": {
                    "adapter": adapter,
                    "association_hash": association,
                    "members": [
                        {"metric": item["metric"], "record_hash": item["record_hash"]}
                        for item in observations
                    ],
                },
            }
        ],
        "availability": availability,
    }
