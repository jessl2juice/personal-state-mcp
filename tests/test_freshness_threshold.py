from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from personal_state_mcp.freshness import FreshnessPolicy, classify_freshness, classify_threshold
from personal_state_mcp.models import GlucoseReading, Provenance


def reading(value: int, measured_at: datetime, received_at: datetime | None = None) -> GlucoseReading:
    return GlucoseReading(
        adapter="test",
        value_mg_dl=value,
        trend=None,
        trend_raw=None,
        sample_type="current",
        measured_at=measured_at,
        received_at=received_at or measured_at,
        stored_at=received_at or measured_at,
        provenance=Provenance(adapter="test", vendor="synthetic", source="unit_test"),
    ).with_id()


class FreshnessThresholdTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 24, 20, 0, tzinfo=timezone.utc)
        self.policy = FreshnessPolicy(fresh_max_age_seconds=600, recent_max_age_seconds=1800)

    def test_below_threshold_when_fresh(self) -> None:
        sample = reading(79, self.now - timedelta(minutes=2))
        freshness = classify_freshness(sample, self.now, self.policy)
        threshold = classify_threshold(sample, freshness, threshold_mg_dl=80)
        self.assertEqual("fresh", freshness["status"])
        self.assertEqual("below", threshold["state"])

    def test_near_threshold_band(self) -> None:
        sample = reading(85, self.now - timedelta(minutes=12))
        freshness = classify_freshness(sample, self.now, self.policy)
        threshold = classify_threshold(sample, freshness, threshold_mg_dl=80, near_margin_mg_dl=10)
        self.assertEqual("recent", freshness["status"])
        self.assertEqual("near", threshold["state"])

    def test_stale_reading_suppresses_threshold_classification(self) -> None:
        sample = reading(61, self.now - timedelta(hours=2))
        freshness = classify_freshness(sample, self.now, self.policy)
        threshold = classify_threshold(sample, freshness, threshold_mg_dl=80)
        self.assertEqual("stale", freshness["status"])
        self.assertEqual("unknown_stale", threshold["state"])

    def test_future_measurement_is_stale(self) -> None:
        sample = reading(100, self.now + timedelta(minutes=10))
        freshness = classify_freshness(sample, self.now, self.policy)
        self.assertEqual("stale", freshness["status"])
        self.assertEqual("future_measurement_clock_skew", freshness["reason"])


if __name__ == "__main__":
    unittest.main()

