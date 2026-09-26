# Fable Review Resolution Log

## P1: Selective access is mostly advisory

Resolution: accepted.

Design changes:

- Added a local access policy with host id, allowlist, audit log, and per-host rate limit.
- Made opportunistic refresh disabled by default.
- Added an `access_audit` table.
- Added tests for denied hosts, audit entries, and rate limiting.

Implementation changes:

- `AccessPolicy` checks the host id and rate limit before returning health data.
- `HealthService` records every MCP read attempt in SQLite.
- MCP tools use the configured host id from local config/environment.

## P1: Regional redirect handling could leak credentials

Resolution: accepted.

Design changes:

- Added strict Libre host validation.
- Clarified that the client handles JSON region discovery, not arbitrary HTTP redirects.
- Required disabling automatic HTTP redirects for credential-bearing requests.

Implementation changes:

- `LibreLinkUpClient` accepts only `https://api.libreview.io` or `https://api-<region>.libreview.io`.
- Region codes must match a conservative pattern.
- HTTP redirects are rejected by a no-redirect opener.

## P1: Time semantics are underspecified

Resolution: accepted.

Design changes:

- Added timestamp source precedence and timezone conversion rules.
- Added stale/future timestamp behavior.

Implementation changes:

- `parse_vendor_timestamp` prefers epoch timestamps, then aware ISO timestamps, then configured-source-timezone local timestamps.
- Freshness marks impossible future measurements stale.

## P2: Threshold context needs exact semantics and stale-data gating

Resolution: accepted.

Design changes:

- Defined below, near, above, unknown, and unavailable threshold states.
- Required threshold context to become unknown when data is stale.

Implementation changes:

- `classify_threshold` returns exact states and explanatory text.
- `health.context()` never reports below/near/above from stale readings.

## P2: Long-term retention is too open-ended

Resolution: accepted.

Design changes:

- Added retention days config, export, delete, and pruning behavior.

Implementation changes:

- CLI includes `export-history`, `delete-history`, and `prune-history`.
- Storage implements JSONL export and retention pruning.

## P2: MCP response schemas are not concrete enough

Resolution: accepted.

Design changes:

- Added concrete response envelopes and enum semantics to the design.

Implementation changes:

- Service methods return deterministic JSON-compatible envelopes.
- Tests assert required freshness/provenance/safety fields.

