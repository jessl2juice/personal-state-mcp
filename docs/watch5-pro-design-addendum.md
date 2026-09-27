# Galaxy Watch5 Pro Integration Design Addendum

Status: revision 2.1 pending final Fable gate  
Date: 2026-09-24  
Scope: read-only Galaxy Watch5 Pro data in Personal State MCP and its private dashboard

Revision: 2.1

## Goals

- Show every Galaxy Watch5 Pro and Samsung Health category that Samsung currently synchronizes to Android Health Connect.
- Preserve measurement time, companion-observed time, server-ingest time, source application, source device, data age, recency, and completeness caveats.
- Persist normalized long-term history independently of Samsung Health's display windows.
- Make selected watch context available to agents through stable MCP contracts.
- Keep the integration read-only and informational. It must not diagnose, alarm, score risk, recommend treatment, or change Samsung Health data.
- Make setup understandable without requiring the user to operate developer tooling.

## Non-goals

- Replacing Samsung Health, Samsung Health Monitor, emergency features, or official device alerts.
- Reading raw ECG, PPG, accelerometer, or other research-grade sensor streams.
- Inferring medical conditions, causes, sleep disorders, arrhythmias, fitness readiness, or treatment.
- Publishing the Android companion through Google Play in the first release.
- Circumventing Samsung regional, device, account, or partner-program restrictions.

## Verified Platform Boundary

The first adapter uses Android Health Connect on the phone paired with the watch:

`Galaxy Watch5 Pro -> Samsung Health on phone -> Health Connect -> Personal State companion -> private ingest endpoint -> normalized SQLite history -> dashboard and MCP`

Health Connect does not run on the watch. Samsung Health performs the watch-to-phone and phone-to-Health-Connect synchronization. The timing is vendor-controlled and can lag, especially for continuous heart-rate data.

Samsung currently documents Health Connect synchronization for steps, blood glucose, blood oxygen, blood pressure, exercise sessions, exercise calories, exercise distance, exercise heart rate, exercise power, exercise speed, exercise VO2 max, general heart rate, nutrition, sleep sessions and stages, weight, body fat, basal metabolic rate, and height. The companion requests read access only for the documented mappings the user selects. A Health Connect type existing does not mean Samsung exports it; resting heart rate, HRV, skin temperature, floors, active time, and stress remain unavailable unless the capability matrix is revised from new Samsung documentation or explicit real-device evidence.

Samsung Health can hold additional Watch5 Pro data that is not currently synchronized through Health Connect, including some activity summaries, floors climbed, the Samsung stress score, energy-derived values, irregular rhythm notifications, sleep apnea, and richer Samsung-specific sleep fields. The dashboard must label these as unavailable through the current adapter, not as zero or absent from the user.

Stress is a required first-class Personal State availability category, but only a vendor-provided stress observation may populate it. The system must not derive or estimate a stress score from heart rate, heart-rate variability, sleep, electrodermal activity, or behavior. Samsung's current official Health Connect mapping and Samsung Health Data SDK type list do not expose the Samsung Health stress score, so the first release reports `not_exported_by_samsung_mapping` with no observation value or recency classification.

A future Samsung Health Data SDK adapter can cover additional Samsung-only categories, but production use requires Samsung's current partner and distribution requirements. Developer mode is not a production path.

## Components

### Android companion

A small Kotlin Android application runs on the paired phone. It has no write permissions for health data. Its responsibilities are:

- Explain and request explicit Health Connect read permissions by category.
- Show per-category permission and last-sync status.
- Read new or changed Health Connect records in bounded batches.
- Normalize records into a versioned JSON envelope.
- Upload over HTTPS to the private Personal State ingestion route.
- Store only sync checkpoints and encrypted connection credentials locally.
- Support a visible `Sync now` command and periodic background work where Android permits it.

### Direct live heart-rate companion

A second module runs a visible health foreground service on the Galaxy Watch5 Pro and uses Wear OS Health Services `ExerciseClient` for continuous heart-rate updates. It requests heart rate only, disables GPS, enables `HEART_RATE_5_SECONDS` batching for screen-off delivery, and relays the newest valid reading through the Wearable Data Layer to the paired phone. Google Play services requires the watch and phone packages and signing certificates to match; another application cannot address this app-private channel. The phone, not the watch, holds the upload credentials and performs the authenticated upload.

This direct path is intentionally user-started and visible through an ongoing notification and Stop control. It uses the platform exercise session internally, resumes after service or watch restarts only when the user has granted all-time health access, and yields rather than replacing another app's active exercise. Continuous collection uses more battery and cannot measure through off-body or poor-contact periods. It does not replace Samsung alerts or claim safety monitoring. Only a direct sample measured within ten seconds may populate the dashboard's live heart-rate value, covering the configured five-second delivery cadence while withholding the value after two expected deliveries are missed.

Direct observations use source package `ai.clinicianassist.personalstate`, `active` recording method, and positive device-model evidence. The server accepts this source only for `vitals.heart_rate`; all other current watch metrics must retain Samsung Health provenance. Historical Health Connect records and direct live records coexist on the same recorded-time axis without changing their source attribution.

The first release filters reads to the Samsung Health data origin. A Samsung Health origin is not proof that a record came from the watch. Every record carries one of these attribution states: `watch_confirmed`, `samsung_health_unattributed`, `phone`, `manual`, `external_device`, or `unknown`. `watch_confirmed` is used only when Health Connect metadata positively identifies the paired Watch5 Pro. The companion preserves the source package, Health Connect recording method, and non-identifying device type/model evidence. The UI says `Samsung Health` for unattributed records.

Initial collection imports the full Health Connect range when the user grants history permission and falls back to 30 days otherwise. The Samsung Health Data SDK path performs a complete available-history backfill. Subsequent synchronization uses independent change checkpoints with pagination. Create and update events are version-aware; older replays cannot overwrite newer records. Delete events create a short-lived local tombstone and remove the observation from dashboard and MCP results. Change-token expiry triggers reconciliation and a visible `reconciling` completeness state. A companion reinstall creates a new installation identifier and forces reconciliation rather than trusting old checkpoints.

`READ_HEALTH_DATA_HISTORY` and `READ_HEALTH_DATA_IN_BACKGROUND` are optional, separately explained permissions. The companion remains useful with foreground-only sync and the normal 30-day history window. It declares no Health Connect write permissions and no route/location permission. Long-term history is guaranteed only from successful collection onward unless older upstream history is both present and explicitly authorized.

The companion keeps no queued health payload. On upload failure it retains encrypted checkpoints only and rereads Health Connect on retry. Credentials, checkpoints, health payloads, and diagnostics are excluded from Android backup, clipboard, screenshots where practical, crash reporting, and logs. WorkManager jobs use connected-network and battery-not-low constraints, bounded pages, exponential backoff, and a visible last-attempt/result state.

Android Keystore protects the device credential at rest. It does not protect credentials or in-memory values from a compromised or unlocked device while the app is running. Logs must never contain health payloads, Cloudflare credentials, pairing secrets, record identifiers, or account identifiers.

### Ingestion endpoint

The existing loopback dashboard process gains a dedicated endpoint for normalized watch batches. A public deployment uses a separate Cloudflare Access application and ingest hostname, with a narrowly scoped service token. The dashboard hostname remains protected by the user's interactive identity policy.

The dedicated Cloudflare application uses a Service Auth-only policy, a unique audience, and one revocable service token per paired phone. `cloudflared` validates the Access assertion signature, issuer, audience, expiration, and service-token identity before proxying the request. The origin independently validates the hostname/path/method and a per-device HMAC credential. Dashboard and ingest applications never share an Access audience or credential.

Each upload includes `X-PSM-Device-Id`, `X-PSM-Timestamp`, `X-PSM-Nonce`, `X-PSM-Batch-Id`, and `X-PSM-Signature`. The signature is base64url HMAC-SHA256 over `timestamp + "\\n" + nonce + "\\n" + batch_id + "\\n" + exact_request_body`. The origin requires a request timestamp within 300 seconds, a unique nonce and batch ID, and a valid per-device secret using constant-time comparison. Nonces are retained for 24 hours, capped at 20,000 per device, and pruned transactionally. Credential rotation permits a maximum 24-hour overlap; a lost phone revokes its Cloudflare token and origin credential without affecting the dashboard or another phone.

Origin checks enforce all of the following:

- Only the configured ingest hostname can call the ingest endpoint.
- The dashboard hostname cannot call the ingest endpoint.
- The ingest hostname serves no dashboard pages or dashboard APIs.
- A bounded request size, bounded batch count, strict content type, schema version, metric allowlist, unit validation, and timestamp validation are enforced.
- Device credentials are compared in constant time and requests are replay-protected.
- Duplicate records are idempotent.
- Malformed payloads fail as a unit and store no partial batch.
- Responses never echo submitted observations, health values, configuration, detailed parser diagnostics, or secrets.
- The ingest hostname exposes only a minimal unauthenticated liveness response and authenticated ingestion; it cannot serve dashboard content or read APIs.

Cloudflare configuration and service-token creation are deployment actions, not application defaults. They require explicit user confirmation at the moment credentials are created.

### Normalized storage

The SQLite schema gains `health_observations` and `device_sync_runs` tables. Existing glucose storage remains unchanged.

Each normalized observation contains:

- Stable identifier derived with a keyed HMAC over adapter, Health Connect record identifier, metric, and source.
- Metric key and category.
- Scalar numeric value and unit when applicable.
- A schema-validated value for series, sessions, stages, or composite measurements. No route or location field is allowed.
- Start, end, and/or point measurement timestamps.
- Companion-observed timestamp and server-ingested timestamp.
- Adapter, vendor, source application, source device hash, and source record hash.
- Data origin and Health Connect metadata needed for provenance, with direct identifiers hashed before persistence.
- Upstream last-modified time/version, recording method, schema version, attribution state, and optional quality/completeness flags.

Database schema migrations are versioned, idempotent, and backed up before migration. Watch tables include observations, per-type/origin checkpoints, sync runs, tombstones, device credentials, replay nonces, and category availability. The watch feature is disabled by default until synthetic end-to-end tests pass. Rollback disables ingestion and MCP exposure while preserving export/delete access to already collected watch data.

Raw upstream payloads are not stored. The companion sends only the normalized fields required by the contract. Exercise route coordinates and any `route`, `location`, `latitude`, `longitude`, or encoded-polyline field are rejected in the first release.

The local database threat boundary is the signed-in Windows user and administrators on the host. Before real watch data is provisioned, full-disk/device encryption must be verified or the user must explicitly accept the residual at-rest risk. Watch retention defaults to 3,650 days and is displayed in the dashboard; it is configurable only by explicit local user action. Export, deletion, backup, restore, and retention operations cover observations, availability, sync metadata, tombstones, and derived aggregates.

## Versioned Ingest Contract

Top-level schema `personal-state-watch-batch/v1` is defined exactly by `docs/watch-ingest-schema-v1.json`, which is normative. It is a closed object with: `schema_version`, `batch_id`, `installation_id`, `generated_at`, `changes`, and `availability`. `changes` is a closed union of `upsert` with a typed observation or `delete` with upstream identity and version. `availability` is a closed object containing metric, state, evidence, checked time, and category coverage. Unknown fields or schema versions reject the entire batch with a retryable `upgrade_required` or non-retryable `invalid_payload` result.

Observations are a closed discriminated union of `point`, `interval`, `series`, `session`, `composite`, and `aggregate`. All variants require `record_id`, `metric`, `record_kind`, `observed_by_companion_at`, `upstream_last_modified_at`, `source_package`, `recording_method`, and `attribution`. `upstream_last_modified_at` is explicitly nullable when Health Connect omits it. Point/composite records require `measured_at`; interval/session/aggregate records require `start_at` and `end_at`; series records require bounded timestamped samples. The normative schema defines each metric payload, nullability, unit enum, value bound, field length, and variant timing. Timestamps retain original zone offsets when present.

Validation limits are: 1 MiB uncompressed JSON, no content encoding, 500 changes per batch, 2,000 samples or stages per record, JSON depth 12, strings 512 characters, finite numbers only, and timestamps from 2015-01-01 through server time plus 10 minutes. Duplicate JSON keys, invalid Unicode, unknown fields, unknown metrics, unknown units, non-finite values, and invalid ranges reject the full transaction. Metric-specific bounds in the schema prevent parser/resource abuse and are not clinical normal ranges. The origin rate limit is 40 authenticated batches per device per minute, enough for the visible direct-heart stream with bounded retry headroom, and returns a sanitized retryable response.

## Metric Contract

Canonical metric keys include:

- `activity.steps`
- `activity.exercise_calories`
- `activity.distance`
- `activity.exercise_session`
- `activity.exercise_power`
- `activity.speed`
- `activity.vo2_max`
- `vitals.heart_rate`
- `vitals.resting_heart_rate`
- `vitals.oxygen_saturation`
- `vitals.blood_pressure`
- `vitals.skin_temperature`
- `vitals.heart_rate_variability`
- `sleep.session`
- `wellness.stress`
- `body.weight`
- `body.body_fat`
- `body.basal_metabolic_rate`
- `body.height`
- `nutrition.intake`

Blood glucose from Health Connect is accepted as `vitals.blood_glucose` but is not merged with Libre glucose. Libre remains the primary glucose context source and safety boundary. Any comparison is descriptive and source-labeled.

Stress availability is metadata, not an observation. In revision 2.1 the static state is `not_exported_by_samsung_mapping`, with the checked capability version and no recency. A future vendor stress observation must carry provider, provider metric name and version, raw score, scale minimum/maximum, display label, measured interval, and provenance. Scores from different providers are never normalized or compared. Stress is excluded from `health.current_state()` and agent access unless the user separately opts in.

### Capability Matrix v1 (checked 2026-09-24)

| Canonical metric | Health Connect type | Samsung documented export | Likely Watch5 Pro source | Permission | First-release state |
| --- | --- | --- | --- | --- | --- |
| `activity.steps` | `StepsRecord`/aggregate | Yes, all steps | Mixed watch/phone | Read steps | Available, attribution required |
| `activity.exercise_calories` | `TotalCaloriesBurnedRecord` | Exercise tracker only | Watch or phone exercise | Read total calories | Available, exercise only |
| `activity.distance` | `DistanceRecord` | Exercise tracker only | Watch or phone exercise | Read distance | Available, exercise only |
| `activity.exercise_session` | `ExerciseSessionRecord` | Yes | Watch or phone | Read exercise | Available, attribution required |
| `activity.exercise_power` | `PowerRecord` | Exercise tracker only | Watch/external sensor | Read power | Available if present |
| `activity.speed` | `SpeedRecord` | Exercise tracker only | Watch/phone GPS | Read speed | Available without route coordinates |
| `activity.vo2_max` | `Vo2MaxRecord` | Exercise tracker only | Watch | Read VO2 max | Available if present |
| `vitals.heart_rate` | `HeartRateRecord` | Yes | Watch or phone/manual | Read heart rate | Available, attribution required |
| `vitals.oxygen_saturation` | `OxygenSaturationRecord` | Yes | Watch/manual | Read oxygen saturation | Available, attribution required |
| `vitals.blood_pressure` | `BloodPressureRecord` | Yes, region/device dependent | Watch/manual | Read blood pressure | Available if present, unattributed by default |
| `sleep.session` | `SleepSessionRecord` | Yes | Watch/phone | Read sleep | Available, attribution required |
| `body.weight` | `WeightRecord` | Yes | Usually manual/scale | Read weight | Samsung Health, not presumed watch |
| `body.body_fat` | `BodyFatRecord` | Yes | Watch BIA/scale | Read body fat | Available, attribution required |
| `body.basal_metabolic_rate` | `BasalMetabolicRateRecord` | Yes | Derived/body measurement | Read BMR | Samsung Health, not presumed watch |
| `body.height` | `HeightRecord` | Yes | Usually profile/manual | Read height | Samsung Health, not presumed watch |
| `nutrition.intake` | `NutritionRecord` | Yes | Manual/app | Read nutrition | Samsung Health, not watch |
| `vitals.resting_heart_rate` | `RestingHeartRateRecord` | Not in Samsung mapping | Unknown | Read resting HR | `not_exported_by_samsung_mapping` |
| `vitals.heart_rate_variability` | `HeartRateVariabilityRmssdRecord` | Not in Samsung mapping | Unknown | Read HRV | `not_exported_by_samsung_mapping` |
| `vitals.skin_temperature` | `SkinTemperatureRecord` | Not in Samsung mapping | Watch during sleep | Read skin temperature | `not_exported_by_samsung_mapping` unless runtime evidence |
| `wellness.stress` | None | No | Samsung proprietary | None | `not_exported_by_samsung_mapping` |
| `activity.floors` | `FloorsClimbedRecord` | Not in Samsung mapping | Watch/phone | Read floors | `not_exported_by_samsung_mapping` |
| `activity.active_time` | None | Activity tracker not synchronized | Watch/phone | None | `not_exported_by_samsung_mapping` |

Static capability claims and runtime availability remain separate. Runtime availability uses only: `available`, `permission_required`, `platform_feature_unavailable`, `not_exported_by_samsung_mapping`, `no_observation`, `source_configuration_unverified`, `companion_read_failed`, and `unknown`. Every state includes evidence and `checked_at`. Category coverage also includes `window_start`, `window_end`, and explicit booleans for `truncated`, `backfill_limited`, `interrupted`, and `reconciling`. Granted permission with no record means `no_observation`, never proof of failed watch/Samsung sync.

Units are canonicalized at the companion boundary: beats/minute, percent, count, meters, kilocalories, watts, meters/second, milliliters/minute/kilogram, millimeters of mercury, degrees Celsius, kilograms, centimeters, and milliseconds or seconds for durations. Revision 2.1 does not retain original-unit metadata; the typed canonical value is the persisted contract.

## Recency, Sync, and Completeness

Every observation exposes:

- `measured_at` or `start_at`/`end_at`
- `observed_by_companion_at`
- `ingested_at_server`
- `measurement_age_seconds`
- `observed_path_lag_seconds`, explicitly including unknown Samsung/watch synchronization and collection delay
- `observation_recency.status`
- `observation_recency.reason`
- provenance

Recency is metric-specific and recomputed at response time. Series use the latest valid sample time and intervals use their end time. Records use neutral states: `latest_recorded`, `recent`, `old`, or `unknown`; only heart-rate samples use `fresh`, because that policy is explicit. Initial display windows:

- Heart rate: fresh within 15 minutes, recent within 2 hours, then old.
- Oxygen saturation: recent within 2 hours, then latest recorded.
- Steps and activity aggregates: recent within 12 hours, then latest recorded.
- Stress in revision 2.1 has availability only and no recency.
- Exercise sessions: recent for 24 hours, then latest recorded.
- Sleep: recent for 36 hours, then latest recorded.
- Body composition and other point measurements: latest recorded, with exact age.

The API must distinguish:

- No permission.
- Permission granted but no record supplied.
- Record supplied but old.
- Companion has not successfully read or uploaded recently.
- Category unsupported by Health Connect or not exported by Samsung.

Companion read status, server upload status, and observation recency are separate. Absence cannot prove Samsung Health or watch synchronization failure. Future timestamps within tolerance are labeled clock-skewed; those outside tolerance are rejected. All storage is UTC while original offsets are retained, so timezone changes and daylight-saving transitions do not rewrite source time. No state may be represented as a medical alert.

## MCP Surface

Two read-only tools are added behind a collection-independent agent exposure allowlist:

- `health.watch()` returns a compact latest summary by category, companion sync health, observation recency, provenance, permission/availability states, and limitations.
- `health.watch_recent(metric, hours=24, limit=200, cursor=None)` requires an explicit allowed metric. `hours` must be from 0.25 through 720 (30 days); requests outside policy fail with `invalid_range`. Page size is 1 through 200. The server-issued cursor is HMAC-protected, bound to host/metric/range/exposure policy, expires after 15 minutes, and fails closed as `invalid_cursor` if modified or expired. Results return the effective range, next cursor, completeness flags, and per-observation recency/provenance.

`health.current_state()` gains a compact watch summary limited to user-authorized recent heart rate, today's steps, last sleep session, last exercise session, and latest oxygen saturation when available. It must not infer why the user seems off. Routes, nutrition, body composition, blood pressure, blood glucose, and stress are excluded from agents by default even when collected for the dashboard. The default allowlist is heart rate, steps, sleep session, exercise session, and oxygen saturation. Every MCP call uses the existing host policy, rate limit, audit log, envelope, and safety notice and returns the effective non-secret exposure policy.

The existing glucose tools and threshold behavior do not change.

## Dashboard

The dashboard gains a Watch section designed for scanning rather than diagnosis:

- Pairing state, last successful companion Health Connect read, and last successful server upload. Samsung/watch synchronization status remains unknown.
- Today: Health Connect aggregate steps and exercise-only calories/distance when available.
- Heart rate: latest value, observed range, and time series with measurement recency, companion-observed time, and server-ingest time.
- Sleep: latest session duration and stages.
- Oxygen: latest and recent range.
- Stress: explicit `not_exported_by_samsung_mapping` state in revision 2.1. A future authorized source may show neutral provider-specific history, never an inferred or cross-provider trend.
- Exercise: recent sessions and source-labeled details.
- Body: latest weight, body fat, BMR, and height when present.
- Other Samsung/Health Connect observations in a searchable recent-data table.
- Coverage panel listing available, permission-missing, unsupported, and Samsung-not-exported categories.

Every card shows attribution, measurement age, companion-observed age, and server-ingest age. Charts do not connect across defined gaps and label ranges as observed samples. The heart-rate path uses a centered, weighted seven-sample display curve followed by monotone visual interpolation only inside a continuous recorded segment. This presentation does not change stored values or timestamps; current values, tooltips, tables, exports, and API responses remain raw recorded measurements. Across every heart-rate source, the curve stays connected across gaps of up to one minute and shows a break when the gap is longer than one minute. Missing, old, and unavailable data use neutral styling. The page states that Samsung Health and Samsung Health Monitor remain authoritative. The page must remain useful before a phone is paired. Empty states explain the next user action without showing fake or zero-valued measurements.

Daily aggregates use the configured user timezone, preserve the source query window/offsets, and are stored as `aggregate` records distinct from raw observations. The companion uses Health Connect aggregate APIs so platform data-origin priority and deduplication rules apply. Midnight, daylight-saving, revised-record, and mixed-origin cases are tested.

## Agent Behavior Contract

Agents may:

- Use recent watch data as explicitly labeled conversational context.
- Mention measurement time, source, observation recency, and the fact that Samsung/watch synchronization delay is unknown.
- Ask the user whether they want to check Samsung Health or the official medical app for authoritative detail.

Agents must not:

- Attribute confusion, mood, fatigue, sleep, heart rate, oxygen, blood pressure, or any other state to a cause.
- Treat missing or old records as normal or current measurements.
- Diagnose, score risk, alarm, recommend treatment, or automate action.
- Modify Health Connect, Samsung Health, thresholds, permissions, credentials, or safety instructions.
- Conflate Health Connect blood glucose with Libre data.

## Threat Model

Protected assets are health observations, account linkage, device identity, service credentials, and long-term history.

Primary threats and controls:

- Credential theft: Android Keystore, OS-scoped server secret storage, no logs, no repository secrets, immediate revocation path.
- Public endpoint discovery: Cloudflare Access service authentication plus origin-side hostname, path, credential, size, and schema enforcement.
- Replay and duplication: deterministic observation IDs, idempotent inserts, bounded timestamp skew, mandatory unique batch identifier, mandatory nonce, and mandatory body HMAC.
- Payload abuse: strict parser, allowlisted fields/metrics/units, bounded nesting, bounded series length, bounded request size, transactional insert.
- Cross-surface privilege: separate dashboard and ingest Access applications and audiences; ingest route cannot serve dashboard data.
- Excessive collection: read-only, user-selected permissions, documented category list, no raw sensor streams.
- Local database disclosure: current-user filesystem ACLs, hashed upstream identifiers, no raw payload retention, documented backup/delete controls.

## Failure Modes

- Watch disconnected or unavailable: display the latest recorded value with its measurement time, companion-observed time, server-ingest time, and neutral recency; do not claim the watch sync state.
- No new Samsung record: show observation age plus companion read/upload times; do not claim the watch or Samsung failed to sync.
- Health Connect permission revoked: stop reading that category and report `permission_required`.
- Android background work delayed: preserve a visible last successful sync time; never claim continuous monitoring.
- Cloudflare credential revoked or expired: reread Health Connect after credential recovery; no payload queue is retained.
- Partial category support: continue other categories and report exact unsupported categories.
- Schema mismatch: reject the batch, retain no payload, keep the checkpoint unchanged, and reread Health Connect after the companion is upgraded.
- Database failure: return no success acknowledgement; the companion retries with idempotent IDs.
- Health Connect unavailable/outdated/disabled: retain checkpoints, show platform state, and do not downgrade permissions silently.
- Samsung Health absent/signed out/not writing: report `source_configuration_unverified`; do not infer account state from missing records.
- Clock rollback/timezone change: reject invalid request clocks, retain UTC checkpoints, and require a manual sync after large rollback.
- Disk full/SQLite lock/corruption/migration failure: fail the full transaction, retain the previous schema backup, and disable ingest if integrity cannot be proven.
- Multiple phones: use isolated device credentials/checkpoints and deterministic upstream IDs so legitimate duplicate records deduplicate without merging device state.
- Server restart or acknowledgement loss: committed batch IDs return idempotent success; uncommitted batches retry safely.

## Observability and Privacy

Operational telemetry is local and contains counts, durations, category names, status codes, and keyed device identifiers only. It contains no measurement values. The dashboard shows last successful companion read/upload, records accepted, records deduplicated, and the last sanitized error code. Cloudflare, application, reverse-proxy, and crash logs must not contain request bodies, credentials, query-contained health data, or response payloads. Dashboard, ingest, and export responses use `Cache-Control: no-store` and restrictive CSP/frame policies.

The user can export and delete watch history separately from Libre history. The dashboard displays the configured 3,650-day watch retention before pairing. The system does not upload health history to third-party analytics.

## Deployment and Pairing

1. Install the signed companion on the paired Android phone.
2. Enable Samsung Health to write selected categories to Health Connect.
3. Grant the companion read-only Health Connect permissions.
4. Verify full-disk/device encryption or record explicit user acceptance of residual local at-rest risk.
5. Create a dedicated Cloudflare Access service token and ingest application after explicit user confirmation.
6. Pair the companion by provisioning the ingest URL and credentials into encrypted storage.
7. Run a foreground sync and verify source, timestamps, values, update/delete behavior, and deduplication.
8. Enable background sync if the user grants the additional permission and Android supports it.

No public unauthenticated ingestion route is permitted.

## Test Plan

- Model and schema tests for scalar, interval, series, and composite observations.
- Ingest validation tests for every metric, unit conversion, timestamp boundary, body limit, batch limit, duplicate, malformed JSON, unknown field, unsupported schema, and transactional rollback.
- Contract tests for duplicate keys, non-finite values, invalid Unicode, excessive depth, route/location rejection, and HMAC timestamp/nonce replay.
- Host/path isolation tests for dashboard and ingest surfaces.
- Authentication tests for missing, wrong, and rotated device credentials.
- MCP contract tests for no data, mixed recency, unavailable permissions, partial support, and large history limits.
- Dashboard rendering tests for paired, unpaired, old, partial, and dense histories at desktop and mobile widths.
- Android unit tests for normalization and checkpoint logic.
- Android manifest test proving there are no write, route, or location permissions.
- Android instrumented tests for permission denial/revocation and encrypted credential persistence.
- End-to-end test with synthetic Health Connect records before any real-data test.
- Real-device verification on the user's paired phone only after explicit permissions are granted.
- Mixed-origin attribution tests proving Samsung Health origin alone never becomes `watch_confirmed`.
- Create/update/delete, out-of-order replay, change-token expiry, pagination, reconciliation, and acknowledgement-loss tests.
- Agent exposure tests proving collected but unauthorized categories never appear in MCP responses.
- Stress tests proving unsupported stress has availability only and no value or derived computation.
- Privacy tests proving payloads and credentials are absent from logs, backup, crash text, and HTTP errors.
- Retention tests removing observations, aggregates, sync metadata, tombstones, replay state, and linked device state.

## Future Samsung Path

After the Health Connect path is stable, evaluate Samsung Health Data SDK partnership or another explicitly authorized Samsung interface for floors climbed, activity summary, energy score, the Samsung stress score if Samsung exposes it, richer sleep data, irregular rhythm notification metadata, sleep apnea, and other Samsung-only records. That adapter must remain read-only, use the same normalized observation contract, and undergo a separate privacy and safety review. Raw EDA or HRV must not be transformed into a home-grown stress score.

## Sources

- Samsung Health Connect FAQ: https://developer.samsung.com/health/health-connect-faq.html
- Samsung Health and Health Connect mapping: https://developer.samsung.com/health/blog/en/accessing-samsung-health-data-through-health-connect
- Samsung Health Data SDK overview: https://developer.samsung.com/health/data/overview.html
- Samsung Health Data SDK data types: https://developer.samsung.com/health/data/guide/features/data-types.html
- Android Health Connect data types: https://developer.android.com/health-and-fitness/health-connect/data-types
- Android Health Connect read guidance: https://developer.android.com/health-and-fitness/health-connect/read-data
- Android Keystore: https://developer.android.com/privacy-and-security/keystore
- Cloudflare Access service tokens: https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/
