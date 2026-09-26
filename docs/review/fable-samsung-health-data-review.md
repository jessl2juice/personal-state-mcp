# Fable Review: Samsung Health Data Adapter

Date: 2026-09-26
Reviewer role: Fable
Reviewed artifact: `docs/samsung-health-data-adapter-design.md`, review draft

## Decision

Blocked pending design revision. Implementation may proceed only after the following findings are resolved and re-reviewed.

## Findings

### P1-1: v1 cannot represent two adapters

The proposed adapter identifier and version are incompatible with the closed v1 envelope, current storage, and hardcoded MCP provenance.

Required resolution: define a v2 envelope with adapter identity on observations, deletions, and availability; preserve v1 acceptance; specify migration, exports, rollback, and persisted provenance.

### P1-2: existing agent access would silently widen

Adding richer fields to already allowed sleep and oxygen metrics would expose new Samsung-derived data to agents without a new user decision.

Required resolution: split enriched data into separately authorized metrics or implement field-level projections. Existing default MCP scope must not expand.

### P1-3: record shapes do not preserve Samsung semantics

Samsung blood oxygen is an interval series with minimum and maximum, Energy Score is a local-day value, and one Samsung sleep record can contain multiple sessions and associated measurements.

Required resolution: add property-level mappings that preserve intervals, series, local dates, offsets, multiple sleep sessions, and association identity.

### P1-4: deduplication is not implementable as written

SDK and Health Connect records may use different identifiers. A winning-record model risks false matches and source deletion erasing a surviving record.

Required resolution: persist each source independently; define narrowly scoped equivalence, presentation precedence, update/delete behavior, and types that must never be heuristically merged.

### P1-5: availability is not adapter-specific

The current installation-and-metric key lets one adapter overwrite another. Missing SDK and failed Samsung authorization are also distinct states.

Required resolution: key availability by installation, adapter, and metric; define merged presentation; add `adapter_not_installed`; reserve partnership failure for a positively identified authorization error.

### P1-6: incremental synchronization is underspecified

The draft lacks per-type token, pagination, acknowledgement, expiry, reconciliation, deletion, backfill, and reinstall rules.

Required resolution: define a checkpoint stream per adapter and type, post-ack advancement, bounded reconciliation, tombstones, idempotent replay, and independent failure recovery.

### P2-1: clinical labels exceed provider semantics

Sleep apnea exposes a detected-sign enum, while irregular-rhythm notification exposes only detected or undefined. No arbitrary vendor-reason field is documented.

Required resolution: use only documented enums; label apnea as a Samsung Health Monitor detected sign; absence remains unknown.

### P2-2: prerequisites and freshness need normative values

Required resolution: pin Samsung Health Data SDK 1.1.0, Samsung Health 6.30.2+, Android 10+, Java 17, public package/signature/data-scope approval, and a source-aware freshness matrix. Only direct Wear heart rate at 60 seconds or less may be called live.
