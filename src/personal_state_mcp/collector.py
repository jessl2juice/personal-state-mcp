from __future__ import annotations

from datetime import datetime, timedelta, timezone
import time

from .adapters.base import HealthAdapter
from .storage import StateStore


class Collector:
    def __init__(self, store: StateStore, adapters: list[HealthAdapter], min_poll_interval_seconds: int = 60):
        self.store = store
        self.adapters = adapters
        self.min_poll_interval_seconds = min_poll_interval_seconds

    def collect_once(self) -> list[dict[str, object]]:
        summaries: list[dict[str, object]] = []
        for adapter in self.adapters:
            result = adapter.collect()
            glucose_inserted = self.store.upsert_glucose_readings(result.readings)
            health_result = self.store.import_health_observations(result.observations)
            inserted = glucose_inserted + health_result["inserted"]
            first_error = result.errors[0] if result.errors else None
            self.store.record_collector_run(
                adapter=result.adapter,
                started_at=result.started_at,
                finished_at=result.finished_at,
                status=result.status,
                readings_seen=len(result.readings) + len(result.observations),
                readings_inserted=inserted,
                error_code=first_error.code if first_error else None,
                error_message=first_error.message if first_error else None,
                metadata=result.metadata,
            )
            summaries.append(
                {
                    "adapter": result.adapter,
                    "status": result.status,
                    "readings_seen": len(result.readings),
                    "readings_inserted": glucose_inserted,
                    "observations_seen": len(result.observations),
                    "observations_inserted": health_result["inserted"],
                    "observations_duplicates": health_result["duplicates"],
                    "errors": [error.public_dict() for error in result.errors],
                    "metadata": result.metadata,
                }
            )
        return summaries

    def collect_loop(self) -> None:
        while True:
            self.collect_once()
            time.sleep(self.min_poll_interval_seconds)

    def should_collect_after(self, last_finished_at: datetime | None, now: datetime | None = None) -> bool:
        if last_finished_at is None:
            return True
        now = now or datetime.now(timezone.utc)
        return now - last_finished_at >= timedelta(seconds=self.min_poll_interval_seconds)

