# Google Health / Fitbit Air Adapter Design

Document version: 1.1
Date: 2026-10-01

## Purpose

Add Fitbit Air observations to Personal State so an authorized Casey agent can obtain a deeper, time-bounded picture of the user's physiological state. The adapter enriches the existing MCP tools; it does not create an alarm, diagnosis engine, treatment workflow, or competing source of truth.

## Source and timing model

Fitbit Air data currently enters Personal State through two synchronized paths:

- Google Health API v4, with adapter id `google_health_fitbit`.
- Android Health Connect phone ingest when the Fitbit phone app writes Health Connect records, with adapter id `android_health_connect_fitbit`.

Fitbit devices synchronize only through the Fitbit mobile app or cloud/provider APIs. Google documents automatic synchronization at roughly 15-minute intervals when the app, Bluetooth, connectivity, and background execution are available. Health Connect records are likewise only as current as the Fitbit phone app's last write. Therefore:

- only direct Wear Health Services heart rate may be labeled `live`;
- Google Health heart rate is `recent_record` or `last_recorded`;
- Fitbit Health Connect heart rate is synchronized phone history, not direct live data;
- every observation retains measurement, Google-update when available, Personal State receipt, and storage times;
- absence or delay is unknown availability, never a normal physiological result.

## Read-only scope

The OAuth request contains only:

- `googlehealth.activity_and_fitness.readonly`
- `googlehealth.health_metrics_and_measurements.readonly`
- `googlehealth.sleep.readonly`

Location, nutrition, ECG, irregular-rhythm, profile, settings, and every write scope are excluded. Partial consent is supported: permitted streams continue while denied streams report adapter errors without disabling the rest.

## Architecture

`GoogleHealthAdapter` refreshes a short recent window through `users/me/dataTypes/{type}/dataPoints:reconcile` with `users/me/dataSourceFamilies/google-wearables`. This selects Google/Fitbit wearable hardware and excludes phone estimates and manual entries. The default continuous collection window is 36 hours and is recorded as `bounded_recent_samples_for_timeline_context`.

This is an explicit minimization decision: recent samples are permitted because the product needs source-labeled timeline context beside Libre and direct Galaxy heart rate. Raw Google API responses are not persisted, access tokens remain in memory, and Fitbit/Google Health observations are always synchronized records, never live data. A separate 14-day-window backfill command can import older history within Google's query limits when the operator intentionally runs it; imported records remain historical/synchronized context and cannot occupy a live/current card.

OAuth client secret and refresh token are stored in the OS keychain. Access tokens remain in memory and are refreshed on demand. Raw API responses are not persisted. Normalized observations enter the existing `health_observations` table with adapter `google_health_fitbit`, schema `personal-state-google-health/v1`, stable deduplication, provenance, and source evidence.

Health Connect phone ingest accepts Fitbit-origin records only when source-package evidence identifies the Fitbit mobile application. Those records use adapter `android_health_connect_fitbit` and public source label `Fitbit via Health Connect`. They are displayed in the Fitbit panel but remain distinct from Google Health records for provenance, filtering, currentness, and troubleshooting.

## Initial metrics

- heart rate and resting heart rate
- heart-rate variability
- oxygen saturation
- respiratory rate
- skin-temperature derivations
- steps, Active Zone Minutes, distance, and total calories
- VO2 max
- exercise sessions
- sleep sessions and stages

The exact Fitbit Air label is asserted only when Google supplies `platform=FITBIT` and device display name `Fitbit Air` or `Air`. Otherwise the record remains a Google wearable with `external_device` attribution.

## Casey behavior contract

`health.current_state()` and `health.context()` include only metrics allowed by `PERSONAL_STATE_WATCH_MCP_METRICS`. Casey may use them to understand conversational context, such as whether sleep, activity, heart rate, HRV, or other recent physiology could warrant slowing down, clarifying, or suggesting that the user check an official app.

Casey must:

- state source, measurement time, and recency when relying on a value;
- treat missing or stale data as unknown;
- avoid diagnosing why a user seems different;
- avoid treatment, dosing, exercise, driving, nutrition, or emergency instructions;
- defer device-specific notices to Fitbit, Samsung, Libre, and established care guidance.

## Failure modes

- OAuth consent missing or revoked: adapter reports unavailable and retains stored history.
- Partial scope consent: only affected data types fail.
- Fitbit app has not synchronized: last recorded observations remain visible with their true age.
- Fitbit app has written Health Connect records but no current heart-rate record: Fitbit source is synced, while Casey biofeedback remains stale until a current Fitbit heart sample appears.
- Phone companion upload fails with HTTP 502: inspect the dashboard server traceback before retrying. A live production miss was caused by an additive SQLite table absent from an already-migrated database.
- Direct watch live-heart polling can drain the Galaxy Watch. Set `WatchLivePollingEnabled = $false` to stop server-side live watch demand while preserving Fitbit/Health Connect sync.
- Rate limit or transient Google error: collector retries on its next bounded cycle; no tight retry loop.
- Unknown device metadata: attribution is not upgraded to Fitbit Air.
- Duplicate or overlapping reconciled records: stable hashes and exact observation deduplication prevent duplicate history.

## Direct Fitbit cloud evaluation

The next Fitbit-only design path is not Bluetooth/device-direct polling. It is a separate cloud/API adapter, tentatively `fitbit_direct_cloud`, that reads Fitbit-owned endpoints directly when available. Legacy Fitbit Web API intraday endpoints may expose heart-rate detail, but Fitbit/Google's public direction is toward Google Health API replacement surfaces. Any direct adapter must therefore be treated as an evaluation lane until lifecycle, scopes, cadence, and response semantics are verified.

Minimum design requirements:

- Store Fitbit-direct OAuth material separately from Google Health material.
- Preserve `fitbit_direct_cloud`, `fitbit_health_connect_phone`, and `google_health_fitbit` as separate source lanes.
- Never silently fall back to Galaxy Watch for Casey biofeedback.
- Use the same currentness gate as other Fitbit lanes: source-labeled Fitbit heart rate is usable for Casey only when the measured sample age is inside the configured window.
- Verify on a real run with only the phone and Fitbit path carrying Casey's source requirement.

## Operations

1. Enable Google Health API in a Google Cloud project and create a Web OAuth client with the authorized redirect URI required by Google.
2. Add the user as an OAuth test user and configure only the three read scopes above.
3. Run `personal-state google-health-connect <credentials.json>` and visit the returned authorization URL.
4. Complete connection with `--code`, then run `personal-state google-health-status`.
5. Run `personal-state google-health-sync --hours 36` and optionally `personal-state google-health-backfill --days 3650`.
6. Restart the Personal State collector so continuous bounded synchronization begins.

## Validation

Synthetic tests cover read-only OAuth, exact and non-exact device attribution, canonical heart-rate normalization, wearable-only reconciliation, Health Connect Fitbit attribution, partial failure behavior, collector persistence, MCP exposure, dashboard source panels, source-filtered metric history, and watch-polling disablement. Real-account validation must confirm paired device identity, granted scopes, data type response shapes, Health Connect Fitbit package evidence, and advancing timestamps before Casey biofeedback is called complete.

2026-10-01 live validation status:

- Phone companion pairing and Health Connect grants were intact.
- Manual phone sync completed successfully after the server-side schema fix.
- Fitbit source appeared as synced with stored metrics.
- Fitbit biofeedback remained stale because the newest Fitbit heart-rate sample available to Personal State was outside the Casey currentness window.
- Watch live polling was disabled server-side so the Galaxy Watch can charge and so Fitbit-only behavior can be evaluated without a watch fallback.
