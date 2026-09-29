from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from personal_state_mcp.models import ErrorInfo, GlucoseReading, HealthObservation


@dataclass(frozen=True)
class CollectionResult:
    adapter: str
    started_at: datetime
    finished_at: datetime
    readings: list[GlucoseReading] = field(default_factory=list)
    observations: list[HealthObservation] = field(default_factory=list)
    status: str = "ok"
    errors: list[ErrorInfo] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)


class HealthAdapter(Protocol):
    name: str

    def supports(self, metric: str) -> bool:
        ...

    def collect(self) -> CollectionResult:
        ...

