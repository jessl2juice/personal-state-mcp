# Google Health / Fitbit Air Adapter Design

Document version: 1.4
Date: 2026-10-02

## Purpose

Add Fitbit Air observations to Personal State so an authorized Casey agent can obtain a deeper, time-bounded picture of the user's physiological state. The adapter enriches the existing MCP tools; it does not create an alarm, diagnosis engine, treatment workflow, or competing source of truth.

## Source and timing model

Fitbit Air data currently enters Personal State through two synchronized paths and one experimental realtime path:

- Google Health API v4, with adapter id `google_health_fitbit`.
- Android Health Connect phone ingest when the Fitbit phone app writes Health Connect records, with adapter id `android_health_connect_fitbit`.
- Direct Bluetooth LE Heart Rate Service subscription, with adapter id `fitbit_ble_heart_rate`.

Google Health and Health Connect records depend on the Fitbit mobile app or cloud/provider synchronization. Google documents automatic synchronization at roughly 15-minute intervals when the app, Bluetooth, connectivity, and background execution are available. That cadence is acceptable for history and context, but it is a failure for Casey realtime biofeedback. Therefore:

- only direct Wear Health Services or Fitbit Bluetooth heart rate may be labeled `live`;
- Fitbit Bluetooth heart rate is live only while the newest measured sample is no more than ten seconds old;
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

The direct Fitbit Bluetooth path subscribes to the standard Bluetooth LE Heart Rate Service (`0x180D`) and Heart Rate Measurement characteristic (`0x2A37`). The primary runtime is the Android phone companion because Casey carries the phone during runs and the phone is physically close to the Fitbit. The Python BLE probe remains a development diagnostic path on the Windows host. Both paths store heart rate only and avoid storing raw Bluetooth identity. They do not attempt to collect steps, sleep, HRV, oxygen, or recovery data. Those remain synchronized context lanes.

Fitbit Air must be placed into its live heart-rate sharing mode before this standard BLE service is expected to appear. Google's support flow is: open Google Health on the phone, open Connections, select the Fitbit device, choose Share heart rate, tap Get started, then pair/connect from the receiving app or equipment. In Personal State, the receiving app is the Android companion's `Start Fitbit live heart rate` button. A bonded Fitbit connection without the Heart Rate Measurement characteristic means the Air is reachable but is not yet broadcasting the standard live-heart-rate profile to this app. Source: https://support.google.com/googlehealth/answer/14236705?hl=en

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
- Fitbit BLE not discovered, disconnected, or failing upload: first enable Google Health > Connections > Fitbit > Share heart rate, then start the companion's Fitbit live heart-rate receiver. If the phone shows samples but the dashboard is stale, check ingest host configuration and schema errors before changing device setup. If the receiver stops after a range loss or Android service interruption, the foreground service must rescan/reconnect rather than leaving the dashboard to age out silently. Casey realtime biofeedback is unavailable unless a fresh direct Fitbit BLE sample or another explicitly named live source is shown. Slow Fitbit history must not be promoted to live.
- Phone companion upload fails with HTTP 502: inspect the dashboard server traceback before retrying. A live production miss was caused by an additive SQLite table absent from an already-migrated database.
- Direct watch live-heart polling can drain the Galaxy Watch. Set `WatchLivePollingEnabled = $false` to stop server-side live watch demand while preserving Fitbit/Health Connect sync.
- Rate limit or transient Google error: collector retries on its next bounded cycle; no tight retry loop.
- Unknown device metadata: attribution is not upgraded to Fitbit Air.
- Duplicate or overlapping reconciled records: stable hashes and exact observation deduplication prevent duplicate history.

## Direct Fitbit live and cloud evaluation

The Casey realtime path is `fitbit_ble_heart_rate`. It may disrupt normal Fitbit app sync, but that is an accepted tradeoff during a Casey biofeedback session if it provides live heart rate. It must remain heart-rate-only and source-labeled.

The richer Fitbit-only context path remains a separate cloud/API adapter, tentatively `fitbit_direct_cloud`, that reads Fitbit-owned endpoints directly when available. Legacy Fitbit Web API intraday endpoints may expose heart-rate detail, but Fitbit/Google's public direction is toward Google Health API replacement surfaces. Any direct cloud adapter must therefore be treated as an evaluation lane until lifecycle, scopes, cadence, and response semantics are verified.

Minimum design requirements:

- Store Fitbit-direct OAuth material separately from Google Health material.
- Preserve `fitbit_ble_heart_rate`, `fitbit_direct_cloud`, `android_health_connect_fitbit`, and `google_health_fitbit` as separate source lanes.
- Never silently fall back to Galaxy Watch for Casey biofeedback.
- Use a ten-second live gate for `fitbit_ble_heart_rate`; slower Fitbit lanes are usable for context only unless explicitly marked current by their own measured-time age.
- Verify on a real run with only the phone and Fitbit path carrying Casey's source requirement.

## Operations

1. Enable Google Health API in a Google Cloud project and create a Web OAuth client with the authorized redirect URI required by Google.
2. Add the user as an OAuth test user and configure only the three read scopes above.
3. Run `personal-state google-health-connect <credentials.json>` and visit the returned authorization URL.
4. Complete connection with `--code`, then run `personal-state google-health-status`.
5. Run `personal-state google-health-sync --hours 36` and optionally `personal-state google-health-backfill --days 3650`.
6. Restart the Personal State collector so continuous bounded synchronization begins.
7. For realtime testing on the phone, open Google Health > Connections > Fitbit > Share heart rate > Get started, then open the Personal State phone companion and tap `Start Fitbit live heart rate`.
8. For development diagnostics from the Windows host, install the optional BLE runtime and run `personal-state fitbit-ble-probe --name Fitbit --seconds 300 --print-samples`.

## Validation

Synthetic tests cover read-only OAuth, exact and non-exact device attribution, canonical heart-rate normalization, Bluetooth heart-rate packet parsing, wearable-only reconciliation, Health Connect Fitbit attribution, partial failure behavior, collector persistence, MCP exposure, dashboard source panels, source-filtered metric history, and watch-polling disablement. Live validation on 2026-10-02 confirmed the phone can receive Fitbit Air standard BLE heart-rate samples after Share heart rate is enabled, upload them through the authenticated ingest host, and display them as `fitbit_ble_heart_rate` live data. Remaining field validation must confirm range, reconnect behavior, battery impact, and run behavior before treating the lane as operationally proven for Casey.

2026-10-01 live validation status:

- Phone companion pairing and Health Connect grants were intact.
- Manual phone sync completed successfully after the server-side schema fix.
- Fitbit source appeared as synced with stored metrics.
- Fitbit biofeedback remained stale because the newest Fitbit heart-rate sample available to Personal State was outside the Casey currentness window.
- Watch live polling was disabled server-side so the Galaxy Watch can charge and so Fitbit-only behavior can be evaluated without a watch fallback.

2026-10-02 implementation update:

- Added optional `fitbit_ble_heart_rate` adapter lane for direct Bluetooth LE Heart Rate Service samples.
- Added CLI command `personal-state fitbit-ble-probe` and `scripts/start_fitbit_ble.ps1`.
- Updated dashboard, MCP source grouping, and Casey biofeedback currentness so Fitbit BLE can be live while Google Health and Health Connect remain synchronized history.
- Installed and verified the optional BLE runtime in the development virtual environment. A local Windows scan for standard BLE Heart Rate Service advertisers completed successfully but discovered no devices, so the phone became the primary BLE probe.
- Added Android phone companion BLE scanning/subscription and signed upload for `bluetooth.le.heart_rate_service` observations. The debug phone APK builds and lint passes.
- Installed the phone companion in place with the same debug signing identity as the already paired app, preserving pairing and Health Connect grants.
- Repaired the local receiver path: the local dashboard/ingest service was down and the Cloudflare tunnel returned HTTP 530 until the receiver and tunnel were restarted. After repair, the phone uploaded 3,376 Health Connect changes successfully.
- Direct BLE field result: the phone scan saw hundreds of nearby BLE advertisements but zero Fitbit/heart-rate candidates because the Fitbit did not advertise a public name or Heart Rate Service UUID. The companion then connected directly to the bonded `Google Fitbit Air` LE device. GATT connection and service discovery succeeded, but the standard Heart Rate Measurement characteristic (`0x2A37`) under Heart Rate Service (`0x180D`) was not exposed to this third-party app. No `fitbit_ble_heart_rate` sample was captured.
- Follow-up field result: after Google Health `Share heart rate` / `Always visible` was enabled, the phone companion subscribed to Heart Rate Measurement (`0x2A37`) and received about one sample per second from the bonded Fitbit Air. Upload initially failed because the dashboard process had not loaded `WatchIngestHosts`, then because the BLE payload included fields outside the v1 series contract. Restarting the dashboard through `scripts/start_dashboard.ps1` and sending contract-clean samples resolved the path.
- Current conclusion: public standard BLE HR is verified on Casey's phone for live heart rate. The dashboard showed Fitbit Air `Live`, the live heart card showed Fitbit direct Bluetooth with a 0-second age, and Casey biofeedback reported `fitbit_ble_heart_rate_live`. The user then confirmed they had been about 30 feet away for roughly 10 minutes during the successful live window, which is strong house-range evidence. Afterward, the dashboard aged to 15+ minutes stale because the phone's Fitbit BLE foreground service had stopped and lacked an automatic reconnect loop. The companion now treats disconnect as a rescan/reconnect condition. Remaining validation work is longer field behavior: reconnect after actual range loss, battery impact, and outdoor run stability without Galaxy fallback.
