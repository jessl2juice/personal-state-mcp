# Galaxy Watch5 Pro Review Resolution Log

Date: 2026-09-24  
Reviewer: Fable  
Design revision: `docs/watch5-pro-design-addendum.md`, revision 2.1

## Gate

Fable's initial gate was `approved after listed changes`. Re-review 1 remained blocked because revision 2 retained contradictory legacy wording and lacked an exact closed schema. Revision 2.1 removes those contradictions and adds the normative `docs/watch-ingest-schema-v1.json`. Real data, phone permissions, device credentials, and Cloudflare service credentials remain blocked until synthetic verification passes.

## P0 Resolutions

| Finding | Resolution |
| --- | --- |
| P0-1 watch attribution | Added six-state attribution, Samsung-origin filtering, positive-evidence-only `watch_confirmed`, source package/recording method/device evidence, Samsung Health labeling, and mixed-origin tests. |
| P0-2 updates/deletes/checkpoints | Added per-record-type/origin change tokens, pagination, version-aware upserts, tombstones and result exclusion, bounded reconciliation after token expiry, reinstall handling, and full lifecycle tests. |
| P0-3 ingest authentication | Required separate hostname/application/audience/per-device service token; `cloudflared` assertion validation; mandatory per-device HMAC timestamp/nonce/batch signature; replay store; rotation/revocation; fail-closed origin checks and negative tests. |
| P0-4 route risk | Excluded coordinates and location/route fields, omitted route/location permissions, required schema rejection, and deferred any future route support to a separate opt-in review. |

## P1 Resolutions

| Finding | Resolution |
| --- | --- |
| P1-1 capability overstatement | Added dated capability matrix; renamed calories to exercise calories; separated Health Connect availability, Samsung export, and watch attribution; marked stress/resting HR/HRV/skin temperature/floors/active time honestly. |
| P1-2 vague contract | Added a normative closed JSON Schema with typed changes, availability, coverage, observation variants, metric payloads, nullability, unit enums, value/resource bounds, timestamp/offset/version/provenance fields, keyed IDs, and full-batch compatibility behavior. |
| P1-3 stress safety | Stress is availability-only in v1; future values remain provider-specific; no cross-provider normalization or home-grown derivation; no current-state exposure and separate agent opt-in. |
| P1-4 freshness conflation | Separated observation recency, companion read, and server upload; renamed timestamps and lag; neutral sparse/completed states; response-time calculation; skew/timezone rules. |
| P1-5 completeness claims | Defined eight availability states with evidence/checked time and category windows plus `truncated`, `backfill_limited`, `interrupted`, and `reconciling`; no-observation is not a sync diagnosis; static capability and runtime state are separate. |
| P1-6 history/background permissions | Made history/background optional and separate; defined 30-day fallback, foreground usability, feature checks, no write permissions, WorkManager constraints, and bounded backoff. |
| P1-7 phone queue contradiction | Removed payload queue, including schema-mismatch retention. Retry leaves the checkpoint unchanged and rereads Health Connect. Added backup, screenshot, clipboard, crash, and logging exclusions plus the Keystore runtime limitation. |
| P1-8 MCP overexposure | Added separate user exposure allowlist, safe default categories, explicit metric requirement, a hard 30-day range and 200-item page cap, a 15-minute host/metric/range/policy-bound HMAC cursor, and collected-but-not-authorized tests. |
| P1-9 daily totals | Uses configured timezone and Health Connect aggregates, stores aggregate provenance/window separately, and tests midnight/DST/overlap/revision/mixed origins. |
| P1-10 at-rest/retention | Required disk-encryption verification or explicit residual-risk acceptance before real data; explicit 3,650-day watch retention; broad export/delete/backup/restore coverage; no-store and no-health-logging rules. |

## P2 Acceptance Criteria

- Dashboard cards show attribution and all three relevant ages, charts break across gaps, styling is neutral, and Samsung apps remain visibly authoritative.
- Ingest limits are fixed at 1 MiB, 500 changes, 2,000 samples/stages, depth 12, string length 512, 300-second request skew, and 40 authenticated batches/device/minute. The rate ceiling includes bounded headroom for the visible two-second direct heart stream.
- Failure tests cover platform/source absence, token rotation, lost/reinstalled phones, expired change tokens, clocks/timezones, database failures, multiple phones, restarts, and acknowledgement loss.
- Schema migration is versioned, idempotent, backed up, and rollback-safe; watch support is disabled by default until synthetic end-to-end success.
- APK package identity and signing key are stable and documented before installation.
- Privacy/contract tests assert attribution honesty, stress absence, no derived stress, no prohibited Android permissions, no secret/payload logging, deletion propagation, MCP filtering, cross-host/audience rejection, and comprehensive retention deletion.

## Implementation Authorization

The original project request authorized implementation after review resolution. This log records that design gate. It does not authorize real health-data access, Android permission grants, at-rest risk acceptance, or creation of Cloudflare service credentials; those remain user-controlled actions at deployment time.

## Synthetic Implementation Verification

Completed 2026-09-24:

- The Android companion compiles against Android API 36 and Health Connect `1.1.0` with Gradle 8.11.1 and JDK 17.
- Android unit tests, debug APK assembly, and `lintDebug` complete successfully.
- The companion requests read-only Health Connect permissions and no health-write, route, or location permission.
- Samsung-origin daily step totals use Health Connect aggregation in the phone timezone; aggregate attribution remains Samsung Health device-unconfirmed because aggregation removes device metadata.
- Initial backfill obtains a change token before reading history, so concurrent records are recovered on the next incremental pass.
- Uploads are split below the 1 MiB contract limit without retaining a second health-data queue on the phone.
- The Python server and dashboard suite passes 23 synthetic tests covering signed idempotent ingest, replay handling, updates/deletes, stress absence, attribution proof, location rejection, MCP exposure policy, manifest permissions, and watch CSV export.
- Populated desktop and 390-pixel mobile dashboard renders were inspected with no overlap or horizontal overflow. The UI separately shows companion read time, server upload time, observation age, category coverage, and Samsung-only unavailable states.

The synthetic gate is complete. Real-device activation still requires the user-controlled deployment actions listed above.
