from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .config import AppConfig
from .storage import StateStore


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    decision: str
    reason: str


class AccessPolicy:
    def __init__(self, config: AppConfig):
        self.config = config

    def check(self, store: StateStore, host_id: str, now: datetime | None = None) -> AccessDecision:
        now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if host_id not in set(self.config.allowed_hosts):
            return AccessDecision(False, "denied", "host_id_not_allowed")

        since = now - timedelta(minutes=1)
        count = store.count_allowed_accesses_since(host_id, since)
        if count >= self.config.rate_limit_per_minute:
            return AccessDecision(False, "rate_limited", "per_host_rate_limit_exceeded")
        return AccessDecision(True, "allowed", "host_id_allowed")

