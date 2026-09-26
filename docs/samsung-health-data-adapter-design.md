# Samsung Health Data Adapter Design

Status: revision 5, licensed reader implemented; real-device validation pending

Date: 2026-09-26

## Purpose

Extend Personal State beyond its existing Health Connect and direct Wear OS heart-rate paths so the owner and authorized clinicians can review additional facts recorded by Samsung Health and Samsung Health Monitor. The first target device is the Galaxy Watch5 Pro (SM-R920) paired with a Pixel phone running Samsung Health.

The adapter is a read-only personal history collector. It is not an alarm, diagnostic, or treatment system. Samsung Health, Samsung Health Monitor, the Galaxy Watch, and Libre remain authoritative for their own notices and safety functions.

## User stories

- As the owner, I can see when a measurement occurred, when the phone read it, when the server received it, which adapter read it, and which Samsung source recorded it.
- As the owner, I can see sleep, blood oxygen, skin temperature, Energy Score, and vendor-issued health findings without confusing an old record with a current reading.
- As a primary-care clinician, I can review source-labeled history and export it without interpreting missing data as a normal result.
- As an endocrinologist, I can compare recorded glucose with sleep, activity, oxygen, temperature, and heart-rate history on a common time axis without the product claiming causation.
- As a cardiologist, I can review Samsung-issued irregular-rhythm notification history while the system clearly distinguishes a notification from an ECG tracing or diagnosis.
- As a privacy-conscious user, I can retain a category in my private history while withholding it from agents.

## Goals

1. Add an optional Samsung Health Data SDK adapter to the Android phone companion.
2. Preserve existing v1 Health Connect and direct Wear clients unchanged.
3. Add a v2 signed ingestion envelope with durable adapter provenance.
4. Preserve Samsung's native timing, local-day, series, association, and enum semantics.
5. Persist every source observation independently and make any presentation grouping reversible.
6. Store long-term history with event, phone-read, server-ingest, and coverage times.
7. Add private dashboard and export support for Samsung-only records.
8. Keep new enriched and clinical metrics denied to MCP agents by default.

## Non-goals

- No diagnosis, causality claim, risk score, or treatment recommendation.
- No recreation of Samsung algorithms.
- No raw ECG storage or interpretation.
- No home-grown sleep-apnea, rhythm, readiness, or stress detection.
- No inferred stress score from heart rate, HRV, sleep, activity, or other signals.
- No silent permission grant, Samsung account sign-in, partner enrollment, or acceptance of Samsung legal terms.
- No health-data write access.
- No location, exercise route, contacts, microphone audio, or snore audio collection.

## Provider and distribution gates

The implementation is pinned to Samsung Health Data SDK 1.1.0, Samsung Health 6.30.2 or later, Android 10 or later, and Java 17. SDK 1.1.0 is required for the documented sleep-apnea and irregular-rhythm types.

The SDK binary is licensed separately. It must remain outside Git and public source archives. A human account owner must accept Samsung's terms. Developer mode is for development on the owner's device only. Public distribution requires Samsung partner approval, approved data-type scope, and registration of package `ai.clinicianassist.personalstate` with the release-signing certificate.

There are two delivery gates:

1. The v2 server contract, migrations, dashboard, fake adapter, fixtures, build isolation, and tests may ship without the proprietary SDK.
2. The concrete Samsung reader may be compiled and device-tested only after the owner legitimately obtains the SDK. A Samsung-enabled public APK may ship only after the license and Samsung authorization permit it.

The concrete reader lives in `app/src/samsungSdk` and is excluded from ordinary builds. Gradle adds that source set and the local AAR only when `SAMSUNG_HEALTH_DATA_SDK_AAR` or the `samsungHealthDataSdkAar` Gradle property points to a valid file. The AAR is ignored by Git and is never copied into source or release archives. The signed release helper exposes this as `-SamsungSdkAar`.

## Architecture

```text
Galaxy Watch5 Pro
  -> Samsung Health / Samsung Health Monitor on Pixel
     -> Samsung Health Data SDK adapter (Samsung-only and richer records)
     -> Health Connect adapter (portable records and fallback)
  -> Personal State phone companion
     -> v1 Health Connect upload, unchanged
     -> v2 adapter-aware upload
  -> Personal State persistence
     -> source records retained independently
     -> reversible presentation grouping
     -> private dashboard and export
     -> explicit MCP projection
```

The phone companion is the only component with Samsung permissions. The server receives no Samsung credential, session, or account token.

## Versioned ingestion

### v1 compatibility

`personal-state-watch-batch/v1` remains accepted without any field changes. During ingestion the server assigns the canonical adapter identity:

```json
{"id":"android_health_connect","version":"legacy-v1"}
```

The direct Wear heart-rate source is assigned `wear_health_services` based on its accepted source package. Existing v1 ids, signatures, clients, tests, and responses continue to work.

### v2 envelope

`personal-state-watch-batch/v2` is a new closed schema. Its top level retains the v1 batch fields and adds `adapter`. Every observation, deletion, and availability item also carries the same closed adapter object:

```json
{
  "schema_version": "personal-state-watch-batch/v2",
  "adapter": {"id": "android_samsung_health_data", "version": "1.1.0"},
  "batch_id": "uuid",
  "installation_id": "opaque installation id",
  "generated_at": "RFC3339 timestamp",
  "changes": [],
  "availability": []
}
```

Allowed adapter ids are `android_health_connect`, `android_samsung_health_data`, `wear_health_services`, and `synthetic_test`. The production endpoint rejects `synthetic_test`. An item's adapter must exactly match the envelope adapter. Mixed-adapter batches are rejected.

v2 observations add optional `association_hash` and support `record_kind: daily` with required `local_date` and `zone_offset`. Deletions include adapter identity so one adapter cannot delete another adapter's record. Availability includes adapter identity and `coverage_checked_at`.

A `source_change` is the atomic v2 change unit. It contains one provider data-type change and every normalized record derived from it. For Samsung sleep, it contains the summary, all sessions, score when present, associated oxygen, associated temperature, and an association manifest. The manifest is the complete set of `{metric, record_hash}` members derived from the parent, including the summary. An upsert replaces that association manifest atomically. A provider parent delete sends an empty manifest and an adapter-scoped association cascade, tombstoning every previously stored member. No member may be split across two source changes.

## Persistence and migration

Database migration 3 is additive and idempotent:

- add non-null `adapter_id`, `adapter_version`, and `identity_namespace_id` columns to health observations after backfilling v1 rows;
- add nullable `association_hash`;
- rebuild tombstones with `(adapter_id, observation_id)` identity;
- rebuild availability with `(installation_hash, adapter_id, metric)` primary key;
- add adapter identity to sync-run metadata and indexes;
- create a same-volume migration backup before migration.

Each source observation is retained independently. v2 uploads never contain a raw Samsung provider or device id. Pairing v2 adds a server-generated 256-bit `record_identity_key` and an opaque UUID `identity_namespace_id`. The server stores the key in its secret store. After import, the phone stores both values in Android-Keystore-encrypted pairing state. An authorized reinstall obtains a newly generated short-lived pairing file for the same active identity namespace, preserving stable identities.

The companion computes lowercase hexadecimal HMAC-SHA256 values over UTF-8 strings with NUL separators:

- record hash: `HMAC(K, "provider-record\0" + adapter_id + "\0" + source_package + "\0" + provider_uid)`;
- device hash: `HMAC(K, "provider-device\0" + adapter_id + "\0" + provider_device_uid)`;
- parent association: `HMAC(K, "association\0" + adapter_id + "\0" + parent_provider_uid)`;
- sleep child hash: `HMAC(K, "sleep-child\0" + parent_association_hash + "\0" + canonical_session)`.

`canonical_session` is canonical JSON containing start instant, end instant, duration seconds, and the ordered stage tuples `(start, end, enum)`. It never contains a list index. The server validates that uploaded hashes are exactly 64 lowercase hexadecimal characters and relies on batch authentication for origin; it cannot reverse or independently recompute a provider hash because the raw uid is intentionally absent. Its internal observation id adds another domain-separated HMAC over identity namespace id, adapter id, uploaded record hash, and metric. The identity key is stable for the active namespace and rotates independently of the upload credential. It is never silently regenerated.

Installation id is not part of record identity, allowing an authorized reinstall to replay the same provider records idempotently. Installation provenance remains separate.

Identity rekey is transactional: pause ingestion; create a new inactive identity namespace and key; issue a short-lived replacement pairing file; replay and reconcile every enabled provider type into staging rows under the new namespace; verify per-type coverage and complete association manifests; atomically mark the new namespace active; then tombstone and delete old-namespace rows. Failure before activation discards staging and leaves the old namespace active. Old credentials are revoked only after the activation acknowledgement.

Exports include schema version, adapter id/version, source package, attribution, association hash, all three event-path timestamps, and the original normalized payload. Public dictionaries and MCP provenance read persisted adapter values; no adapter is hardcoded.

The migration backup is `state.db.pre-samsung-v3.bak` in the same restricted data directory, inherits the directory ACL, and relies on the documented local disk-encryption threat boundary; Personal State does not claim application-level backup encryption. The installer retains one pre-migration backup until post-migration verification succeeds and does not automatically delete it. Recovery stops the service, verifies the backup hash recorded before migration, replaces the database, and starts the prior binary. The owner controls later backup deletion.

Rollback is operational, not lossy: stop v2 ingestion, stop the upgraded service, and restore that backup before running a v1-only binary. A v1-only binary must never open a database that has accepted v2 rows. The upgraded server can continue in v1-only mode without rollback by disabling the Samsung adapter.

## Adapter ownership and source independence

Health Connect remains the owner of existing portable metrics and direct Wear remains the owner of near-real-time heart rate. The first Samsung SDK release owns only new Samsung-specific or richer metrics. It does not duplicate Health Connect steps, generic heart rate, blood pressure, weight, nutrition, or exercise.

`ActivitySummaryType` is the one provider exception to changed-data synchronization: Samsung exposes active time through a daily aggregate request rather than a change-readable record type. The companion therefore rereads the bounded 30-day active-time window on each run and relies on deterministic identities and idempotent server upserts. Its availability explicitly remains `backfill_limited`; it never claims a change checkpoint for this aggregate.

Source records are never destructively merged. A delete removes only the row with the same adapter and source identity. A surviving equivalent record from another adapter remains.

Presentation grouping is optional and reversible:

- `sleep.session` and `sleep.samsung_session` candidates may share a display group only when start and end are within 60 seconds, the source-device hash matches, and neither record conflicts in stage intervals.
- The Samsung SDK item is preferred for the summary card only when it is at least as recent and has richer documented fields. Both rows remain in history and export.
- Health Connect point oxygen and Samsung continuous oxygen use different metrics and are never grouped.
- Direct Wear heart rate, irregular-rhythm notifications, sleep-apnea detected signs, Energy Score, and every clinical/vendor finding are never heuristically grouped.
- When evidence is insufficient, both records are displayed with their sources. False negatives in grouping are preferable to false matches.

## Normalized metrics and MCP policy

The existing v1 metric shapes remain unchanged. New Samsung-rich records use separate metrics so current agent scope cannot silently expand.

| Metric | Kind | Normalized payload | Default MCP |
|---|---|---|---|
| `vitals.oxygen_saturation` | point | existing single value and percent unit | unchanged, allowed |
| `vitals.oxygen_saturation_series` | series | samples, percent unit, minimum, maximum | denied |
| `sleep.session` | session | existing Health Connect title and stage list | unchanged, allowed |
| `sleep.samsung_session` | session | one Samsung sub-session with stages and parent association | denied |
| `sleep.summary` | session | total duration and ordered child-session association hashes | denied |
| `sleep.score` | point | score 0-100 plus parent association hash | denied |
| `vitals.skin_temperature` | series | Celsius samples plus minimum and maximum | denied |
| `wellness.energy_score` | daily | local date, score 0-100 | denied |
| `cardiac.irregular_rhythm_notification` | point | `detected` or `undefined` | denied |
| `sleep.apnea_detected_sign` | point | `detected`, `not_detected`, or `undefined` | denied |
| `activity.floors` | aggregate | integer floor count | denied |
| `activity.active_time` | aggregate | integer active minutes | denied |

Availability-only metrics:

- `sleep.snoring`: not exposed by the public Samsung Health Data SDK type list; no audio is collected.
- `cardiac.ecg`: no ECG waveform is ingested; Samsung Health Monitor remains authoritative.
- `wellness.stress`: no public Samsung Health Data SDK stress type is documented; Personal State never estimates it.

`PERSONAL_STATE_WATCH_MCP_METRICS` remains the only agent allowlist. New metrics are absent from its default. The dashboard and export may show stored metrics independently of agent authorization. Tests enumerate every new metric and prove it is absent from `health.current_state()`, `health.watch()`, and `health.watch_recent()` until explicitly enabled.

## Samsung property mapping

| Samsung type/property | Personal State mapping |
|---|---|
| `BloodOxygenType.startTime/endTime/zoneOffset` | series interval and offsets |
| blood-oxygen series values | ordered `vitals.oxygen_saturation_series.payload.samples` |
| blood-oxygen minimum/maximum | preserved as payload `minimum` and `maximum` |
| `SleepType.uid` | keyed parent association hash; raw uid is not uploaded |
| `SleepType.startTime/endTime/zoneOffset/duration` | `sleep.summary` interval, offset, `duration_minutes` |
| each `SleepSession` | separate `sleep.samsung_session` child with canonical property-derived record hash and parent association hash |
| each `SleepStage` | ordered stage interval inside its child session |
| `SleepType.SLEEP_SCORE` | separate `sleep.score` at the parent wake time with the parent association hash |
| associated blood oxygen | separate oxygen-series record carrying the parent association hash |
| associated skin temperature | separate skin-temperature series carrying the parent association hash |
| `SkinTemperatureType` interval and values | series interval, Celsius samples, minimum, maximum |
| `EnergyScoreType.startTime/zoneOffset/ENERGY_SCORE` | `wellness.energy_score` daily record with explicit `local_date`, offset, score |
| `IrregularHeartRhythmNotificationType.STATUS` | strict `detected` or `undefined`; no extra reason field |
| `SleepApneaType.DETECTED_SIGN` | strict `detected`, `not_detected`, or `undefined` |
| floors aggregate | aggregate interval, integer `count` |
| activity-summary active time | aggregate interval, integer `min` |

The phone persists an encrypted parent-family manifest after server acknowledgement. On each parent upsert it computes every derived member: summary, sessions, optional score, associated oxygen, and associated skin temperature. The server applies the source change and manifest in one database transaction, tombstoning prior members absent from the replacement set. Parent deletion is an empty replacement manifest and association cascade in that same transaction. Reordering produces the same child hashes. A split, merge, time edit, stage edit, removed score, removed associated series, or removed session produces new hashes and tombstones for superseded members. Initial reconciliation after reinstall sends complete manifests, so an absent local manifest cannot leave server orphans. If upload or acknowledgement is interrupted, the server commits either the whole source change or none of it; replay is idempotent.

Provider ids and device ids are converted to keyed hashes before upload. Association hashes identify related normalized records without exposing Samsung ids.

## Availability

Availability is stored per installation, adapter, and metric. Supported states are:

- `available`: query succeeded and records were returned;
- `permission_required`: read permission was not granted;
- `adapter_not_installed`: this app build does not contain that adapter;
- `provider_partnership_required`: a positively identified Samsung policy or app-registration failure;
- `not_exposed_by_provider`: no supported public provider type exists;
- `platform_feature_unavailable`: device, region, account, or Samsung app does not support the type;
- `no_observation`: query succeeded but no record exists in the stated coverage;
- `source_configuration_unverified`: provider source or attribution configuration is not verified;
- `companion_read_failed`: query failed;
- `unknown`: no valid report exists.

`provider_partnership_required` is never inferred from a missing SDK. A Health Connect status cannot overwrite a Samsung SDK status.

The active installation is the installation hash in the latest successfully authenticated batch for the configured device id. A different installation hash in a later authenticated batch marks the prior installation inactive for current status without deleting its history. Only active-installation reports checked within 26 hours participate in the merged state; older reports remain visible as stale evidence. If no report meets the TTL, merged state is `unknown` with reason `coverage report older than 26 hours`.

For eligible reports, the deterministic precedence from highest to lowest is: `available`, `permission_required`, `provider_partnership_required`, `companion_read_failed`, `source_configuration_unverified`, `no_observation`, `platform_feature_unavailable`, `adapter_not_installed`, `not_exposed_by_provider`, `unknown`. Ties use newest checked time and then adapter id lexical order. The UI displays every contributing adapter and checked time, so the merged state is only a summary. `no_observation` never means normal, negative, or synchronized.

## Incremental synchronization

Health Connect retains its existing checkpoint per Health Connect record type. Samsung checkpoints are keyed by `(adapter_id, Samsung provider DataType)`, such as `SleepType`, `EnergyScoreType`, or `BloodOxygenType`, because one provider change can produce multiple Personal State metrics. Availability remains separately reported per normalized metric. Samsung Health Data SDK uses change-time ranges and temporary per-request pagination tokens; those temporary tokens are never persisted as checkpoints.

### Samsung change-time workflow

1. Persist `committed_through_change_time` only after a complete synchronization run is acknowledged.
2. At run start, capture a fixed `run_upper_bound` from the phone clock. If it is earlier than the committed high-water mark by more than five minutes, report clock rollback and do not advance. Otherwise clamp it to at least the high-water mark.
3. Query changed data from `max(initial_policy_start, committed_high_water - five minutes)` through the fixed upper bound. The five-minute overlap recovers boundary and same-timestamp changes; keyed identities make replay idempotent.
4. Convert each provider change into one `source_change`; all outputs from that source change are applied in one server database transaction. Upload splitting may occur only between source changes, never inside one. If a single source change exceeds the 1 MiB contract limit, report `companion_read_failed`, leave the checkpoint unchanged, and do not send a partial family.
5. Follow Samsung's temporary next-page token until every page in that fixed range is read. Upload signed batches in order. The server commits each source change atomically and acknowledges the batch. Earlier pages may remain committed if a later page fails; replay is safe because source changes are idempotent and the high-water mark remains unchanged.
6. Advance the provider-DataType high-water mark to the fixed upper bound only after every page and every source change through that bound is acknowledged as committed or already committed.
7. If a read, upload, or acknowledgement fails, discard temporary page state, retain the old high-water mark, and repeat the overlapped range on the next run. No health payload queue is retained.
8. Changes sharing the same timestamp are safe because the next run overlaps five minutes and record identity is stable. The checkpoint is the fixed query bound, never the maximum timestamp observed in a page.
9. On first run or checkpoint loss, capture an initial upper bound, read at most the preceding 30 days as Personal State backfill policy, upload complete association manifests, and then process changes from that captured bound through a new bound. Thirty days is policy, not a Samsung permission limit.
10. A source delete removes only its adapter-specific record family. Tombstones reject older replays. Empty association manifests remove every sleep-derived member.
11. A reinstall imports a newly issued pairing file for the stable identity namespace, gets a new installation id, performs the bounded backfill and complete association reconciliation, and replays idempotently. Old installation availability is inactive.
12. One adapter's failure never advances, clears, or rewrites another adapter's checkpoint.

## Freshness and timestamp semantics

Four independent ages are always retained:

- event age: physiological or vendor-event time to response time;
- phone-read lag: provider event to companion read;
- server-ingest lag: companion read to committed server receipt;
- coverage age: availability check to response time.

Fixed presentation matrix:

| Source/metric | Label rule |
|---|---|
| direct Wear heart rate | `live` only when event age is 60 seconds or less; otherwise `last recorded` or no display in the live card |
| Health Connect heart rate | never `live`; `recent record` through 15 minutes, then `last recorded` |
| oxygen point or series | `recent record` through 2 hours, then `latest recorded` |
| activity | `recent record` through 12 hours, then `latest recorded` |
| exercise session | `recent record` through 24 hours, then `latest recorded` |
| sleep session/summary/score | `recent record` through 36 hours, then `latest recorded` |
| Energy Score | `today's Samsung score` only when local date equals the phone's current local date; otherwise `latest recorded` |
| skin temperature, rhythm notification, apnea detected sign | always `latest recorded`; never `live`, `normal`, or `current` |

The UI always shows event time and adapter source. An old record cannot occupy a live vital slot.

## Clinical presentation rules

- `cardiac.irregular_rhythm_notification` is labeled `Samsung irregular-rhythm notification` and displays only `Detected` or `Undefined` as recorded.
- `sleep.apnea_detected_sign` is labeled `Samsung Health Monitor detected sign` and displays `Detected`, `Not detected`, or `Undefined` only when an actual record exists.
- Missing apnea or rhythm data is `No recorded result`, never `Not detected`.
- Skin temperature is labeled `Skin temperature`, never `body temperature` or `fever`.
- Energy Score is labeled `Samsung Energy Score` and described as vendor-derived wellness data.
- No arbitrary vendor reason or interpretation field is accepted.

## Agent behavior contract

When a new metric is explicitly authorized, agents must state vendor and event time, distinguish vendor-derived scores/findings from raw measurements, describe association rather than causation, avoid diagnosis and treatment, mention stale or missing coverage, and never infer a negative finding from absence.

Clinical finding metrics remain excluded from `health.current_state()` even when they are enabled for explicit `health.watch()` or `health.watch_recent()` access. Default `sleep.session` MCP access is restricted to persisted `android_health_connect` records. Samsung sessions use the distinct `sleep.samsung_session` metric and appear only after that metric is explicitly enabled. `health.watch()` never replaces one with the other; if both metrics are authorized it returns them under separate names. `health.watch_recent()` remains metric-specific. Dashboard-only grouping does not alter MCP history. Changing the agent allowlist remains an explicit local user action.

## Privacy and security

- Read-only provider permissions and no health write permissions.
- No Samsung credential, session token, or account password leaves the phone.
- No route, location, microphone, or snore audio.
- After import, the device secret and record-identity key are held only in Android-Keystore-encrypted app state and the applicable server/Cloudflare secret stores. The temporary pairing file is a separate plaintext secret boundary described below.
- Signed requests retain timestamp, nonce, batch id, replay defense, size limits, and strict schema validation.
- Provider record/device ids become keyed hashes.
- Proprietary SDK files are ignored, excluded from Git, and excluded from public release archives by tests.
- Dashboard storage and MCP access are separate controls.

### Pairing file lifecycle

- Generation is an explicit local owner action on the protected dashboard. The server reuses the active identity namespace/key, creates a fresh upload credential, adds a random one-time pairing id, and sets `issued_at` plus a 15-minute `expires_at`.
- The server stores only a keyed hash of the pairing id and its state. It refuses expired, used, or revoked pairing ids. The phone must complete a signed pairing confirmation before the file is marked used.
- The file is plaintext because Android's document picker must import it. The download response uses `Cache-Control: no-store`, the filename contains no email or health value, and documentation treats possession as full companion credential access.
- Import validates the closed schema, endpoint, namespace, timestamps, and one-time id; then encrypts credentials with Android Keystore and confirms pairing. The app immediately presents a `Delete pairing file` action using the document URI. If provider deletion is unavailable, it gives an explicit deletion status and the owner removes the file from Downloads.
- Normal operation never reads the file again. A replacement for reinstall is generated explicitly, expires in 15 minutes, uses the same active identity namespace, and revokes the prior upload credential after successful confirmation.
- Suspected file disclosure revokes the pairing id, upload credential, and Cloudflare client credential. Record identity key rotation is required only if that key may have been disclosed and follows the transactional namespace migration above.
- Pairing artifacts are excluded from Git, backups, logs, crash reports, screenshots, and public installers. Operations documentation includes verification that no pairing JSON remains in Downloads or synchronized cloud storage.

## Failure modes

- SDK absent from build: `adapter_not_installed`.
- Samsung authorization explicitly rejected: `provider_partnership_required` with sanitized error class.
- Samsung app absent/old or feature unavailable: `platform_feature_unavailable`.
- Permission denied: `permission_required` without repeated coercive prompts.
- Query interrupted: preserve checkpoint and report interrupted coverage.
- Region/device limitation: retain provider evidence and do not generalize.
- Provider API change: fail closed for the affected type while other adapters continue.
- Database migration failure: leave the original database untouched and restore the same-volume migration backup under the documented disk-encryption boundary.

## Observability

The companion logs only adapter initialization, sanitized permission state, query count, duration, coverage, grouping count, and upload outcome. Logs contain no measurements, account ids, raw provider ids, tokens, or secrets.

The dashboard shows adapter, last phone read, last server upload, availability per adapter, coverage window, and stored count. It does not claim to know watch-to-phone synchronization timing unless the provider supplies it.

## Deployment

1. Approve this revision through Fable re-review.
2. Implement and test dual v1/v2 server support, migration 3, MCP projection, fake adapter, dashboard, exports, and build-isolation controls.
3. Owner accepts Samsung's SDK agreement and places the SDK in the ignored local dependency path.
4. Compile an internal developer-mode build and verify it on the Pixel and SM-R920.
5. Confirm each requested permission and capture per-type availability evidence.
6. Submit Samsung partner request and register package, signing certificate, and requested data-type scope.
7. Ship a Samsung-enabled public phone APK only when licensing and authorization permit it. The paired watch APK remains in the unified installer.

## Test plan

- Dual v1/v2 acceptance and mixed-version rejection.
- Strict v2 schema tests for adapter identity, kinds, units, enums, ranges, local dates, offsets, and association hashes.
- Migration backup, idempotence, v1 backfill, rollback guard, and persisted provenance tests.
- Source-scoped upsert, deletion, tombstone, replay, and reinstall tests.
- Per-adapter availability and merged-presentation tests.
- Health Connect token tests plus Samsung provider-DataType checkpoint, fixed-bound change-time, overlap, pagination, batch splitting between source changes, atomic source-change commit, post-ack advancement, interruption, clock rollback, same-timestamp change, delete, and checkpoint-loss tests.
- Sleep-family parent deletion, reorder, split, merge, edited-time, edited-stage, removed score, removed oxygen, removed temperature, interrupted reconciliation, manifest replacement, and reinstall tests.
- Record-identity HMAC domain, stable re-pair, upload-secret rotation, explicit identity-rekey, and malformed-hash tests.
- Pairing-file issue, expiry, one-time confirmation, deletion success/failure, regeneration, revocation, disclosure response, and namespace activation/rollback tests.
- Mapping fixtures for multi-session sleep, associated oxygen/temperature, local-day Energy Score, rhythm status, and apnea detected sign.
- No-observation tests proving no availability state creates a value.
- MCP tests proving all new metrics are denied by default and clinical findings never enter current state.
- Manifest tests proving no health write, route, location, or microphone permission.
- Release-content tests proving the Samsung AAR is absent from Git and public ZIPs.
- A private-AAR verification script that runs Samsung-enabled unit tests, release lint, a minified release build, and APK class-retention inspection.
- Device tests for permission denial, authorization failure, empty history, incremental sync, reboot, network loss, and backfill.
- Visual tests at phone and desktop widths, including unavailable states and long labels.
- End-to-end signed upload, CSV export, and protected public dashboard verification.

## Acceptance criteria

- Existing v1 phone and watch clients continue syncing unchanged.
- Every v2 record carries persisted adapter provenance through dashboard, MCP, and export.
- Supported Samsung records preserve native timing and documented semantics.
- Unsupported, absent, denied, empty, and failed states are distinct from real values.
- New Samsung metrics do not appear in any agent response without explicit configuration.
- No metric is called live outside the direct Wear 60-second rule.
- No vendor finding is relabeled as a Personal State diagnosis.
- A delete from one adapter cannot erase another adapter's record.
- Public source and releases contain no proprietary Samsung SDK binary.
- Health Connect and direct heart-rate operation continue when the Samsung adapter is absent.

## References

- Samsung Health Data SDK overview: https://developer.samsung.com/health/data/overview.html
- Samsung Health Data SDK data types: https://developer.samsung.com/health/data/guide/features/data-types.html
- Samsung Health Data SDK data access: https://developer.samsung.com/health/data/guide/features/data-access.html
- Samsung Health Data SDK filter and source semantics: https://developer.samsung.com/health/data/guide/features/filter-and-group.html
- Samsung Health Data SDK app process: https://developer.samsung.com/health/data/process.html
- Samsung Health Data SDK app verification: https://developer.samsung.com/health/data/guide/app-verification.html
- Samsung Health Data SDK release notes: https://developer.samsung.com/health/data/release-note.html
- Samsung Health Data SDK API reference: https://developer.samsung.com/health/data/api-reference/index.html
- Wear OS app packaging: https://developer.android.com/training/wearables/packaging
