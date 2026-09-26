# Fable Review

Reviewer role: Fable

Reviewed artifact: `docs/design.md`

## Findings

### P1: Selective access is mostly advisory, not enforced

The design says agents should only call the MCP when context is relevant, but the server needs concrete enforcement. Add local access policy, audit logging, refresh throttles, and default "no opportunistic refresh unless enabled."

### P1: Regional redirect handling could leak credentials unless constrained

The Libre adapter should not blindly follow arbitrary redirect destinations or region values. Require HTTPS, expected LibreView host patterns, no automatic credential forwarding across HTTP redirects, and explicit region-host logging.

### P1: Time semantics are underspecified

Freshness needs precise timestamp normalization rules, especially for vendor local timestamps, timezone-less values, DST, clock skew, and receipt-time fallback.

### P2: Threshold context needs exact semantics and stale-data gating

Define below, near, and above threshold bands. Suppress or soften threshold classification when the latest reading is stale or unavailable.

### P2: Long-term retention is too open-ended

V1 should include at least local export/delete and configurable retention controls, even if the default is long-lived history.

### P2: MCP response schemas are not concrete enough

Add exact JSON envelope semantics, nullable fields, enum values, error shapes, and units for success, stale, unavailable, authorization failure, and partial-history cases.

## Verdict

Not approved to build until the P1 findings are resolved and the P2 clarifications are reflected in the design.

