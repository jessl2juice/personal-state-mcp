# Personal State Validation, Release, and Governance

Document version: 1.0  
Last verified: 2026-09-29
Intended readers: maintainers, reviewers, and release operators

## Governance principles

1. Physiological observations remain read-only.
2. The product must not diagnose causality, automate treatment, or replace official safety alerts.
3. Old or missing data must never look current.
4. Agent access must remain narrower than operator or dashboard access.
5. Threshold, permission, credential, retention, and safety changes require explicit user action outside agent tools.
6. New adapters must preserve normalized contracts, provenance, time semantics, and failure honesty.
7. Credentials and real health data must not enter source control, tests, documentation, screenshots, or general logs.

## Design review record

The initial design, Galaxy Watch5 Pro addendum, Samsung Health Data expansion, and Google Health/Fitbit Air adapter received independent rigorous review under the reviewer role name Fable. The reviews identified and resolved issues in:

- Agent authorization and rate limiting.
- Libre redirect and credential-leak risk.
- Timezone and timestamp semantics.
- Stale-data threshold gating.
- Retention and deletion controls.
- Concrete response envelopes.
- Watch attribution honesty.
- Update, delete, checkpoint, and reconciliation behavior.
- Separate ingest authentication and dashboard isolation.
- Route and location exclusion.
- Stress-score non-inference.
- Health Connect capability limits.
- MCP exposure policy.
- Replay, idempotency, and payload validation.

The detailed findings and resolutions remain in the engineering review documents linked from the Documentation Index.

## Current release contents

### Server 0.4.2

- Libre-compatible follower collection.
- SQLite long-term history.
- Read-only MCP tools.
- Private dashboard and CSV exports.
- Signed watch ingest and Health Connect storage.
- Read-only Google Health OAuth, recent synchronization, and historical backfill.
- Google Fit and LibreView historical imports with backup and deduplication.
- Host allowlisting, rate limiting, audit records, and safety envelopes.

### Phone companion 0.4.2

- Android Health Connect read-only collection.
- Encrypted pairing storage.
- Signed ingest.
- Wear OS message and Data Item reception.
- Timestamp deduplication of direct heart-rate transport.
- Full authorized Health Connect history and optional licensed Samsung Health Data reads.

### Watch companion 0.4.2

- Battery-safe passive heart-rate collection plus lease-bounded foreground live collection through Health Services.
- Five-second screen-off batching, no GPS.
- Immediate message plus urgent latest-value Data Item delivery.
- 45-second stalled-stream watchdog and safe exercise restart.
- Persistent notification and visible Start/Stop control.

## Automated verification

Python and web checks:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --check .\src\personal_state_mcp\dashboard_static\app.js
```

Android checks:

```text
:app:testDebugUnitTest
:app:assembleDebug
:app:lintDebug
:wear:assembleDebug
:wear:lintDebug
```

Tests use synthetic data. Instrumented connected-device tests must not run against the paired production application because setup can clear encrypted credentials and Health Connect grants.

## Required acceptance tests

### Data and contracts

- Valid and invalid glucose timestamp formats.
- Fresh, recent, stale, future-skewed, and unavailable readings.
- Exact 80 mg/dL threshold boundaries and stale gating.
- Watch schema variants, units, bounds, duplicates, updates, deletes, and pagination.
- Attribution states and refusal to infer watch origin.
- Stress absence and no derived stress score.
- Cursor tampering, expiry, host binding, metric binding, and policy binding.

### Security and privacy

- Wrong dashboard Host is rejected.
- Ingest host cannot serve dashboard data.
- Missing, wrong, expired, and rotated credentials fail closed.
- Nonce replay and duplicate batch handling.
- Location, route, unknown fields, duplicate JSON keys, excessive depth, and over-size bodies are rejected.
- Logs and errors contain no credentials or measurement payloads.
- Responses use no-store and restrictive browser headers.

### Dashboard

- Desktop and mobile layouts do not overlap or overflow.
- Day is the default; Week, Month, and Year remain selectable.
- Glucose and heart rate remain visually distinct and share a truthful time axis.
- Live heart rate disappears at eleven seconds.
- Historical values are explicitly labeled.
- Heart-rate display smoothing leaves raw data unchanged.
- Gaps through one minute connect; longer gaps break.
- Print and CSV include the selected period and exact values.

### Real-device production verification

- Phone and watch package signatures match.
- Existing phone pairing and first-install identity survive an in-place update.
- Watch sample times advance repeatedly.
- Phone relay times advance repeatedly.
- Server ingest times advance repeatedly.
- Dashboard age remains within ten seconds for at least five minutes, with no flicker between five-second watch deliveries.
- The same verification passes after USB and debugger disconnection.
- Screen-off and ordinary movement do not permanently stop the stream.
- A forced or naturally occurring 45-second stream stall recovers without reinstalling the application.

## Release checklist

1. Review the change against goals, non-goals, privacy, and agent behavior contracts.
2. Update version codes and version names.
3. Build with the approved signing identity.
4. Verify APK certificate fingerprints before installation.
5. Run Python, JavaScript, Android unit, assembly, and lint checks.
6. Review manifests for prohibited write, route, and location permissions.
7. Install in place. Do not uninstall unless reset is intentional.
8. Verify pairing, permissions, and scheduled tasks.
9. Complete real-device freshness testing without USB dependence.
10. Update this documentation set and release record.
11. Preserve a rollback artifact signed by the same identity when schema compatibility permits.

## Change control

The following changes require explicit user approval and a focused security/safety review:

- Changing the glucose context threshold or near margin.
- Adding treatment, alerting, notification, or automated action.
- Adding a health write permission.
- Adding location, exercise route, or raw sensor collection.
- Adding a new external service, analytics system, or public endpoint.
- Expanding agent-accessible metrics.
- Sharing data with another person or organization.
- Changing retention or deleting history.
- Replacing device or Cloudflare credentials.

## Known limitations

- The Libre-compatible programmatic path is unofficial and can change.
- Personal State is not an alert or emergency system.
- The dashboard is not an EHR and has no clinical attestation workflow.
- Consumer devices may have measurement error, delay, and missingness.
- Samsung Health does not export every Watch5 Pro metric to Health Connect.
- Samsung stress, resting heart rate, HRV, skin temperature, floors, and active-time summary are unavailable unless a separately reviewed adapter supplies them.
- The current Windows production tasks depend on a signed-in interactive session.
- Direct live heart monitoring uses additional watch battery only while an MCP or dashboard lease is active, and pauses while another exercise application owns Health Services.
- The system can report companion read and upload status, but it cannot generally prove Samsung or watch synchronization state.

## Adapter policy

The current Google Health/Fitbit and optional Samsung Health Data adapters, plus any future adapter, must:

- Be read-only in the initial release.
- Map into existing normalized observation types.
- Preserve exact source and device attribution without overclaiming.
- Define metric-specific recency and availability states.
- Use OS-protected credentials and least privilege.
- Pass an independent review and documented resolution gate.
- Add synthetic and real-device tests without using real values in fixtures.
- Avoid cross-provider score normalization unless a separate validated clinical design explicitly supports it.

## Documentation maintenance

Update the documents whenever behavior, version, security boundary, freshness rule, supported metric, deployment topology, or known limitation changes. A release is not complete if the working behavior and shared documentation disagree.
