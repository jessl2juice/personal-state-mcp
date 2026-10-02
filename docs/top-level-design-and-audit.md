# Personal State MCP Top-Level Design and Audit

Document version: 0.3
Date: 2026-10-02
Status: Direct Fitbit BLE realtime lane verified on phone; Casey state/confidence model documented; field range/run validation remains open

## Source Basis

This document reconciles three sources of truth:

- Notion wiki source: Fitbit Air / Google Health wearable exploration and independent wearable project plan, both written 2026-07-26 and migrated 2026-07-30.
- Current repository documentation: README, dashboard user stories, architecture/security/privacy, API/data reference, and Google Health / Fitbit Air design.
- Current dirty working tree audit as of 2026-10-01, including active dashboard, collector, credential, Google Health, storage, and test changes.

No current Notion page named exactly for `personal-state-mcp` or `phy.clinicianassist.ai` was found during this audit. This document is the missing top-level page that connects the Notion wearable plan to the live implementation.

## Product Boundary

Personal State is a private, read-only physiological context system. It is not an alarm, diagnosis engine, causality engine, treatment recommender, emergency workflow, or replacement for the official device applications.

The system may show recorded facts and context:

- what value was recorded;
- when it was measured;
- when it was received, synchronized, or stored;
- which source produced it;
- how old it is;
- whether the source is live, near real time, synchronized history, or historical import;
- what is missing, stale, delayed, or unproven.

The system must not turn missing data into reassurance, turn stale data into current status, or turn consumer sensor trends into clinical instructions.

## User Stories

### Person Whose Data Is Shown

As the person whose data is shown, I need a single truthful screen that shows glucose, direct Galaxy heart rate, direct Fitbit Bluetooth heart rate, Samsung/Galaxy history, and Fitbit/Google Health history with clear source and age labels, so I can tell what is happening now versus what was merely recorded earlier.

Acceptance criteria:

- Every prominent value shows value, unit, source, measurement time, age, and state.
- Current glucose can be near real time only when Libre freshness permits it.
- Current heart rate can be live only when a direct Galaxy Watch or Fitbit Bluetooth sample is no more than 10 seconds old.
- Fitbit/Google Health cloud and Health Connect values are labeled synchronized or recorded, never live.
- Missing values say unavailable, waiting, stale, or not connected. They never imply normal physiology.

### Clinician Or Clinical Reviewer

As a clinician or reviewer, I need a source-aware record with provenance, coverage, and gaps so I can discuss observed data without the dashboard asserting diagnosis or causality.

Acceptance criteria:

- Glucose remains primary for glucose review, with mg/dL and threshold context clearly limited to conversational context.
- Heart-rate and wearable data appear as context on the same recorded-time axis.
- Print and export views include source, age, generated time, and limitations.
- Official Libre, Samsung, Fitbit, and Google applications remain the device-authority and safety-notice layer.

### Authorized Agent

As an authorized agent, I need a compact read-only physiological context response when the user seems off, so I can slow down, clarify, or suggest checking official apps without diagnosing or directing care.

Acceptance criteria:

- MCP tools are read-only and allowlisted.
- Responses include freshness, provenance, limitations, and safety wording.
- Watch data is grouped by source class, not flattened into a single latest-by-metric blob.
- Stale and missing data remain unknown.

### Operator And Engineer

As the operator/engineer, I need source modules, credentials, jobs, storage, UI, and APIs to have clear ownership boundaries so a Google/Fitbit fix cannot break Libre or direct Galaxy heart rate.

Acceptance criteria:

- Each source has an independent collection path, status, error, and timeout budget.
- A slow Google Health sync cannot block Libre freshness or live heart display.
- A dashboard refresh must not silently refresh only one source unless the UI says so.
- Disconnect semantics are explicit: revoke credentials, delete source data, or retain history by named choice.
- Secrets are held in OS-backed secret storage or another explicitly approved encrypted store.

## Source Taxonomy

| Source class | Current source | Timing promise | Display rule |
| --- | --- | --- | --- |
| Near-real-time glucose | Libre 3 Plus through LibreLinkUp follower | Near real time only when Abbott follower data is fresh | Can occupy glucose current card when freshness passes policy |
| Direct live vital | Galaxy Watch Wear OS Health Services | Live only for direct samples <= 10 seconds old | Can show numeric live heart rate only at sample/card level |
| Direct live vital | Fitbit Bluetooth LE Heart Rate Service | Live only for direct samples <= 10 seconds old | Can support Casey realtime heart-rate biofeedback; heart-rate only |
| Recorded direct/vendor history | Samsung Health / Health Connect / Samsung Health Data SDK | Recorded history with source timestamps | Never promoted to live without direct-watch evidence |
| Synchronized wearable history | Fitbit Air or Google wearable through Google Health | Synchronized after Fitbit/Google app/cloud sync | Always synchronized/recorded, never live |
| Phone Health Connect source history | Fitbit application through Android Health Connect | Synchronized after Fitbit phone app writes Health Connect records | Source-labeled as Fitbit via Health Connect when package evidence is present; never direct live |
| Direct Fitbit cloud evaluation | Fitbit Web API or Google Health successor endpoints | Cloud/API direct, subject to provider cadence and API lifecycle | Candidate future lane; must not be presented as guaranteed realtime until measured |
| Historical import | LibreView, Google Fit Takeout, other user archives | Historical only | Cannot occupy a live/current card |

The term `Fitbit Air` is allowed only when source evidence supports it. Otherwise the public label should be `Google Health synchronized wearable history`.

## Top-Level Architecture

```text
Official device/vendor apps
  -> source adapter or authenticated phone ingest
  -> normalized source-owned observations
  -> SQLite store with provenance, freshness, attribution, and source status
  -> read models for dashboard, export, and MCP
  -> source-aware UI/API responses
```

Control planes:

- Credentials: Libre follower password, Google Health OAuth grant, watch device secrets, Cloudflare Access credentials.
- Scheduling: collector loop and manual dashboard refresh.
- Device ingest: phone/watch HMAC, replay protection, batch idempotency, schema validation.
- Agent access: host allowlist, rate limit, metric allowlist, audit log, fixed safety wording.

## Design Decisions

1. Live is a property of a direct sample, not a whole source panel.
2. Fitbit/Google Health cloud and Health Connect are synchronized history, even when recent.
3. Fitbit BLE heart rate is a separate direct-live source lane and is eligible for Casey realtime only inside the 10-second live window.
4. Source labeling must be shared code or shared data, not repeated UI strings.
5. Manual refresh must have source-level intent and source-level result reporting.
6. Disconnect must explicitly handle both credentials and local source data.
7. Secret fallback may not silently reduce security.
8. The Google/Fitbit pilot gate remains real-world Casey/Fitbit validation: Share heart rate is verified on the phone, a roughly 30-foot house-range live window was observed, and manual pulse supported Fitbit during a Galaxy conflict. Reconnect behavior, outdoor run cadence, battery impact, and cloud catch-up still need documented field evidence before claiming operational readiness.

## Current Design Strengths

- The written boundary is correct: read-only context, no diagnosis, no treatment, no alarm.
- The dashboard already separates Libre, Galaxy, and Fitbit source strips.
- Direct Galaxy heart-rate cutoff is correctly centered on the 10-second live rule.
- Google Health scopes are read-only.
- Normalized observations preserve source timestamps, adapter ids, provenance, and attribution.
- Current tests cover partial Google Health failure, Fitbit source summary, stale glucose, and direct-heart live cutoff.

## Audit Findings

### P1: Google Health Credential Fallback Conflicts With Encrypted Token Storage

The Notion wearable plan and repo docs require encrypted token storage. The current working tree adds a JSON fallback that can write Google client secret and refresh token to a local plaintext file when keyring fails.

Evidence:

- `src/personal_state_mcp/secrets.py:65` writes the credential fallback payload.
- `src/personal_state_mcp/secrets.py:237` silently falls back to that file on keyring failure.
- `docs/google-health-fitbit-air-design.md:33` says OAuth client secret and refresh token are stored in the OS keychain.

Required design:

- Fail closed if keyring is unavailable, or use an explicit OS-backed encrypted fallback such as DPAPI.
- If an emergency local file fallback is ever permitted, it must be opt-in, named unsafe, permission-checked, excluded from production, and reflected in docs and tests.

### P1: Disconnect Removes Credentials But Not Local Google/Fitbit Observations

The Notion plan says disconnect deletes. Current CLI credential deletion removes grants but does not delete already synchronized Google/Fitbit observations.

Evidence:

- `src/personal_state_mcp/cli.py:91` through `src/personal_state_mcp/cli.py:93` delete Google Health credentials.
- Existing storage deletion is not exposed as a source-scoped Google/Fitbit disconnect operation.

Required design:

- Add source-scoped deletion for `google_health_fitbit`.
- Make disconnect semantics explicit: `disconnect --delete-local-data` or `disconnect --retain-history`, with the default matching the product privacy promise.

### P1: Google/Fitbit Data Granularity Drifted From Daily-Summary Minimization

The original Notion wearable plan called for nightly or daily summaries and no raw streams for minimization. The current adapter includes sample and series data types and persists sample payloads.

Evidence:

- `src/personal_state_mcp/adapters/google_health.py:46` includes multiple sample and series data types.
- `src/personal_state_mcp/adapters/google_health.py:353` stores samples in normalized payloads.
- Notion wearable exploration states daily summaries are preferred for data minimization.

Required design:

- Decide explicitly whether the current product permits bounded recent samples.
- If not, narrow Google/Fitbit pilot collection to summary records.
- If yes, update Notion and repo docs with why bounded samples are necessary, what limits apply, and why this remains minimization.

### P1: Source Labels Still Blur Google Health, Fitbit Air, And Stream Semantics

The system should only say Fitbit Air when exact evidence supports that attribution, and should not call Google Health a stream.

Evidence:

- `src/personal_state_mcp/models.py:185` through `src/personal_state_mcp/models.py:187` label `google_health_fitbit` as `Google Health API wearable stream`.
- `src/personal_state_mcp/adapters/google_health.py:306` through `src/personal_state_mcp/adapters/google_health.py:314` acknowledge exact Fitbit Air attribution is not always asserted.

Required design:

- Default public label: `Google Health synchronized wearable history`.
- Upgrade label to `Fitbit Air via Google Health` only with platform and device evidence.
- Eliminate `stream` from Google/Fitbit public labels.

### P1: Most Prominent Current Cards Do Not Show Source Inline

The page states every value is labeled with age and source, but the top current cards show value and age without an equally prominent source chip.

Evidence:

- `src/personal_state_mcp/dashboard_static/index.html:80` makes the source/age promise.
- `src/personal_state_mcp/dashboard_static/app.js:248` renders current glucose details.
- `src/personal_state_mcp/dashboard_static/app.js:294` renders current heart details.

Required design:

- Current glucose card must show `LibreLinkUp follower` or explicit unavailable source.
- Current heart card must show `Direct Galaxy Watch` when live; historical fallback must show its actual adapter.
- Every card must include value, unit, source, measurement time, age, and state.

### P1: Galaxy Source Panel Uses Live At The Panel Level

The backend marks Galaxy live when direct heart is current, but the UI can label the mixed `Direct + Samsung Health` panel as live even while other Galaxy/Samsung metrics are recorded history.

Evidence:

- `src/personal_state_mcp/dashboard.py:746` sets Galaxy source live based on direct heart status.
- `src/personal_state_mcp/dashboard_static/app.js:361` and `src/personal_state_mcp/dashboard_static/app.js:674` surface panel-level `Live`.

Required design:

- `Live` belongs only on the direct heart-rate card.
- The Galaxy panel can say `Direct heart live; Samsung history recorded`.
- Samsung Health records must retain recorded/synchronized wording.

### P1: Collector Or Live Failures Are Too Easy To Miss

Stored values can still look normal while the collector or live endpoint is degraded. The current UI puts collector failures lower in the system panel rather than in the top source status.

Evidence:

- `src/personal_state_mcp/dashboard.py:1025` includes collector run state in dashboard payload.
- `src/personal_state_mcp/dashboard_static/app.js:486` renders collector details in the system panel.
- `src/personal_state_mcp/dashboard_static/app.js:814` top status does not treat collector failure as a first-class degraded state.

Required design:

- Add a persistent top-level `sync degraded` or `stored history only` state when the latest collector run failed, timed out, or `/api/live` fails.
- Keep the last value visible only with source, age, and degraded-state warning.

### P1: Manual Dashboard Refresh Now Refreshes Libre Only

The dashboard constructs a full collector with Libre and Google Health, but the manual refresh route calls a Libre-only collector. That can make a user believe all sources refreshed.

Evidence:

- `src/personal_state_mcp/dashboard.py:462` creates the full collector.
- `src/personal_state_mcp/dashboard.py:467` creates a Libre-only collector.
- `src/personal_state_mcp/dashboard.py:1135` refreshes only `self.libre_collector`.

Required design:

- Rename the button and result to `Refresh Libre` if that is intentional.
- Or support source-selective refresh, e.g. `POST /api/refresh {"sources":["libre","google_health"]}`, with per-source results.

### P2: MCP Watch Summary Is Latest-By-Metric, Not Source-Grouped

`health.watch()` currently returns latest observations by metric. That can hide source conflicts or source-specific stale behavior.

Evidence:

- `src/personal_state_mcp/service.py:278` calls `latest_watch_by_metric(allowed)`.

Required design:

- Return source groups: `galaxy_direct_live`, `samsung_health_history`, `google_health_sync`, and optionally `historical_imports`.
- Let clients request a metric by source when needed.

### P2: Recency Wording Is Metric-Based Instead Of Source-Based

Some wording calls vitals `Fitbit-derived` based on metric type rather than adapter source.

Evidence:

- `src/personal_state_mcp/watch_contract.py:540` through `src/personal_state_mcp/watch_contract.py:541` use `Fitbit-derived` for resting heart rate, HRV, and respiratory rate.

Required design:

- Compute wording from `adapter_id`, source package, and attribution evidence.

### P2: Timeout Is Reporting, Not Cancellation

The new collector timeout lets fast adapters return while slow adapters continue running in daemon threads. That avoids blocking but does not cancel the underlying adapter call.

Evidence:

- `src/personal_state_mcp/collector.py:46` through `src/personal_state_mcp/collector.py:50` start adapter threads.
- `src/personal_state_mcp/collector.py:69` through `src/personal_state_mcp/collector.py:76` record timeout while the thread may still be running.
- `tests/test_collector.py:74` through `tests/test_collector.py:98` verify timeout and skip behavior.

Required design:

- Document timeout as degraded reporting, not cancellation.
- Prefer adapter-level request budgets and cancellation-safe source ownership.
- Record long-running adapter status until the old thread releases the source lock.

### P1: Android Health Connect Consent Is Broader Than The Current Product Surface

The phone companion requests the complete `HealthConnectSync.READ_PERMISSIONS` set from a single `Choose health data` action. That set includes activity, heart, sleep, oxygen, blood pressure, blood glucose, body, height, basal metabolic rate, and nutrition records. Health Connect still gates reads by granted permission, but the app-level request shape is broader than the current Fitbit/Galaxy source taxonomy and broader than the dashboard currently explains.

Evidence:

- `android/health-connect-companion/app/src/main/AndroidManifest.xml:8` through `android/health-connect-companion/app/src/main/AndroidManifest.xml:25` declare the broad read permission set.
- `android/health-connect-companion/app/src/main/java/ai/clinicianassist/personalstate/MainActivity.kt:67` requests `HealthConnectSync.READ_PERMISSIONS` as one bundle.
- `android/health-connect-companion/app/src/main/java/ai/clinicianassist/personalstate/HealthConnectSync.kt:567` through `android/health-connect-companion/app/src/main/java/ai/clinicianassist/personalstate/HealthConnectSync.kt:585` define the bundle.

Required design:

- Split consent into product-aligned groups, for example `core activity`, `sleep`, `heart`, `oxygen`, `body`, `nutrition`, and `glucose/BP`.
- Default the companion to the minimum group needed for the live dashboard.
- Treat blood glucose, blood pressure, body composition, and nutrition as explicit optional panels with visible user intent.
- Mirror every granted group in dashboard/source labels and API metadata so data is never collected invisibly.

### P1: Health Connect Heart History Reads All Origins But The Panel Says Galaxy

Health Connect history is Samsung-filtered for most metrics, but heart rate deliberately uses an empty origin filter. That can ingest heart records from any Health Connect origin while the dashboard presents a Galaxy Watch panel.

Evidence:

- `android/health-connect-companion/app/src/main/java/ai/clinicianassist/personalstate/HealthConnectSync.kt:180` through `android/health-connect-companion/app/src/main/java/ai/clinicianassist/personalstate/HealthConnectSync.kt:181` return `emptySet()` for `vitals.heart_rate`.
- The same origin filter is used for initial backfill, change reads, and aggregate reads.
- The dashboard source taxonomy currently groups this into Galaxy/Samsung source presentation.

Required design:

- Keep direct Galaxy live heart rate separate from Health Connect recorded heart history.
- If all-origin heart history is intentional, label it as `Health Connect heart history` and show actual origin package/device where available.
- If the product promise is Galaxy-only, apply the Samsung origin filter consistently and add tests that reject non-Samsung heart records from the Galaxy panel.

### P2: Android Companion Persists More Metrics Than The Dashboard Governs

The companion normalizes and uploads metrics that are not yet clearly represented in source panels, MCP contracts, or data-retention controls. This is a data-minimization and operator-surprise risk even when the underlying Health Connect permission was granted.

Evidence:

- `android/health-connect-companion/app/src/main/java/ai/clinicianassist/personalstate/HealthConnectSync.kt:52` through `android/health-connect-companion/app/src/main/java/ai/clinicianassist/personalstate/HealthConnectSync.kt:68` include power, speed, VO2 max, blood pressure, blood glucose, body composition, height, and nutrition.
- `src/personal_state_mcp/dashboard.py` and `src/personal_state_mcp/dashboard_static/app.js` focus the user-facing source panels on Libre, Galaxy, and Fitbit.

Required design:

- Create a source and metric policy table that says which metrics are collected, displayed, exposed to MCP, retained, and deletable.
- Add source-scoped deletion for every uploaded Health Connect metric group.
- Keep dashboard and MCP defaults narrow until the policy table explicitly permits broader exposure.

## Module Boundaries

### `source_registry`

Owns source ids, display labels, source class, timing promise, live eligibility, default visibility, source filters, and safety wording.

Example source ids:

- `libre_near_real_time`
- `galaxy_direct_live`
- `samsung_health_history`
- `google_health_sync`
- `google_fit_import`

### `adapters/*`

Own vendor API translation only. Adapters emit normalized observations and source status. They should not decide dashboard wording beyond structured source evidence.

### `collector.py`

Owns scheduling, adapter budgets, source-level run status, and persistence of collection results. It should report source-specific outcomes and avoid a slow source blocking a fast source.

### `storage.py`

Owns normalized observations, source-scoped query APIs, source-scoped deletion, dedupe, and retention. It should support latest-by-source as a first-class read model.

### `service.py`

Owns read-only MCP data contracts and safety envelopes. It should return source-grouped summaries rather than flattening all sources by metric.

### `dashboard.py`

Owns HTTP read models, refresh endpoint behavior, ingest endpoint routing, and local dashboard security headers. It should return source groups and degraded states directly so the browser is not inventing semantics.

### `dashboard_static/*`

Owns rendering. It should display source registry labels and state from the API, not maintain independent medical/source semantics in JavaScript.

### `secrets.py`

Owns secret retrieval and storage. It should fail closed or use approved OS-backed encryption. Plaintext fallback must not be silent.

### `mcp_server.py`

Owns tool registration only. It should remain read-only and delegate all behavior to `HealthService`.

## API Shape

### Dashboard API

`GET /api/live`

- Returns compact current glucose and direct live heart status.
- Must include source, measured time, age, freshness, and degraded source status.

`GET /api/dashboard?range=24h`

- Returns dashboard payload grouped by source.
- Must include collector/source run status near the top.

`POST /api/refresh`

- Should accept optional source selection.
- Must return per-source results.
- Must not imply all sources refreshed if only Libre was collected.

`GET /api/watch/history?metric=...&source=...`

- Should support optional source filtering.
- Must distinguish direct live source, Samsung history, Google Health sync, and imports.

### MCP API

`health.current_state()`

- Compact, source-labeled current state.
- No treatment or causality language.

`health.watch()`

- Source-grouped wearable summary.
- Each group includes source class, latest observations, age, sync status, and limitations.

`health.watch_recent(metric, hours, limit, cursor, source=None)`

- Adds optional source filter.
- Refuses unauthorized metrics and source combinations.

### CLI

`google-health-connect`

- Connects using approved encrypted credential storage.

`google-health-sync`

- Runs bounded source sync and reports data types advanced, deferred, or failed.

`google-health-disconnect`

- Explicitly separates revoke/delete credentials from local-history deletion or retention.

## Stabilization Plan

1. Revert or replace plaintext Google Health credential fallback with approved encrypted storage.
2. Create a shared source registry and update Python and JavaScript labels to consume source state instead of duplicating labels.
3. Fix the current cards so every prominent value includes source and age.
4. Split Galaxy direct live heart from Samsung/Galaxy recorded history in payload and UI.
5. Make manual refresh source-specific and honest.
6. Add source-scoped local deletion and explicit disconnect semantics for Google/Fitbit.
7. Decide and document Google/Fitbit granularity: summary-only pilot versus bounded sample collection.
8. Reshape MCP `health.watch()` around source groups.
9. Add tests for visible UI promises: source plus age on current cards, degraded collector state, no panel-level `Live` for mixed sources, and Fitbit/Google synchronized wording.

## Execution Progress

Completed on 2026-10-01:

- Removed the silent plaintext Google Health credential fallback. Google Health OAuth material is no longer read from or written to `google-health-credentials.json`; the legacy file path is used only so an old unsafe fallback file can be deleted.
- Added tests proving Google Health credentials fail closed without keyring, do not read a legacy plaintext fallback, delete a legacy plaintext fallback, and still use keyring when available.
- Changed Google Health/Fitbit provenance labels so confirmed Fitbit Air records say `Fitbit Air via Google Health`, while records without exact device evidence say `Google Health synchronized wearable history`.
- Updated dashboard rendering so top glucose and direct-heart cards include source in the existing detail line.
- Made `POST /api/refresh` explicitly Libre-scoped in both API response and browser success message.
- Split Android Health Connect permissions into core and optional expanded groups. The default phone button now launches `CORE_READ_PERMISSIONS`; blood pressure, blood glucose, body metrics, VO2 max, power, speed, and nutrition are in `OPTIONAL_READ_PERMISSIONS`.
- Added static Android companion tests for the permission split, then restored full Android debug verification by using the SDK build-tools `aapt2.exe` instead of the Gradle-downloaded AAPT2 daemon.
- Added adapter-scoped watch-history deletion and a `google-health-disconnect --yes` command that deletes the Google Health grant and, unless `--retain-history` is passed, local Google/Fitbit observations.
- Added MCP `health.watch()` source groups while preserving the existing flat `latest` field for compatibility.
- Changed Galaxy dashboard wording so the mixed Galaxy/Samsung panel says `Direct heart live` and `recorded metrics` instead of applying `Live` to the whole panel.
- Ran a browser-rendered smoke check against a throwaway synthetic database served from the edited worktree on `127.0.0.1:8799`. The page rendered fresh Libre glucose with `LibreLinkUp follower`, Fitbit cards with `Fitbit Air via Google Health`, and Galaxy/Samsung cards with `Direct Wear OS Health Services` and `Samsung Health via Health Connect`.
- Added a shared `source_registry` module for wearable source groups and source filters.
- Added source-filtered watch history to the dashboard API and MCP: `GET /api/watch/history?metric=...&source=...` and `health.watch_recent(..., source=...)` now distinguish `galaxy_direct_live`, `samsung_health_history`, `health_connect_history`, `google_health_sync`, `historical_imports`, plus aggregate `galaxy` and `fitbit` aliases.
- Added a backend `sync` read model to `/api/dashboard` and `/api/live`, and promoted collector/live endpoint degradation to the first-screen status rail and source chip. A failed, partial, timed-out, or still-running collector now surfaces as `Stored history only` or `Sync degraded` while preserving source-labeled last values.
- Resolved the Google/Fitbit sample-vs-summary minimization decision: continuous Google Health sync uses bounded recent samples for source-labeled timeline context, defaults to a 36-hour window, records `collection_policy=bounded_recent_samples_for_timeline_context`, does not persist raw Google API responses, and treats optional backfill as historical/synchronized context only.
- Earlier in the audit, verified the local dashboard with real sources while the Galaxy Watch was active: 15 consecutive `/api/live` polls reported current glucose, live Galaxy/direct heart, synced Fitbit, and direct heart present; the browser rendered `3 sources reporting`, current Libre, direct Galaxy heart live, and Fitbit synchronized records.
- Fixed a legacy dashboard display issue found during that live browser check: stored Google Health records with the old `Google Health API wearable stream` label are normalized in the browser to `Google Health synchronized wearable`, and old `libre_linkup_follower` labels display as `LibreLinkUp follower`.
- Continued the code review into the dashboard drilldown flow and found a source-boundary mismatch: the backend supported `GET /api/watch/history?metric=...&source=...`, but the browser cards opened metric history without carrying their source. Fixed Fitbit Air, Galaxy Watch, and aggregate signal cards so drilldowns pass `google_health_sync`, `galaxy_direct_live`, `samsung_health_history`, `health_connect_history`, `historical_imports`, or the aggregate `galaxy` filter as appropriate.
- Added `scripts/verify_android_debug.ps1` so app unit tests, app debug lint, Wear debug lint, app debug assembly, and Wear debug assembly can be reproduced with project-local Gradle/Android state and the SDK AAPT2 override.
- Restarted the production dashboard scheduled task from the edited worktree during the earlier watch-active audit. The main `127.0.0.1:8766` backend exposed the `sync` read model on `/api/live`, served the source-filtered drilldown browser code, and reported glucose current, direct heart current, Galaxy live, and Fitbit synced.
- Fixed the final rendered-label issue found after restart: current glucose now displays `LibreLinkUp follower` instead of the raw `libre_linkup_follower` token, and direct watch samples display `Direct Galaxy Watch`.
- Completed a live Fitbit Air / phone companion troubleshooting pass with USB and wireless debugging enabled on the phone. The phone was paired, all intended Health Connect categories were granted, and a manual sync completed successfully after the server-side fixes.
- Accepted Fitbit-origin Health Connect heart-rate records from the phone companion as adapter `android_health_connect_fitbit`, separate from `google_health_fitbit`, and displayed them under the Fitbit source section with their own provenance.
- Fixed a production database migration miss found during live sync. The existing database had already recorded the v3 schema migration, so a newly added `watch_ingest_rate_events` table was absent and ingest requests crashed into HTTP 502. Schema-current detection now requires that table, and the rate-event methods self-heal by running `init_schema()` if an older live database is missing it.
- Split watch ingest rate limiting into `health_sync` and `live_heart` buckets. A burst of direct-watch live heart batches no longer consumes the same per-minute budget needed by phone Health Connect / Fitbit uploads.
- Added `WatchLivePollingEnabled` / `PERSONAL_STATE_WATCH_LIVE_POLLING_ENABLED`. When disabled, dashboard and MCP reads clear the live-heart demand lease instead of renewing it, and the phone live-demand poll returns `live_requested=false`. This lets the Galaxy Watch charge without being pulled by Personal State while Fitbit/Health Connect sync continues.
- Fixed the PowerShell settings loader so boolean `$false` values are exported to the service environment instead of being skipped as empty values.
- Reclassified the live Fitbit result honestly: Fitbit is synced and source-labeled, but the latest Fitbit heart-rate sample available through Health Connect was outside the Casey biofeedback currentness window. The dashboard now reports `fitbit_heart_rate_stale` instead of incorrectly blaming Google Health sync when phone-side Fitbit data exists.
- Evaluated direct Fitbit reading options. The supportable realtime path is now `fitbit_ble_heart_rate`, using Fitbit Air's standard Bluetooth LE Heart Rate Service after Google Health Share heart-rate mode is enabled. A richer Fitbit-only cloud/API lane can still be evaluated separately as `fitbit_direct_cloud`, but it is context/history until measured cadence proves otherwise.

Remaining before calling Casey Fitbit biofeedback operational:

- The direct Fitbit BLE path is verified live on the phone and is the primary Casey realtime source. Longer field validation remains open for reconnect after range loss, outdoor/gym behavior, battery impact, and no-Galaxy fallback operation.
- Add a first-class `fitbit_direct_cloud` design if direct cloud access is pursued. It must be separate from `fitbit_health_connect_phone` and `google_health_fitbit`, and must state provider cadence, API lifecycle risk, scopes, retention, disconnect behavior, and fallback ordering.
- Do not rely on the Galaxy Watch as a production fallback for Casey. It can remain a separate source panel, but Casey's Fitbit path must stand alone.
- Keep watch live polling disabled while the watch is charging or while evaluating Fitbit-only behavior.
- Build Casey's state estimator from trends, deltas, persistence, source confidence, and conflict handling as documented in `docs/casey-fitbit-air-state-model.md`.

## Verification Status

Latest run on 2026-10-01:

- Full Python test suite after live Fitbit/Health Connect, schema, rate-limit, and watch-polling changes: `90 passed`.
- Focused Fitbit/watch/dashboard regression run after the live fixes: `34 passed` across `tests/test_watch_integration.py` and `tests/test_dashboard.py`.
- Dashboard JavaScript syntax after the live fixes: `node --check src/personal_state_mcp/dashboard_static/app.js` passed.
- Production phone companion sync after the schema fix: completed successfully and uploaded a large Health Connect batch. The document intentionally omits raw health values, account identifiers, device identifiers, and private URLs.
- Scheduled production tasks restored after foreground diagnostics: `Personal State MCP Dashboard` and `Personal State MCP Cloudflare Tunnel` were running.
- Final live dashboard check after scheduled-task restore: glucose stream current; Fitbit source synced with five stored metrics; Fitbit biofeedback state stale for `fitbit_heart_rate_stale`; Galaxy section present but direct-heart status expected to be unavailable while the watch is off-wrist/on charger.
- Watch live-demand database lease after disabling watch polling: cleared to the epoch value, confirming dashboard reads were no longer renewing direct watch live-heart demand.
- Full Python test suite after minimization, label-normalization, and source-filtered drilldown changes: `82 passed`.
- Final rendered dashboard check after restart: current cards and source panels display human source labels, source-filtered drilldown code is served, and the signal board is populated. Live readings were used only for local verification and are not copied into this document.
- Android debug verification script: `BUILD SUCCESSFUL` for `:app:testDebugUnitTest`, `:app:lintDebug`, `:wear:lintDebug`, `:app:assembleDebug`, and `:wear:assembleDebug`; phone and Wear debug APKs were produced.
- Main local dashboard task restarted successfully. `/api/live` now includes top-level `sync`, reports `sync_degraded=False`, serves source-filtered drilldown JavaScript, and reports the expected source states without exposing raw readings in this document.
- Focused source-filtered drilldown regression run: `29 passed` across `tests/test_dashboard.py` and `tests/test_watch_integration.py`.
- Focused post-registry test run: `33 passed` across `tests/test_watch_integration.py`, `tests/test_dashboard.py`, and `tests/test_storage_service.py`.
- Focused degraded-sync dashboard test run: `18 passed` across `tests/test_dashboard.py`.
- Earlier watch-active `/api/live` sustained-source check: 15 consecutive polls reported current glucose, live Galaxy/direct heart, synced Fitbit, and direct heart present.
- Earlier watch-active browser-rendered real-source check: the running local dashboard showed `3 sources reporting`, direct heart live, Fitbit synchronized, and populated same-axis glucose/heart history.
- Dashboard JavaScript syntax: `node --check src/personal_state_mcp/dashboard_static/app.js` passed.
- Python syntax check passed for `source_registry.py`, `service.py`, `dashboard.py`, and `mcp_server.py`; repeated after degraded-sync changes for `dashboard.py`.
- Browser-rendered smoke check: passed for source-labeled Libre, Fitbit/Google Health, and Galaxy/Samsung panels on a synthetic database served by the edited worktree.
- Browser-rendered degraded-sync smoke check: passed on `127.0.0.1:8801` with a synthetic fresh Libre reading and latest `adapter_timeout`; the first-screen chip showed `Stored history only` while preserving the source-labeled Libre value.
- Documentation diff check: passed for this design doc, `README.md`, and `docs/00-DOCUMENTATION-INDEX.md`; Git reported only existing LF-to-CRLF normalization warnings.
- Earlier Android companion verification was blocked by the Gradle-downloaded AAPT2 daemon and then by sandboxed Java compiler file access. The final verified path is to run the Android debug gate outside the sandbox with the project script, which uses the SDK build-tools AAPT2.

Android command:

```powershell
.\scripts\verify_android_debug.ps1
```

## Release Gate For Casey Fitbit Air

Fitbit Air source display is connected, syncing, and direct BLE live heart rate has been verified on the phone. Do not call Casey Fitbit biofeedback operational until all of these are true:

- OAuth consent and scopes are confirmed for the intended account.
- One real-world run or comparable active session is pulled end-to-end without relying on the Galaxy Watch.
- The dashboard displays the values with source, age, and synchronized wording.
- Fitbit heart rate reaches the Casey biofeedback currentness window through `fitbit_ble_heart_rate`, not a watch fallback.
- Libre near-real-time glucose still appears or fails independently with a clear source state.
- Direct Galaxy live heart still appears only within the 10-second rule.
- Google/Fitbit data is source-labeled as Fitbit Air only when Google supplies exact evidence.
- Disconnect behavior is tested for credentials and local data.
- No plaintext credential fallback is active in production behavior.
- Casey reports conflicted/low-confidence physiology when Fitbit and Galaxy disagree materially unless a session-specific calibration event supports one source.
- If direct Fitbit cloud access is added, the UI must distinguish `fitbit_direct_cloud`, `fitbit_health_connect_phone`, and `google_health_fitbit`; freshness/fallback ordering must be explicit.

## Field Log: 2026-10-01 Fitbit Air Live Test

Sanitized sequence of what was tried and learned:

1. Google OAuth consent was completed for the Google Health scopes. The dashboard initially still showed Fitbit as not connected until server-side and phone-side paths were reconciled.
2. The Android phone was connected with developer options, USB debugging, and wireless debugging available. ADB was used to inspect the phone app, confirm pairing and Health Connect grant state, bring the app forward, and tap `Sync now`.
3. The Fitbit phone application was opened and refreshed so it could write recent device records into the phone ecosystem. Personal State cannot force the Fitbit device or Fitbit application to produce a current synchronized heart sample. For realtime Casey use, the phone must receive the standard Bluetooth Heart Rate Service after Fitbit Share heart-rate mode is enabled.
4. The phone companion was already paired and retained its pairing. A debug APK reinstall attempt failed because the build signing identity did not match the installed production package, so the installed app was preserved rather than uninstalled.
5. Foreground dashboard and Cloudflare tunnel sessions were used for diagnostics. Scheduled tasks were stopped during diagnostics and restored afterward.
6. Phone sync first failed with HTTP 502. The server traceback showed `sqlite3.OperationalError: no such table: watch_ingest_rate_events`. The fix was a schema-current check and self-healing migration path for the additive rate-events table.
7. Phone sync then completed successfully, proving the ingest endpoint, HMAC auth, tunnel route, and Health Connect upload path were working.
8. Fitbit Health Connect records were present under the Fitbit source lane, but the newest Fitbit heart-rate record exposed to Personal State was stale relative to the Casey biofeedback window. This proved that synchronized Fitbit data alone is not sufficient for Casey realtime use.
9. Direct Galaxy watch polling caused unacceptable battery drain during testing. `WatchLivePollingEnabled = $false` now turns off server-side live watch demand while preserving Fitbit and other phone Health Connect uploads.
10. The dashboard source summary was corrected so existing phone Fitbit data is shown as synced/stale rather than blocked by a separate Google Health collector error.
11. Direct Fitbit BLE later became live after Share heart rate was enabled. A subsequent Fitbit/Galaxy heart-rate disagreement was checked manually, and the manual pulse supported Fitbit over Galaxy for that moment. The design now treats source disagreement as a Casey confidence state rather than a reason to abandon the Fitbit deployment.

## Direct Fitbit Access Design Note

Direct Fitbit access is now split into two explicit lanes because Casey's realtime biofeedback need outweighs preserving normal Fitbit app sync during a session:

- `fitbit_ble_heart_rate`: direct Bluetooth LE Heart Rate Service (`0x180D`) subscription. Source class `direct_live_vital`. First metric is `vitals.heart_rate` only. It may be labeled live only when the newest measured sample is no more than ten seconds old. It is eligible for Casey realtime biofeedback.
- `fitbit_direct_cloud`: future cloud/API evaluation lane. Source class `synchronized_wearable_cloud_data`. It may provide richer Fitbit-owned context, but it is not assumed realtime until measured cadence proves otherwise.
- `google_health_fitbit`: Google Health synchronized records. Source class `synchronized_wearable_history`. Useful for history and source-of-record context, not live biofeedback.
- `android_health_connect_fitbit`: phone-local Health Connect Fitbit records. Source class `phone_synchronized_fitbit_history`. Useful for corroboration, not live biofeedback unless the record age happens to satisfy the Casey currentness window.

Setup rule: Fitbit Air must be in Google Health's live Share heart rate mode before Personal State expects the standard Bluetooth Heart Rate Service. On the phone, open Google Health > Connections > Fitbit > Share heart rate > Get started, then start the Personal State companion's Fitbit live heart-rate receiver. Official source: https://support.google.com/googlehealth/answer/14236705?hl=en

Fallback rule: never silently fall back to Galaxy Watch for Casey's Fitbit biofeedback. If Galaxy is displayed, name it visibly as Galaxy. If Fitbit BLE is absent or stale, Casey realtime biofeedback is unavailable even when slower Fitbit history exists.

Verification rule: run with Casey's phone and Fitbit during an outdoor run. Confirm that `fitbit_ble_heart_rate` advances at a live cadence without relying on Galaxy, and record range, reconnect behavior, sample age, battery impact, and whether the Fitbit app's later cloud sync recovers. A stale dashboard after a successful live window is a reconnect/session-continuity failure, not proof that synchronized Fitbit data is sufficient.

2026-10-02 field result: the phone companion was updated in place without losing pairing, Bluetooth permissions were granted, and the local receiver/tunnel path was repaired. The phone uploaded 3,376 Health Connect changes successfully. Initial BLE discovery connected to the bonded `Google Fitbit Air` but did not expose Heart Rate Measurement until Google Health's Fitbit Air `Share heart rate` / `Always visible` mode was enabled. After that setup gate was active, the phone companion subscribed to the standard Heart Rate Measurement characteristic (`0x2A37`), received about one sample per second, and uploaded contract-clean `fitbit_ble_heart_rate` observations through the authenticated ingest host. The dashboard verified Fitbit Air `Live`, `Casey biofeedback live`, and the live heart card as `Fitbit direct Bluetooth` with 0-second age while the user was roughly 30 feet from the phone for about 10 minutes. A later dashboard check showed Fitbit 15+ minutes stale because the phone's Fitbit BLE foreground service had stopped; restarting the receiver made Fitbit live again. The service now treats disconnect as a reconnect/rescan condition. A frontend source-boundary fix also prevents Fitbit live heart rate from making the Galaxy panel appear live; Galaxy remains recorded unless its own `wear_health_services` sample is current.

## Notion Source Links

- Wearable Exploration - Google Fitbit Air (2026-07-26): https://app.notion.com/p/3ad02b49fdf281c2a6fec5a56a60a5ca
- Wearable Track - Independent Project Plan (Fitbit Air / Google Health API): https://app.notion.com/p/3ad02b49fdf281e5a4fae70f6229e758
- ChatGPT Health (OpenAI) privacy-gap reference: https://app.notion.com/p/3ad02b49fdf28137a97ac73f68fe4d0d
- Dev & Harness wiki verification discipline: https://app.notion.com/p/3e002b49fdf281949903d6d6158f2556

