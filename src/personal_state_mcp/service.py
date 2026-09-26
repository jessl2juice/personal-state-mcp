from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .access import AccessPolicy
from .collector import Collector
from .config import AppConfig
from .freshness import FreshnessPolicy, classify_freshness, classify_threshold
from .models import ErrorInfo, GlucoseReading, HealthObservation, ResponseEnvelope, SAFETY_NOTICE, WATCH_SAFETY_NOTICE, utc_now
from .storage import StateStore
from .watch_contract import MAX_AGENT_HOURS, MAX_AGENT_PAGE, WatchContractError, decode_cursor, encode_cursor, observation_recency


CLINICAL_FINDING_METRICS = {
    "cardiac.irregular_rhythm_notification",
    "sleep.apnea_detected_sign",
}


EMPTY_PROVENANCE = {
    "adapter": None,
    "vendor": None,
    "source": None,
    "source_patient_hash": None,
    "sensor_hash": None,
    "measurement_timestamp_source": None,
    "received_timestamp_source": None,
    "selected_region_host": None,
}


class HealthService:
    def __init__(
        self,
        *,
        config: AppConfig,
        store: StateStore,
        collector: Collector | None = None,
        clock=utc_now,
    ):
        self.config = config
        self.store = store
        self.collector = collector
        self.clock = clock
        self.access_policy = AccessPolicy(config)

    def _freshness_policy(self) -> FreshnessPolicy:
        return FreshnessPolicy(
            fresh_max_age_seconds=self.config.fresh_max_age_seconds,
            recent_max_age_seconds=self.config.recent_max_age_seconds,
            future_skew_seconds=self.config.future_skew_seconds,
        )

    def _deny_envelope(self, tool: str, error: ErrorInfo) -> dict[str, Any]:
        return ResponseEnvelope(
            ok=False,
            tool=tool,
            generated_at=self.clock(),
            data={},
            freshness=classify_freshness(None, self.clock(), self._freshness_policy()),
            provenance=EMPTY_PROVENANCE,
            safety=SAFETY_NOTICE,
            errors=[error],
        ).public_dict()

    def _check_access(self, tool: str) -> ErrorInfo | None:
        now = self.clock()
        decision = self.access_policy.check(self.store, self.config.host_id, now)
        self.store.record_access(self.config.host_id, tool, decision.decision, decision.reason, now)
        if decision.allowed:
            return None
        return ErrorInfo(decision.reason, f"Health data access was {decision.decision}: {decision.reason}.", retryable=False)

    def _maybe_refresh(self) -> list[ErrorInfo]:
        if not self.config.opportunistic_refresh_enabled or not self.collector:
            return []
        latest = self.store.latest_glucose()
        freshness = classify_freshness(latest, self.clock(), self._freshness_policy())
        if freshness["status"] in {"fresh", "recent"}:
            return []
        summaries = self.collector.collect_once()
        errors: list[ErrorInfo] = []
        for summary in summaries:
            for error in summary.get("errors", []):
                errors.append(ErrorInfo(error["code"], error["message"], error.get("retryable", False)))
        return errors

    def glucose(self) -> dict[str, Any]:
        tool = "health.glucose"
        access_error = self._check_access(tool)
        if access_error:
            return self._deny_envelope(tool, access_error)
        refresh_errors = self._maybe_refresh()
        reading = self.store.latest_glucose()
        now = self.clock()
        freshness = classify_freshness(reading, now, self._freshness_policy())
        threshold = classify_threshold(
            reading,
            freshness,
            self.config.glucose_threshold_mg_dl,
            self.config.near_threshold_margin_mg_dl,
        )
        errors = refresh_errors[:]
        if reading is None:
            errors.append(ErrorInfo("no_glucose_data", "No glucose reading is stored.", retryable=True))
        return ResponseEnvelope(
            ok=reading is not None,
            tool=tool,
            generated_at=now,
            data={
                "glucose": reading.public_dict() if reading else None,
                "threshold_context": threshold,
            },
            freshness=freshness,
            provenance=reading.provenance.public_dict() if reading else EMPTY_PROVENANCE,
            safety=SAFETY_NOTICE,
            errors=errors,
        ).public_dict()

    def glucose_recent(self, hours: float = 3, limit: int = 96) -> dict[str, Any]:
        tool = "health.glucose_recent"
        access_error = self._check_access(tool)
        if access_error:
            return self._deny_envelope(tool, access_error)
        hours = max(0.25, min(float(hours), 24 * 365))
        limit = max(1, min(int(limit), 5000))
        readings = self.store.recent_glucose(hours=hours, limit=limit)
        latest = readings[-1] if readings else self.store.latest_glucose()
        now = self.clock()
        freshness = classify_freshness(latest, now, self._freshness_policy())
        gaps = self._gaps(readings)
        returned_range = {
            "start": readings[0].public_dict()["measured_at"] if readings else None,
            "end": readings[-1].public_dict()["measured_at"] if readings else None,
        }
        return ResponseEnvelope(
            ok=bool(readings),
            tool=tool,
            generated_at=now,
            data={
                "requested_range": {
                    "hours": hours,
                    "limit": limit,
                },
                "returned_range": returned_range,
                "readings": [reading.public_dict() for reading in readings],
                "gaps": gaps,
                "partial_history": len(readings) >= limit,
                "local_history_note": "Local persisted history may extend beyond the vendor upstream history window.",
            },
            freshness=freshness,
            provenance=latest.provenance.public_dict() if latest else EMPTY_PROVENANCE,
            safety=SAFETY_NOTICE,
            errors=[] if readings else [ErrorInfo("no_recent_glucose_data", "No recent glucose readings are stored.", retryable=True)],
        ).public_dict()

    def context(self) -> dict[str, Any]:
        tool = "health.context"
        access_error = self._check_access(tool)
        if access_error:
            return self._deny_envelope(tool, access_error)
        reading = self.store.latest_glucose()
        now = self.clock()
        freshness = classify_freshness(reading, now, self._freshness_policy())
        threshold = classify_threshold(
            reading,
            freshness,
            self.config.glucose_threshold_mg_dl,
            self.config.near_threshold_margin_mg_dl,
        )
        if threshold["state"] in {"below", "near"}:
            wording = (
                "Physiological context may be relevant. Mention the measurement time and suggest checking "
                "the official Libre app/sensor if the user feels unwell."
            )
        elif threshold["state"] == "unknown_stale":
            wording = "The latest stored glucose data is stale. Do not interpret it against the threshold."
        elif threshold["state"] == "unavailable":
            wording = "No glucose context is available."
        else:
            wording = "No below-threshold context is indicated by the latest fresh or recent reading."
        return ResponseEnvelope(
            ok=reading is not None,
            tool=tool,
            generated_at=now,
            data={
                "decision_support_threshold_mg_dl": self.config.glucose_threshold_mg_dl,
                "threshold_context": threshold,
                "agent_guidance": {
                    "allowed": [
                        "Use as context for conversation quality.",
                        "Mention freshness and measurement time if referencing the data.",
                        "Suggest checking the official Libre app/sensor for concerns.",
                    ],
                    "forbidden": [
                        "Do not diagnose causality.",
                        "Do not automate treatment.",
                        "Do not give dosing, food, driving, exercise, or medical instructions.",
                    ],
                    "suggested_wording": wording,
                },
                "glucose": reading.public_dict() if reading else None,
            },
            freshness=freshness,
            provenance=reading.provenance.public_dict() if reading else EMPTY_PROVENANCE,
            safety=SAFETY_NOTICE,
            errors=[] if reading else [ErrorInfo("no_glucose_data", "No glucose reading is stored.", retryable=True)],
        ).public_dict()

    def current_state(self) -> dict[str, Any]:
        tool = "health.current_state"
        access_error = self._check_access(tool)
        if access_error:
            return self._deny_envelope(tool, access_error)
        reading = self.store.latest_glucose()
        now = self.clock()
        freshness = classify_freshness(reading, now, self._freshness_policy())
        threshold = classify_threshold(
            reading,
            freshness,
            self.config.glucose_threshold_mg_dl,
            self.config.near_threshold_margin_mg_dl,
        )
        recent = self.store.recent_glucose(hours=3, limit=96)
        watch_summary = self._watch_summary(include_clinical_findings=False) if self.config.watch_enabled else {
            "enabled": False,
            "latest": {},
            "message": "Watch collection is not enabled.",
        }
        return ResponseEnvelope(
            ok=reading is not None,
            tool=tool,
            generated_at=now,
            data={
                "current_glucose": reading.public_dict() if reading else None,
                "threshold_context": threshold,
                "recent_summary": {
                    "count": len(recent),
                    "first_measured_at": recent[0].public_dict()["measured_at"] if recent else None,
                    "last_measured_at": recent[-1].public_dict()["measured_at"] if recent else None,
                    "gaps": self._gaps(recent),
                },
                "watch": watch_summary,
                "limitations": [
                    "This is not an alarm system.",
                    "Libre app/sensor remains the safety alert layer.",
                    "The MCP must not infer diagnosis or treatment.",
                ],
            },
            freshness=freshness,
            provenance=reading.provenance.public_dict() if reading else EMPTY_PROVENANCE,
            safety=SAFETY_NOTICE,
            errors=[] if reading else [ErrorInfo("no_glucose_data", "No glucose reading is stored.", retryable=True)],
        ).public_dict()

    def _observation_dict(self, observation: HealthObservation, now: datetime) -> dict[str, Any]:
        payload = observation.public_dict()
        payload["observation_recency"] = observation_recency(observation, now)
        return payload

    def _watch_summary(self, *, include_clinical_findings: bool = True) -> dict[str, Any]:
        now = self.clock()
        allowed = set(self.config.watch_mcp_metrics)
        if not include_clinical_findings:
            allowed -= CLINICAL_FINDING_METRICS
        latest = self.store.latest_watch_by_metric(allowed)
        return {
            "enabled": True,
            "latest": {metric: self._observation_dict(observation, now) for metric, observation in latest.items()},
            "last_companion_upload": self.store.latest_watch_sync(),
            "exposure_policy": {"allowed_metrics": sorted(allowed), "stress_enabled": "wellness.stress" in allowed},
            "limitations": [
                "Samsung Health origin does not by itself prove a record came from the watch.",
                "Samsung/watch synchronization timing is unknown.",
                "Missing data is not a normal reading or proof of a synchronization failure.",
            ],
        }

    def watch(self) -> dict[str, Any]:
        tool = "health.watch"
        access_error = self._check_access(tool)
        if access_error:
            return self._deny_envelope(tool, access_error)
        now = self.clock()
        if not self.config.watch_enabled:
            return ResponseEnvelope(
                ok=False,
                tool=tool,
                generated_at=now,
                data={"enabled": False, "exposure_policy": {"allowed_metrics": list(self.config.watch_mcp_metrics)}},
                freshness={"status": "unavailable", "reason": "Watch collection is disabled."},
                provenance=EMPTY_PROVENANCE,
                safety=WATCH_SAFETY_NOTICE,
                errors=[ErrorInfo("watch_disabled", "Watch collection is not enabled.", retryable=False)],
            ).public_dict()
        summary = self._watch_summary()
        latest = summary["latest"]
        statuses = [item["observation_recency"]["status"] for item in latest.values()]
        adapter_ids = sorted({item["provenance"]["adapter"] for item in latest.values()})
        return ResponseEnvelope(
            ok=bool(latest),
            tool=tool,
            generated_at=now,
            data=summary,
            freshness={
                "status": "mixed" if len(set(statuses)) > 1 else (statuses[0] if statuses else "unavailable"),
                "reason": "Recency is evaluated per metric; Samsung/watch synchronization state is unknown.",
            },
            provenance={
                "adapter": adapter_ids[0] if len(adapter_ids) == 1 else ("mixed" if adapter_ids else None),
                "adapters": adapter_ids,
                "vendor": "Samsung",
                "source": "Persisted source provenance is included on every observation.",
            },
            safety=WATCH_SAFETY_NOTICE,
            errors=[] if latest else [ErrorInfo("no_watch_data", "No agent-authorized watch observations are stored.", retryable=True)],
        ).public_dict()

    def watch_recent(
        self,
        metric: str,
        hours: float = 24,
        limit: int = 200,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        tool = "health.watch_recent"
        access_error = self._check_access(tool)
        if access_error:
            return self._deny_envelope(tool, access_error)
        now = self.clock()
        if not self.config.watch_enabled:
            return ResponseEnvelope(
                ok=False,
                tool=tool,
                generated_at=now,
                data={},
                freshness={"status": "unavailable", "reason": "Watch collection is disabled."},
                provenance=EMPTY_PROVENANCE,
                safety=WATCH_SAFETY_NOTICE,
                errors=[ErrorInfo("watch_disabled", "Watch collection is not enabled.")],
            ).public_dict()
        allowed = set(self.config.watch_mcp_metrics)
        if metric not in allowed:
            return ResponseEnvelope(
                ok=False,
                tool=tool,
                generated_at=now,
                data={"exposure_policy": {"allowed_metrics": sorted(allowed)}},
                freshness={"status": "unavailable", "reason": "The metric is not authorized for agent access."},
                provenance=EMPTY_PROVENANCE,
                safety=WATCH_SAFETY_NOTICE,
                errors=[ErrorInfo("metric_not_authorized", "This metric is not authorized for agent access.")],
            ).public_dict()
        try:
            hours = float(hours)
            limit = int(limit)
        except (TypeError, ValueError):
            hours, limit = -1, -1
        if not 0.25 <= hours <= MAX_AGENT_HOURS or not 1 <= limit <= MAX_AGENT_PAGE:
            return ResponseEnvelope(
                ok=False,
                tool=tool,
                generated_at=now,
                data={"policy": {"maximum_hours": MAX_AGENT_HOURS, "maximum_page_size": MAX_AGENT_PAGE}},
                freshness={"status": "unavailable", "reason": "The requested history range is outside policy."},
                provenance=EMPTY_PROVENANCE,
                safety=WATCH_SAFETY_NOTICE,
                errors=[ErrorInfo("invalid_range", "Watch history is limited to 30 days and 200 records per page.")],
            ).public_dict()
        if not self.config.watch_identifier_key:
            return ResponseEnvelope(
                ok=False,
                tool=tool,
                generated_at=now,
                data={},
                freshness={"status": "unavailable", "reason": "The watch cursor key is unavailable."},
                provenance=EMPTY_PROVENANCE,
                safety=WATCH_SAFETY_NOTICE,
                errors=[ErrorInfo("watch_not_configured", "Watch agent access is not fully configured.")],
            ).public_dict()

        offset = 0
        if cursor:
            try:
                decoded = decode_cursor(cursor, self.config.watch_identifier_key, now)
                expected = {
                    "host_id": self.config.host_id,
                    "metric": metric,
                    "hours": hours,
                    "policy": sorted(allowed),
                }
                if any(decoded.get(key) != value for key, value in expected.items()):
                    raise WatchContractError("invalid_cursor", "The watch-history cursor does not match this request.")
                offset = int(decoded["offset"])
            except (WatchContractError, KeyError, TypeError, ValueError) as exc:
                return ResponseEnvelope(
                    ok=False,
                    tool=tool,
                    generated_at=now,
                    data={},
                    freshness={"status": "unavailable", "reason": "The history cursor is invalid."},
                    provenance=EMPTY_PROVENANCE,
                    safety=WATCH_SAFETY_NOTICE,
                    errors=[ErrorInfo("invalid_cursor", str(exc))],
                ).public_dict()

        since = now - timedelta(hours=hours)
        observations = self.store.watch_observations(metric=metric, since=since, limit=limit + 1, offset=offset)
        has_more = len(observations) > limit
        page = observations[:limit]
        next_cursor = None
        if has_more:
            next_cursor = encode_cursor(
                {
                    "host_id": self.config.host_id,
                    "metric": metric,
                    "hours": hours,
                    "policy": sorted(allowed),
                    "offset": offset + limit,
                    "expires_at": (now + timedelta(minutes=15)).timestamp(),
                },
                self.config.watch_identifier_key,
            )
        return ResponseEnvelope(
            ok=bool(page),
            tool=tool,
            generated_at=now,
            data={
                "metric": metric,
                "requested_hours": hours,
                "observations": [self._observation_dict(item, now) for item in page],
                "next_cursor": next_cursor,
                "completeness": {"truncated": has_more, "backfill_limited": False, "interrupted": False, "reconciling": False},
                "exposure_policy": {"allowed_metrics": sorted(allowed)},
            },
            freshness=observation_recency(page[0], now) if page else {"status": "unavailable", "reason": "No observations are stored."},
            provenance=(
                page[0].public_dict()["provenance"]
                if page
                else EMPTY_PROVENANCE
            ),
            safety=WATCH_SAFETY_NOTICE,
            errors=[] if page else [ErrorInfo("no_watch_data", "No observations are stored for this metric and range.", retryable=True)],
        ).public_dict()

    @staticmethod
    def _gaps(readings: list[GlucoseReading], max_gap_minutes: int = 30) -> list[dict[str, Any]]:
        gaps: list[dict[str, Any]] = []
        if len(readings) < 2:
            return gaps
        for previous, current in zip(readings, readings[1:]):
            gap_seconds = int((current.measured_at - previous.measured_at).total_seconds())
            if gap_seconds > max_gap_minutes * 60:
                gaps.append(
                    {
                        "from": previous.public_dict()["measured_at"],
                        "to": current.public_dict()["measured_at"],
                        "gap_seconds": gap_seconds,
                    }
                )
        return gaps
