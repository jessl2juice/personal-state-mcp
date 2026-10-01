from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Any

from personal_state_mcp.adapters.base import CollectionResult
from personal_state_mcp.collector import Collector


NOW = datetime(2026, 10, 1, 16, 30, tzinfo=timezone.utc)


class FakeStore:
    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []

    def upsert_glucose_readings(self, readings):
        return len(readings)

    def import_health_observations(self, observations):
        return {"inserted": len(observations), "duplicates": 0}

    def record_collector_run(
        self,
        adapter,
        started_at,
        finished_at,
        status,
        readings_seen,
        readings_inserted,
        error_code=None,
        error_message=None,
        metadata=None,
    ) -> None:
        self.runs.append(
            {
                "adapter": adapter,
                "started_at": started_at,
                "finished_at": finished_at,
                "status": status,
                "readings_seen": readings_seen,
                "readings_inserted": readings_inserted,
                "error_code": error_code,
                "error_message": error_message,
                "metadata": metadata or {},
            }
        )


class FastAdapter:
    name = "libre_linkup"

    def supports(self, metric: str) -> bool:
        return metric == "glucose"

    def collect(self) -> CollectionResult:
        return CollectionResult(adapter=self.name, started_at=NOW, finished_at=NOW, status="ok")


class SlowAdapter:
    name = "google_health_fitbit"

    def supports(self, metric: str) -> bool:
        return True

    def collect(self) -> CollectionResult:
        time.sleep(1)
        return CollectionResult(adapter=self.name, started_at=NOW, finished_at=NOW, status="ok")


def test_collect_once_does_not_let_slow_adapter_block_fast_adapter() -> None:
    store = FakeStore()
    collector = Collector(store, [FastAdapter(), SlowAdapter()], adapter_timeout_seconds=0.05)

    started = time.monotonic()
    summaries = collector.collect_once()
    elapsed = time.monotonic() - started

    by_adapter = {summary["adapter"]: summary for summary in summaries}
    assert elapsed < 0.5
    assert by_adapter["libre_linkup"]["status"] == "ok"
    assert by_adapter["google_health_fitbit"]["status"] == "error"
    assert by_adapter["google_health_fitbit"]["errors"][0]["code"] == "adapter_timeout"
    assert any(run["adapter"] == "libre_linkup" and run["status"] == "ok" for run in store.runs)


def test_collect_once_skips_adapter_still_running_from_prior_cycle() -> None:
    store = FakeStore()
    collector = Collector(store, [FastAdapter(), SlowAdapter()], adapter_timeout_seconds=0.05)

    collector.collect_once()
    summaries = collector.collect_once()

    by_adapter = {summary["adapter"]: summary for summary in summaries}
    assert by_adapter["libre_linkup"]["status"] == "ok"
    assert by_adapter["google_health_fitbit"]["status"] == "skipped"
    assert by_adapter["google_health_fitbit"]["errors"][0]["code"] == "adapter_already_running"
