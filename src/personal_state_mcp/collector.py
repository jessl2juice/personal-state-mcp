from __future__ import annotations

from datetime import datetime, timedelta, timezone
import queue
import threading
import time

from .adapters.base import HealthAdapter
from .adapters.base import CollectionResult
from .models import ErrorInfo, utc_now
from .storage import StateStore


class Collector:
    def __init__(
        self,
        store: StateStore,
        adapters: list[HealthAdapter],
        min_poll_interval_seconds: int = 60,
        adapter_timeout_seconds: float = 45,
    ):
        self.store = store
        self.adapters = adapters
        self.min_poll_interval_seconds = min_poll_interval_seconds
        self.adapter_timeout_seconds = adapter_timeout_seconds
        self._active_adapters: set[str] = set()
        self._active_lock = threading.Lock()

    def collect_once(self) -> list[dict[str, object]]:
        result_queue: queue.Queue[CollectionResult] = queue.Queue()
        pending: set[str] = set()
        summaries_by_adapter: dict[str, dict[str, object]] = {}
        started_at = utc_now()

        for adapter in self.adapters:
            if not self._claim_adapter(adapter.name):
                summaries_by_adapter[adapter.name] = self._record_timeout_or_busy(
                    adapter.name,
                    started_at,
                    status="skipped",
                    code="adapter_already_running",
                    message=f"{adapter.name} collection is still running from an earlier cycle.",
                )
                continue
            pending.add(adapter.name)
            thread = threading.Thread(
                target=self._collect_adapter_thread,
                args=(adapter, result_queue),
                name=f"personal-state-collector-{adapter.name}",
                daemon=True,
            )
            thread.start()

        deadline = time.monotonic() + max(0.01, float(self.adapter_timeout_seconds))
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                result = result_queue.get(timeout=min(0.25, remaining))
            except queue.Empty:
                continue
            if result.adapter not in pending:
                continue
            pending.remove(result.adapter)
            summaries_by_adapter[result.adapter] = self._persist_result(result)

        finished_at = utc_now()
        for adapter_name in sorted(pending):
            summaries_by_adapter[adapter_name] = self._record_timeout_or_busy(
                adapter_name,
                started_at,
                finished_at=finished_at,
                status="error",
                code="adapter_timeout",
                message=f"{adapter_name} did not finish within {self.adapter_timeout_seconds:g} seconds.",
            )

        return [summaries_by_adapter[adapter.name] for adapter in self.adapters if adapter.name in summaries_by_adapter]

    def _collect_adapter_thread(self, adapter: HealthAdapter, result_queue: queue.Queue[CollectionResult]) -> None:
        try:
            result = adapter.collect()
        except Exception as exc:  # pragma: no cover - defensive isolation for production adapters
            now = utc_now()
            result = CollectionResult(
                adapter=adapter.name,
                started_at=now,
                finished_at=now,
                status="error",
                errors=[ErrorInfo("adapter_error", str(exc), retryable=False)],
            )
        finally:
            self._release_adapter(adapter.name)
        result_queue.put(result)

    def _claim_adapter(self, adapter_name: str) -> bool:
        with self._active_lock:
            if adapter_name in self._active_adapters:
                return False
            self._active_adapters.add(adapter_name)
            return True

    def _release_adapter(self, adapter_name: str) -> None:
        with self._active_lock:
            self._active_adapters.discard(adapter_name)

    def _persist_result(self, result: CollectionResult) -> dict[str, object]:
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
        return {
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

    def _record_timeout_or_busy(
        self,
        adapter_name: str,
        started_at: datetime,
        *,
        status: str,
        code: str,
        message: str,
        finished_at: datetime | None = None,
    ) -> dict[str, object]:
        finished_at = finished_at or utc_now()
        error = ErrorInfo(code, message, retryable=True)
        self.store.record_collector_run(
            adapter=adapter_name,
            started_at=started_at,
            finished_at=finished_at,
            status=status,
            readings_seen=0,
            readings_inserted=0,
            error_code=error.code,
            error_message=error.message,
            metadata={"reason": error.code},
        )
        return {
            "adapter": adapter_name,
            "status": status,
            "readings_seen": 0,
            "readings_inserted": 0,
            "observations_seen": 0,
            "observations_inserted": 0,
            "observations_duplicates": 0,
            "errors": [error.public_dict()],
            "metadata": {"reason": error.code},
        }

    def collect_loop(self) -> None:
        while True:
            self.collect_once()
            time.sleep(self.min_poll_interval_seconds)

    def should_collect_after(self, last_finished_at: datetime | None, now: datetime | None = None) -> bool:
        if last_finished_at is None:
            return True
        now = now or datetime.now(timezone.utc)
        return now - last_finished_at >= timedelta(seconds=self.min_poll_interval_seconds)

