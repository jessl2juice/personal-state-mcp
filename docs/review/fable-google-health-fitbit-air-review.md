# Fable Review: Google Health / Fitbit Air

Review date: 2026-09-29
Disposition: approved after listed changes

## Review summary

The adapter uses the correct strategic API, limits OAuth to read-only activity, health-metric, and sleep bundles, and preserves Personal State's source, freshness, and non-treatment boundaries. The reconciled `google-wearables` stream is the right default because Casey needs wearable physiology rather than phone estimates or manual logs.

## Required changes

### P0: Google-synced heart rate must never inherit the direct-watch live label

Live classification must remain adapter-specific. A fresh Google Health heart-rate timestamp may be recent, but only `wear_health_services` within ten seconds may be live.

### P0: Device attribution must fail closed

The Google/Fitbit platform alone does not prove Fitbit Air. Exact Air attribution requires device evidence on the data point. Missing or different device names must remain `external_device`.

### P1: Casey needs physiological context, not an unrestricted health dump

Fitbit metrics must pass through the existing MCP allowlist. Clinical findings, location, nutrition, ECG, irregular-rhythm notifications, profile, and settings remain excluded unless separately designed and authorized.

### P1: Provider cadence must be visible

Documentation and responses must distinguish measurement time from Google/Fitbit synchronization and Personal State receipt. The dashboard must not imply second-by-second Fitbit delivery.

### P1: Historical import must respect query bounds

Backfill must use bounded windows and idempotent storage. It must not issue an unbounded full-history request or persist raw responses.

### P1: Partial consent and revocation must degrade safely

A denied metric bundle must not disable already authorized bundles. Credentials belong in the OS keychain, and credential deletion must include the Google Health refresh token and OAuth client secret.

## Approval

Approved once the resolution log is satisfied and the complete test suite passes. Live account setup and real Fitbit Air data remain a deployment validation step because source shapes and permissions cannot be proven by synthetic fixtures alone.
