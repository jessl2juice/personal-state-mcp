# Fable Review: Galaxy Watch5 Pro Integration

Reviewer: Fable, independent reviewer  
Date: 2026-09-24  
Reviewed artifact: `docs/watch5-pro-design-addendum.md`  
Review scope: security and privacy, Health Connect and Samsung platform realism, stress-data honesty, data contracts, freshness and completeness, ingest isolation, MCP safety, dashboard UX, failure modes, testability, and deployment risk

## Executive Assessment

The proposed Health Connect companion is the right first production path. The addendum correctly treats the Galaxy Watch as an indirect source, keeps the integration read-only, refuses to invent a stress score, and preserves the official Samsung applications as the authoritative safety layer.

The design is not yet implementation-ready. It currently overstates watch-specific provenance, does not define Health Connect update/deletion synchronization, and leaves the ingest authentication boundary too implicit. Those are approval blockers because they can produce falsely attributed health data, retain records a user deleted upstream, or expose a write path that is less isolated than the dashboard.

The design also needs an exact versioned contract, honest recency language, a source-capability matrix, explicit Android history/background permission behavior, stricter treatment of GPS routes and local queues, and a user-controlled MCP exposure policy.

## P0: Blocking Findings

### P0-1: Samsung Health origin is not proof that the Watch5 Pro collected a record

**Rationale**

The goal and dashboard repeatedly describe the dataset as Galaxy Watch5 Pro data. Health Connect can identify the application that wrote a record, but Samsung Health may contain records from the watch, the phone, a manually entered value, or another connected device. Several listed categories, including nutrition, blood glucose, weight, and height, are not inherently watch measurements. Even when `DataOrigin` is Samsung Health, device metadata may be missing or insufficient to prove Watch5 Pro provenance.

Without a stricter attribution model, the dashboard and MCP can falsely tell the user or an agent that a value came from the watch. This is especially consequential for blood pressure, blood glucose, body measurements, and heart-rate records.

**Required resolution**

- Filter the first adapter to Samsung Health `DataOrigin` records unless the user explicitly enables other origins.
- Add a required attribution field with at least `watch_confirmed`, `samsung_health_unattributed`, `phone`, `manual`, `external_device`, and `unknown` states.
- Use `watch_confirmed` only when record metadata positively identifies the paired Watch5 Pro. Do not infer it merely from the Samsung Health package.
- Preserve source package, Health Connect recording method, and non-identifying device type/model evidence used for attribution.
- Label unattributed records as "Samsung Health" in the dashboard and MCP, not "Galaxy Watch."
- Add tests with mixed Samsung Health, phone, manual, and third-party origins.

### P0-2: Update, deletion, and checkpoint semantics are missing

**Rationale**

"Read new or changed records" and deterministic IDs are insufficient. Health Connect records can be updated or deleted after ingestion. `INSERT OR IGNORE`-style idempotency would preserve obsolete values and would not propagate deletion. A user deleting a sensitive measurement in Samsung Health or Health Connect could reasonably expect the local copy to disappear, while the current design silently keeps it indefinitely.

Change tokens are scoped and can expire. Series and session records can also be revised after initial sync. The design has no rule for last-write ordering, deletion tombstones, token expiry, reinstall, or full reconciliation.

**Required resolution**

- Define an initial backfill followed by per-record-type Health Connect change-token processing, including pagination.
- Persist checkpoints independently for every record type and data origin.
- Upsert updates using an upstream version/last-modified value where available; reject an older replay from overwriting a newer record.
- Process deletion change events. Default to deleting or tombstoning the local observation and excluding it from dashboard/MCP results.
- If independent retention after upstream deletion is desired, make that an explicit, informed user setting rather than an undocumented default.
- Define recovery when a change token expires: bounded reconciliation, clear completeness state, and no duplicate inflation.
- Define reinstall/reset behavior and how the server distinguishes a legitimate resync from rollback.
- Test create, update, delete, out-of-order replay, token expiry, pagination, and reconciliation.

### P0-3: Ingest authentication and audience enforcement are not fail-closed enough

**Rationale**

The addendum names separate Cloudflare applications and audiences, but it does not specify which component validates the Access assertion. A `Host` header check is routing defense, not authentication. A Cloudflare service token embedded on a phone is a long-lived bearer credential and must be treated as extractable despite Keystore protection. The "optional request nonce" does not provide a defined replay defense.

If the tunnel or origin accepts the wrong audience, the companion credential could reach dashboard routes or an interactive dashboard credential could reach ingestion. A compromised companion credential could also fabricate plausible health observations.

**Required resolution**

- Require a dedicated ingest hostname, Access application, Service Auth-only policy, audience, and per-device service token. Do not share dashboard credentials or audience values.
- Specify the exact validation point for `Cf-Access-Jwt-Assertion`: tunnel ingress or origin must validate signature, issuer, audience, expiration, and service identity before the request reaches ingestion logic.
- Keep origin-side method, hostname, path, content-type, and body-size checks even when edge validation succeeds.
- Add a separate per-device origin credential. Make request authentication mandatory, not optional: include a signed timestamp, unique batch ID/nonce, and an HMAC over the exact request body, with a bounded replay window and stored nonce/batch deduplication.
- Define token duration, rotation overlap, revocation, lost-phone response, and a way to disable one device without affecting the dashboard.
- Return no dashboard content, health data, configuration, or detailed diagnostics from the ingest hostname.
- Add negative tests for wrong audience, wrong hostname, dashboard identity, expired JWT, revoked token, bad signature, replay, clock skew, and direct loopback requests.

### P0-4: Exercise route handling creates an undeclared location-data risk

**Rationale**

The storage contract mentions "route metadata," but the goals and privacy section do not decide whether GPS coordinates are collected. Exercise routes can reveal home, work, medical visits, and routines. They have distinct Health Connect access behavior and are materially more sensitive than activity summaries.

**Required resolution**

- Exclude route coordinates from the first release, including from generic structured payloads.
- If routes are later added, require a separate explicit opt-in, separate contract and permission review, encrypted storage, map privacy controls, export/delete coverage, and default exclusion from MCP tools.
- Add a schema test proving route coordinates and location fields are rejected by the first-release ingest allowlist.

## P1: High-Priority Findings

### P1-1: The capability list overstates what Samsung exports and what the watch collects

**Rationale**

Samsung's current published Health Connect mapping includes `TotalCaloriesBurnedRecord` for exercise calories and explicitly says Samsung Health activity-tracker data is not synchronized. The design nevertheless offers `activity.active_calories`, daily activity totals, and active time. Health Connect defines resting heart rate, HRV, and skin-temperature record types, but Samsung's published synchronization table does not say Samsung Health exports those types. A Health Connect type existing is not evidence that Samsung supplies it.

Nutrition, height, weight, blood glucose, and possibly blood pressure may be present in Samsung Health without having been collected by the Watch5 Pro.

**Required resolution**

- Replace the prose list with a versioned capability matrix containing: canonical metric, Health Connect type, Samsung-documented export status, likely watch source, regional/device dependency, companion permission, and first-release state.
- Rename exercise calories so they cannot be mistaken for all-day active or total calories.
- Mark resting heart rate, HRV, skin temperature, stress, active time, activity-summary calories, and floors as `not_exported_by_samsung_mapping` unless real-device evidence proves otherwise. Runtime evidence may upgrade availability but must not rewrite the documented platform claim.
- Distinguish "Samsung Health data visible through Health Connect" from "data collected by this watch" throughout the dashboard and MCP.
- Make the coverage panel the authoritative answer to the user's request for "all" data: collected, available, permission missing, not exported, unsupported, and unknown must be separate states.

### P1-2: The normalized observation contract is too vague for a strict ingest boundary

**Rationale**

A scalar plus arbitrary structured JSON is not a stable or safely validated contract. It leaves nullability, sample timestamps, ranges, enum values, update versions, offsets, recording method, provenance, and series limits undefined. It also makes future adapters likely to encode the same metric differently.

**Required resolution**

- Define an exact versioned JSON Schema or equivalent typed discriminated union for point, interval, series, session, and composite observations.
- Specify required and optional fields, nullability, numeric types, finite-value rules, value bounds, unit enums, maximum string lengths, maximum nesting, and maximum samples/stages per record.
- Preserve point/sample timestamps, interval start/end, start/end zone offsets, upstream last-modified time, recording method, data origin, device attribution state, and schema version.
- Define deterministic ID generation with a keyed HMAC or server-held salt. A plain hash of upstream identifiers is not sufficient where values may be linkable or guessable.
- Define per-metric payloads for blood pressure, nutrition, sleep stages, exercise sessions, body composition, and series records rather than relying on free-form JSON.
- Define forward/backward compatibility and an explicit error response that lets the companion retain and retry an upgrade-required batch.

### P1-3: Stress is honest about absence, but its future contract is not safe or complete

**Rationale**

The addendum correctly states that neither the documented Samsung Health-to-Health Connect mapping nor the current Samsung Health Data SDK data-type list exposes Samsung's stress score. `source_unavailable` is therefore the only honest first-release state. However, `wellness.stress` is listed beside measurable metrics without defining provider scale, range, label, algorithm version, or comparability. A dashboard "trend" could wrongly compare scores from different provider algorithms or imply a clinical worsening.

**Required resolution**

- Represent stress availability separately from observations. Do not create a synthetic `wellness.stress` observation with a null or zero value.
- For the first release, return `not_exported_by_samsung_mapping` with the checked capability version and no freshness classification.
- Define any future stress observation as vendor-specific: provider, provider metric name, provider version when available, raw score, scale minimum/maximum, display label, measured interval, and provenance.
- Prohibit normalization across providers and prohibit derived stress from HR, HRV, sleep, behavior, or conversation.
- Replace "trend" with a neutral source-specific history display unless the vendor itself supplies a named trend state. Do not show risk colors or agent interpretations.
- Keep stress out of `health.current_state()` by default. Expose it to agents only after a separate user opt-in, even when a future source becomes available.

### P1-4: Freshness conflates measurement recency, usefulness, and synchronization health

**Rationale**

Calling a 30-day-old body measurement "current," a completed sleep session "current" for 36 hours, or an exercise session "current" for 24 hours is misleading. `received_at` is described as phone-observed, but the companion does not know when Samsung Health or Health Connect received a record. It only knows when it read the record. "Delivery lag" is therefore an upper/lower-bound observation path duration, not verified vendor delivery lag.

The design also implies that a lack of new records can identify a Samsung sync problem, which is not true for sparse metrics.

**Required resolution**

- Separate `observation_recency` from `companion_sync_status` and `source_sync_status`.
- Rename the phone timestamp to `observed_by_companion_at`; keep `ingested_at_server` separately. Use upstream last-modified time only when supplied by Health Connect.
- Label measurement-to-companion time as an observed lag that includes vendor synchronization and collection delay, not as verified delivery lag.
- Recompute age and recency at response time; do not persist a freshness result that immediately becomes stale.
- Use neutral terms such as `latest_recorded`, `recent`, `old`, and `unknown` for sparse or completed measurements. Reserve `fresh` for metrics where the term has an explicitly justified meaning.
- Base series recency on the latest valid sample and interval recency on interval end, while retaining all original timestamps.
- Define behavior for future timestamps, missing offsets, phone/server clock skew, daylight-saving transitions, and negative ages.
- Do not state "Samsung Health not synchronized" or "phone sync overdue" from absence alone. Report the companion's last successful Health Connect read and server upload, then say Samsung/watch sync state is unknown.

### P1-5: Completeness states promise distinctions the companion cannot always make

**Rationale**

The companion can inspect its own Health Connect permission and platform feature status. It generally cannot prove that Samsung Health has write permission, that the watch recently synchronized, or that no record means the watch did not collect one. The proposed states risk turning unknowns into confident diagnoses of the data pipeline.

**Required resolution**

- Define a finite availability enum such as `available`, `permission_required`, `platform_feature_unavailable`, `not_exported_by_samsung_mapping`, `no_observation`, `source_configuration_unverified`, `companion_read_failed`, and `unknown`.
- Attach `evidence` and `checked_at` to every availability state.
- Treat no data with granted companion permission as `no_observation`, not proof of Samsung non-export or a watch sync failure.
- Keep the static Samsung capability matrix distinct from per-device runtime observations.
- Define category-level completeness windows and indicate whether a query was truncated, backfill-limited, or interrupted.

### P1-6: Historical and background permission behavior is incomplete

**Rationale**

Health Connect normally limits a newly authorized app to data from the 30 days before first permission grant. Older reads require the history permission. Background reads require a separate permission and feature availability. These constraints directly affect the promise of long-term history and unattended collection.

**Required resolution**

- Add explicit handling for `READ_HEALTH_DATA_HISTORY` and `READ_HEALTH_DATA_IN_BACKGROUND`, including feature-status checks and denial fallbacks.
- Keep both permissions optional and separately explained. The app must remain useful with foreground-only reads and a 30-day initial backfill.
- State that long-term history is guaranteed only from successful collection onward unless history access is granted and data exists upstream.
- Declare no write permissions in the manifest and add a test that fails if a Health Connect write permission appears.
- Define bounded page iteration, quota backoff, WorkManager constraints, and the behavior when Android delays or cancels periodic work.

### P1-7: The phone storage design contradicts its failure-mode queue

**Rationale**

The component section says the phone stores only checkpoints and encrypted credentials. The failure section says it retains an unsent bounded queue, which necessarily stores health payloads. This contradiction prevents an honest privacy assessment.

**Required resolution**

- Either retry by rereading Health Connect without a payload queue or explicitly design an encrypted local queue.
- If a queue is retained, specify encryption, key lifecycle, maximum bytes/records/age, eviction order, successful-upload deletion, corruption handling, and a visible "clear pending data" action.
- Exclude credentials and health payloads from Android backup, clipboard, screenshots where practical, crash reports, and diagnostic logs.
- Document that Keystore reduces credential extraction risk but does not protect a running app on a compromised/unlocked phone.

### P1-8: MCP access is broader than the project's selective-access goal

**Rationale**

`health.watch_recent(metric=None)` exposes a broad cross-category health history by default, potentially including nutrition, weight, blood pressure, and sleep. The base project promises selective physiological context when the user seems off; a general dump of 500 observations is not least privilege. Tool annotations and behavioral prose do not enforce category selection.

**Required resolution**

- Add a user-controlled MCP exposure allowlist separate from collection and dashboard visibility.
- Require an explicit metric/category for broad history queries, or make the default a documented minimal set.
- Keep sensitive categories such as routes, nutrition, body composition, blood pressure, and future stress out of agent access by default.
- Ensure every new MCP call uses the existing access policy, rate limit, audit log, response envelope, and safety notice.
- Bound time ranges and result sizes, provide a cursor rather than silent truncation, and include the effective exposure policy in responses without revealing secrets.
- Add tests proving a collected-but-not-agent-authorized category never appears in `health.watch()`, `health.watch_recent()`, or `health.current_state()`.

### P1-9: Daily totals need timezone, overlap, and aggregation rules

**Rationale**

"Today's steps" is not defined without the user's timezone. Raw interval records can overlap, be revised, or come from multiple origins. Summing all records can double count. Day boundaries and daylight-saving transitions can also split intervals.

**Required resolution**

- Use the configured user timezone for dashboard day boundaries and preserve Health Connect start/end offsets.
- Define overlap and duplicate handling per metric and origin.
- Prefer Health Connect aggregate results where they provide the intended user-priority deduplication, or document and test an equivalent server algorithm.
- Store aggregated summaries as a distinct record kind with their query window and provenance; do not masquerade an aggregate as a raw observation.
- Test midnight, daylight-saving changes, overlapping intervals, revised records, and mixed origins.

### P1-10: At-rest privacy and retention are underspecified for long-term health history

**Rationale**

The server database currently relies on current-user filesystem ACLs. The watch extension substantially increases the sensitivity and volume of stored data. The addendum also says it uses existing retention behavior, which may mean indefinite retention. Hashed identifiers do not protect measurement values.

**Required resolution**

- State the local threat boundary and choose an at-rest control: encrypted database, verified full-disk encryption requirement, or a clearly documented accepted residual risk approved by the user.
- Make retention explicit by category and show it in the dashboard. Indefinite retention must be an informed choice, not an implicit default.
- Include watch data in backup, restore, export, and deletion tests. Deletion must cover observations, sync metadata, tombstones, queued payloads, and derived aggregates.
- Use `Cache-Control: no-store` and a restrictive CSP/frame policy for dashboard and ingest responses. Ensure exports are never cached by Cloudflare or the browser.
- Confirm Cloudflare, application, reverse-proxy, and crash logs never record request bodies, credentials, query-contained health data, or response payloads.

## P2: Required Design Clarifications

### P2-1: Dashboard states need stronger non-clinical and data-gap behavior

**Rationale**

The proposed layout is appropriately scan-oriented, but range and trend displays can imply continuous monitoring or clinical interpretation. Connecting sparse samples across gaps can fabricate continuity. A user also needs to know whether a card shows a watch-confirmed record, unattributed Samsung Health data, or no supported source.

**Required resolution**

- Show source attribution and measured/read/upload ages on each card, not only in a coverage panel.
- Never connect chart lines across defined gaps; show sample density and partial-history/truncation states.
- Use neutral colors for stale, missing, and unavailable wellness data. Do not use alarm styling for oxygen, blood pressure, stress, or heart rate.
- Label ranges as observed sample ranges, not normal/abnormal ranges.
- Add accessible labels, keyboard behavior, color-contrast tests, reduced-motion behavior, and desktop/mobile screenshot checks.
- Add a visible statement that Samsung Health and Samsung Health Monitor remain authoritative for device features and official notices.

### P2-2: Validation and resource limits need concrete numbers and parser behavior

**Rationale**

"Bounded" appears throughout the design without values. That prevents implementation and abuse tests from agreeing on acceptable input.

**Required resolution**

- Specify maximum compressed and uncompressed body size, records per batch, samples/stages per record, JSON depth, string length, retained nonces, and accepted timestamp range.
- Reject duplicate JSON keys, non-finite numbers, invalid Unicode, unknown top-level fields, unknown metric fields, and unsupported content encodings.
- Rate-limit by device identity at both Cloudflare and origin, with a retryable sanitized response.
- Define whether unknown schema versions fail the full batch and how long the phone may retain them.

### P2-3: Failure modes and operations need expansion

**Rationale**

The listed failures cover the common path but omit several integrity and deployment failures that are likely in this architecture.

**Required resolution**

Add explicit behavior and tests for:

- Health Connect unavailable, outdated, disabled, or returning a feature mismatch.
- Samsung Health absent, signed out, or not writing a category.
- Service-token expiry during queue drain and credential rotation with overlap.
- Lost phone, companion uninstall/reinstall, restored app backup, and signing-key mismatch on update.
- Change-token expiry, quota limiting, partial pagination, and an update arriving before the original record.
- Clock rollback, timezone change, and daylight-saving transitions.
- Disk full, SQLite lock/corruption, migration failure, and interrupted transaction.
- Multiple paired phones or duplicate companion installations.
- Server restart during upload and acknowledgement loss after a committed transaction.

### P2-4: Schema migration and deployment rollback are not defined

**Rationale**

The current deployment is live and stores Libre history. Adding tables, host routing, and a second Access application without a migration and rollback plan risks taking the working glucose dashboard offline.

**Required resolution**

- Add a versioned, idempotent database migration with pre-migration backup and rollback/restore instructions.
- Keep watch ingestion and UI behind a disabled-by-default feature flag until synthetic end-to-end tests pass.
- Stage the ingest route with synthetic records before provisioning real device credentials.
- Verify the existing dashboard audience, Libre collection, MCP tools, scheduled tasks, and Cloudflare tunnel after deployment.
- Document APK signing-key custody and require updates to use the same package identity/signing key.
- Define rollback behavior that leaves already collected watch history exportable and deletable without exposing it through broken APIs.

### P2-5: Test plan needs contract, privacy, and platform assertions

**Rationale**

The proposed test categories are good but do not yet prove the hardest claims.

**Required resolution**

Add tests that prove:

- Samsung Health origin does not automatically become watch-confirmed attribution.
- Unsupported stress produces availability metadata and no observation value.
- No HR/HRV/sleep-derived stress computation exists.
- The Android manifest contains only approved read permissions and no location/route or write permission.
- Health payloads and secrets are absent from logs, crash diagnostics, backups, and HTTP error responses.
- Upstream record deletion, update, replay, and token-expiry reconciliation work.
- MCP exposure policy prevents collection from implying agent access.
- Cloudflare and origin reject cross-audience and cross-host traffic.
- Retention deletion removes raw, derived, queued, and operationally linked data.

## Platform Evidence Checked

- Samsung documents that Galaxy Watch data reaches Health Connect indirectly through Samsung Health and that synchronization timing is vendor-controlled: https://developer.samsung.com/health/health-connect-faq.html
- Samsung's current mapping lists the exported Health Connect record types and states that activity-tracker data is not synchronized: https://developer.samsung.com/health/blog/en/accessing-samsung-health-data-through-health-connect
- Android documents separate background/history permissions, a default 30-day historical access boundary, pagination, and quota concerns: https://developer.android.com/health-and-fitness/health-connect/read-data
- Health Connect defines HRV, resting-heart-rate, and skin-temperature types, but type availability alone does not mean Samsung exports them: https://developer.android.com/health-and-fitness/health-connect/data-types
- Samsung's current Health Data SDK data-type list includes energy score and other Samsung-only categories but does not list stress: https://developer.samsung.com/health/data/guide/features/data-types.html
- Cloudflare service tokens are Client ID/Client Secret bearer credentials with configured duration and explicit rotation/revocation lifecycle: https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/

## Approval Gate

**APPROVED AFTER LISTED CHANGES**

Implementation may begin only after all P0 findings and P1 findings are resolved in the revised design and resolution log. P2 items may be completed in the revised design or converted into explicit implementation acceptance criteria, but none may be silently omitted. Real health data, Cloudflare service credentials, or phone permissions must not be provisioned until the P0 security and provenance controls are implemented and verified with synthetic data.

## Re-review: Revision 2

Date: 2026-09-24  
Reviewed: revision 2 of `docs/watch5-pro-design-addendum.md` and `docs/review/watch5-pro-resolution-log.md` against every P0 and P1 finding above.

Revision 2 materially resolves P0-1, P0-2, P1-6, P1-9, and P1-10, and establishes the right direction for the other findings. The resolution log nevertheless overstates closure. These blockers remain:

- **P0-3 remains internally contradictory:** the upload protocol makes timestamp, nonce, batch ID, and HMAC mandatory, but the threat model still calls the request nonce optional. Make replay protection mandatory everywhere.
- **P0-4 remains internally contradictory:** the storage description still permits `route metadata` even though the first-release contract says all route/location data is rejected. Remove route metadata from the allowed observation model.
- **P1-1/P1-3/P1-4 retain stale claims:** early platform text still says the companion reads all listed categories and uses `source_unavailable`; the recency section assigns freshness to unavailable stress; the dashboard promises a stress `trend`; and dashboard/failure text still uses `delivery freshness`, `phone-received`, `delivery lag`, and `stale`. Align all sections with the capability matrix, `not_exported_by_samsung_mapping`, neutral source-specific history, and the new companion-observed/server-ingested terminology. Oxygen and steps also cannot use `fresh` while the design says that state is reserved for continuous metrics.
- **P1-2 is not yet an exact closed contract:** the document names observation variants but does not define the field shapes for `changes` or `availability`, the metric-specific payload fields, nullability, per-metric unit mapping, or exact per-metric bounds. Add the actual JSON Schema or an equally precise schema annex before implementation.
- **P1-5 is incomplete:** add explicit response flags for `truncated`, `backfill_limited`, `interrupted`, and `reconciling`, with category coverage windows and evidence timestamps.
- **P1-7 remains contradictory:** the companion correctly has no payload queue, but schema mismatch still says the rejected batch is retained on the phone. It must retain only the checkpoint and reread Health Connect after upgrade. Also state explicitly that Keystore does not protect credentials from a compromised or unlocked running device.
- **P1-8 lacks a hard history bound:** `health.watch_recent` has a default but no maximum `hours` value. Define maximum time range, page size, cursor expiry/integrity, and behavior when the requested range exceeds policy.

### Final Design Gate

**BLOCKED**

Revision 2 is not approved for implementation, including synthetic implementation, until the blockers above are corrected in the design and resolution log. Real health data, Android permissions, device credentials, at-rest risk acceptance, and Cloudflare service credentials remain separately gated and must not be provisioned.

## Final Re-review: Revision 2.1

Date: 2026-09-24  
Reviewed: revision 2.1 of `docs/watch5-pro-design-addendum.md`, normative `docs/watch-ingest-schema-v1.json`, and `docs/review/watch5-pro-resolution-log.md` against every blocker in the Revision 2 re-review.

All P0 blockers are resolved: replay protection is mandatory throughout, route/location data is excluded from the model and schema, attribution remains evidence-based, and update/delete/checkpoint behavior is defined. Revision 2.1 also resolves the stress, completeness, no-queue, Keystore, MCP range/cursor, and stale terminology blockers in substance.

The normative JSON parses successfully and is structurally closed at the batch, change, availability, coverage, attribution, and metric-payload levels. Its metric union has 17 unique observation payload branches; stress, resting heart rate, HRV, skin temperature, floors, and active time are availability-only as intended. Route/location fields have no accepted schema path.

The following P1 blockers remain:

- **P1-2, record variants are not fully closed:** `observation` defines `measured_at`, `start_at`, `end_at`, and all three offset fields globally. The conditional rules require the appropriate fields but do not forbid inapplicable fields. A point/composite record can therefore also carry interval times, and an interval/series/session/aggregate can also carry `measured_at`, creating ambiguous timestamp authority. Make the record-kind union exclusive and reject timing/offset fields that do not belong to that variant.
- **P1-2, unit provenance contradicts the schema:** the design says original unit metadata is retained after conversion, but the normative schema has no `original_unit`, original value, or conversion metadata field. Add a closed conversion-provenance shape or remove the retention claim.
- **P1-4, one sync claim remains:** the dashboard still promises "last phone sync status." Replace it with last successful companion Health Connect read and server upload, while keeping Samsung/watch synchronization explicitly unknown.
- **Normative-document consistency:** the resolution log still identifies the design as revision 2 rather than 2.1 and retains a 64 KiB `details` limit even though the normative schema has no `details` field. Correct or remove those claims so the resolution log does not describe a different contract.

### Final Gate

**BLOCKED**

Synthetic implementation is not yet approved. Once the exclusive timing union and unit-provenance decision are reflected in the normative schema, the remaining phone-sync wording is corrected, and the resolution log matches revision 2.1, no further P0/P1 design blocker is anticipated. Real health data, Android permissions, device credentials, at-rest risk acceptance, and Cloudflare service credentials remain separately gated and must not be provisioned.

## Final Gate Note

Date: 2026-09-24

The four remaining P1 blockers are resolved:

- The normative schema requires `measured_at` for point/composite records and rejects interval timing fields; it requires `start_at`/`end_at` for interval/series/session/aggregate records and rejects point timing fields.
- Revision 2.1 explicitly does not retain original-unit metadata; the canonical typed value is the persisted contract.
- Dashboard wording now reports the last successful companion Health Connect read and server upload while keeping Samsung/watch synchronization status unknown.
- The resolution log identifies revision 2.1 and its ingest limits match the normative contract; the obsolete 64 KiB `details` claim is removed.

The corrections introduce no concrete new P0/P1 defect.

### Gate

**APPROVED FOR SYNTHETIC IMPLEMENTATION ONLY**

Real health data, Android permission grants, device credentials, at-rest risk acceptance, and Cloudflare service credentials remain separately gated. They must not be provisioned until synthetic implementation and its required security, contract, privacy, migration, and end-to-end tests pass, followed by the applicable explicit user approvals.

## Final Gate Verification

Date: 2026-09-26

Re-verification was limited to the four remaining P1 corrections identified in the Final Re-review:

- The normative schema now makes timing fields exclusive by record kind. Point/composite records require `measured_at` and reject interval timing/offset fields; interval/series/session/aggregate records require `start_at` and `end_at` and reject point timing/offset fields. Valid point and interval examples pass schema validation, while mixed-field examples fail.
- Revision 2.1 explicitly states that original-unit metadata is not retained; only the canonical typed value is persisted.
- Dashboard wording now distinguishes the last successful companion Health Connect read from the last successful server upload and explicitly leaves Samsung/watch synchronization status unknown.
- The resolution log identifies revision 2.1 and lists limits consistent with the normative contract. The obsolete 64 KiB `details` claim is absent.

No correction creates a concrete new P0 or P1 defect. Previously resolved items were not reopened.

### Final Gate

**APPROVED FOR SYNTHETIC IMPLEMENTATION ONLY**

Real health data, Android permission grants, device credentials, at-rest risk acceptance, and Cloudflare service credentials remain separately gated pending successful synthetic security, contract, privacy, migration, and end-to-end verification plus the applicable explicit user approvals.
