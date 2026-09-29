# Google Health / Fitbit Air Adapter Design

Document version: 1.0
Date: 2026-09-29

## Purpose

Add Fitbit Air observations to Personal State so an authorized Casey agent can obtain a deeper, time-bounded picture of the user's physiological state. The adapter enriches the existing MCP tools; it does not create an alarm, diagnosis engine, treatment workflow, or competing source of truth.

## Source and timing model

Fitbit Air data is read through Google Health API v4. Fitbit devices synchronize only through the Fitbit mobile app. Google documents automatic synchronization at roughly 15-minute intervals when the app, Bluetooth, connectivity, and background execution are available. Therefore:

- only direct Wear Health Services heart rate may be labeled `live`;
- Google Health heart rate is `recent_record` or `last_recorded`;
- every observation retains measurement, Google-update when available, Personal State receipt, and storage times;
- absence or delay is unknown availability, never a normal physiological result.

## Read-only scope

The OAuth request contains only:

- `googlehealth.activity_and_fitness.readonly`
- `googlehealth.health_metrics_and_measurements.readonly`
- `googlehealth.sleep.readonly`

Location, nutrition, ECG, irregular-rhythm, profile, settings, and every write scope are excluded. Partial consent is supported: permitted streams continue while denied streams report adapter errors without disabling the rest.

## Architecture

`GoogleHealthAdapter` refreshes a short recent window through `users/me/dataTypes/{type}/dataPoints:reconcile` with `users/me/dataSourceFamilies/google-wearables`. This selects Google/Fitbit wearable hardware and excludes phone estimates and manual entries. A separate 14-day-window backfill command imports older history within Google's query limits.

OAuth client secret and refresh token are stored in the OS keychain. Access tokens remain in memory and are refreshed on demand. Raw API responses are not persisted. Normalized observations enter the existing `health_observations` table with adapter `google_health_fitbit`, schema `personal-state-google-health/v1`, stable deduplication, provenance, and source evidence.

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
- Rate limit or transient Google error: collector retries on its next bounded cycle; no tight retry loop.
- Unknown device metadata: attribution is not upgraded to Fitbit Air.
- Duplicate or overlapping reconciled records: stable hashes and exact observation deduplication prevent duplicate history.

## Operations

1. Enable Google Health API in a Google Cloud project and create a Web OAuth client with the authorized redirect URI required by Google.
2. Add the user as an OAuth test user and configure only the three read scopes above.
3. Run `personal-state google-health-connect <credentials.json>` and visit the returned authorization URL.
4. Complete connection with `--code`, then run `personal-state google-health-status`.
5. Run `personal-state google-health-sync --hours 36` and optionally `personal-state google-health-backfill --days 3650`.
6. Restart the Personal State collector so continuous bounded synchronization begins.

## Validation

Synthetic tests cover read-only OAuth, exact and non-exact device attribution, canonical heart-rate normalization, wearable-only reconciliation, partial failure behavior, collector persistence, MCP exposure, and dashboard metric history. Real-account validation must confirm paired device identity, granted scopes, data type response shapes, and advancing timestamps before deployment is called complete.
