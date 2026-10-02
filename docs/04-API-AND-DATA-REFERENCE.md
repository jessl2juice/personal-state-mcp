# Personal State API and Data Reference

Document version: 1.0  
Last verified: 2026-10-02
Intended readers: integration developers, agent developers, data reviewers, and operators

## General response principles

Personal State interfaces are read-only except for authenticated device ingest and the guarded local refresh command. Health responses use JSON-compatible envelopes containing:

- `generated_at`: response creation time in UTC.
- `data`: tool-specific payload or `null`.
- `freshness`: status, reason, ages, and policy window.
- `provenance`: source identity and collection path when available.
- `safety`: non-diagnostic and non-treatment boundary.
- `errors`: structured, sanitized errors when applicable.

Times are serialized as ISO 8601. Storage is UTC. Source offsets are retained where the normalized contract provides them.

## MCP tools

### `health.glucose()`

Returns the newest stored glucose reading with:

- value in mg/dL.
- trend code and trend rate when supplied upstream.
- measurement, receipt, and storage timestamps.
- measurement and receipt age.
- freshness classification.
- Libre-compatible provenance.
- safety boundary.

It performs no treatment action and does not force an upstream refresh by default.

### `health.glucose_recent(hours=3, limit=96)`

Returns recent locally persisted glucose readings, range metadata, detected gaps, latest freshness, provenance, and safety boundary. The service validates range and limit policy.

### `health.context()`

Returns concise decision-support context for an agent that notices unusual confusion, inconsistency, or indecision. It reports the configured 80 mg/dL threshold and one of:

- `below`
- `near`
- `above`
- `unknown_stale`
- `unavailable`

Only fresh or recent glucose can produce below, near, or above. The tool does not diagnose why the user seems off.

### `health.current_state()`

Returns a compact conversational summary. It includes current glucose context and an allowlisted wearable summary when available. The exact wearable fields are constrained by user configuration and do not expose every collected category.

### `health.watch()`

Returns an agent-authorized Galaxy, Samsung Health, Fitbit, and Google Health wearable summary with:

- latest observations by authorized category.
- observation recency.
- attribution state.
- last companion upload metadata.
- permission and availability state.
- source-sync limitations.
- Casey biofeedback context for direct Fitbit heart rate when available, including source id, age, and whether the estimate is usable, trend-only, conflicted, or stale.

### `health.watch_recent(metric, hours=24, limit=200, cursor=None, source=None)`

Returns paged history for one explicitly authorized metric. Policy:

- `hours`: 0.25 through 720.
- `limit`: 1 through 200.
- `metric`: must be in the agent exposure allowlist.
- `cursor`: server-issued, HMAC-protected, policy-bound, and short-lived.
- `source`: optional source filter such as `fitbit_ble_heart_rate`, `google_health_sync`, `health_connect_history`, `galaxy_direct_live`, `samsung_health_history`, or aggregate aliases `fitbit` and `galaxy`.

Invalid or unauthorized requests fail closed with structured errors.

## Default agent watch metrics

- `activity.steps`
- `activity.exercise_session`
- `vitals.heart_rate`
- `vitals.oxygen_saturation`
- `sleep.session`
- `vitals.resting_heart_rate`
- `vitals.heart_rate_variability`
- `vitals.respiratory_rate`
- `vitals.skin_temperature`
- `activity.active_zone_minutes`
- `activity.total_calories`

Collected categories outside the exposure allowlist remain unavailable to agents until the user explicitly changes policy outside the agent interface.

## Dashboard HTTP routes

### Interactive and read routes

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/` | Dashboard application |
| GET | `/api/live` | Compact current glucose and heart-rate state for one-second polling |
| GET | `/api/dashboard?range=24h` | Full selected-range dashboard payload |
| GET | `/api/export.csv?range=24h` | Selected-range glucose export |
| GET | `/api/watch/export.csv?range=24h` | Selected-range watch export |
| GET | `/api/health` | Local dashboard health check |
| GET | `/api/watch/health` | Ingest-host health check only |

Supported dashboard range keys correspond to Day, Week, Month, Year, and All history. Invalid values fall back to Day.

### Local command route

| Method | Route | Purpose |
| --- | --- | --- |
| POST | `/api/refresh` | Guarded manual collection refresh |

The refresh route requires the per-process `X-Personal-State-Token` embedded into the locally served page. It also respects the minimum collection interval.

### Device ingest route

| Method | Route | Purpose |
| --- | --- | --- |
| POST | `/api/watch/ingest` | Authenticated normalized Android companion batch |

The ingest route is accepted only on the configured ingest hostname. It requires JSON, rejects content encoding, enforces a 1 MiB body limit, and validates a closed schema.

Required device headers:

```text
X-PSM-Device-Id
X-PSM-Timestamp
X-PSM-Nonce
X-PSM-Batch-Id
X-PSM-Signature
```

Cloudflare Access service-token headers are also required at the edge. Secret values must never appear in examples or logs.

## Normalized glucose record

Core fields include:

- stable record id.
- glucose value in mg/dL.
- trend and trend rate when available.
- `measured_at`.
- `received_at`.
- `stored_at`.
- source and adapter identity.
- hashed upstream account identity.
- non-secret redacted source metadata.

## Normalized health observation

Health observations use one of these record kinds:

- `point`
- `interval`
- `series`
- `session`
- `composite`
- `aggregate`
- `daily` (v2 only, with required local date and zone offset)

Common fields include:

- stable keyed observation id.
- canonical metric name.
- record kind.
- point time or start/end times.
- `observed_by_companion_at`.
- `ingested_at_server`.
- optional upstream last-modified time.
- source package and recording method.
- attribution state.
- canonical typed payload.

The original Health Connect and direct-Wear contract remains [watch-ingest-schema-v1.json](watch-ingest-schema-v1.json). The adapter-aware Samsung contract is [watch-ingest-schema-v2.json](watch-ingest-schema-v2.json). Both are accepted by the same authenticated endpoint; unknown versions fail closed.

### Adapter-aware v2 records

Schema v2 persists adapter id/version, identity namespace, keyed source-record hash, optional parent-association hash, event time, companion-read time, server-ingest time, and original normalized payload. Mixed-adapter batches are rejected. A delete is scoped to one adapter and identity namespace, so it cannot erase an equivalent record owned by another adapter.

Samsung sleep updates are one atomic source change. The change includes a complete association manifest for the summary, sessions, optional score, and associated oxygen or temperature records. Replacing the manifest tombstones members that are no longer present; a parent deletion supplies an empty manifest and removes the family atomically.

New Samsung-specific observation metrics are:

- `vitals.oxygen_saturation_series`
- `sleep.samsung_session`
- `sleep.summary`
- `sleep.score`
- `vitals.skin_temperature`
- `wellness.energy_score`
- `cardiac.irregular_rhythm_notification`
- `sleep.apnea_detected_sign`
- `activity.floors`
- `activity.active_time`

All are excluded from the default MCP allowlist. Rhythm and apnea findings remain excluded from `health.current_state()` even if explicit history access is later enabled.

## Attribution states

- `watch_confirmed`: positive device metadata identifies the paired watch.
- `samsung_health_unattributed`: Samsung Health supplied the record but watch origin is not proven.
- `phone`: source evidence identifies the phone.
- `manual`: recording method indicates manual entry.
- `external_device`: source evidence identifies another device.
- `unknown`: origin cannot be established.

Do not translate Samsung Health origin alone into `watch_confirmed`.

## Availability states

- `available`
- `permission_required`
- `platform_feature_unavailable`
- `not_exported_by_samsung_mapping`
- `no_observation`
- `source_configuration_unverified`
- `companion_read_failed`
- `unknown`

Schema v2 also supports `adapter_not_installed`, `provider_partnership_required`, and `not_exposed_by_provider`. Availability is stored by active installation, adapter, and metric. Reports older than 26 hours are shown as stale evidence and merge to `unknown`; `no_observation` never means a normal or negative result.

Permission granted with no record means `no_observation`. It does not prove watch or Samsung synchronization failure.

## Units

Canonical units include:

- glucose: mg/dL.
- heart rate: beats/minute.
- oxygen saturation: percent.
- steps: count.
- distance: meters.
- calories: kilocalories.
- power: watts.
- speed: meters/second.
- VO2 max: milliliters/minute/kilogram.
- blood pressure: millimeters of mercury.
- temperature: degrees Celsius.
- weight: kilograms.
- height: centimeters.
- duration: seconds or milliseconds as defined by the metric payload.

## Freshness rules

### Glucose

Default service policy:

- fresh through 600 seconds.
- recent after 600 and through 1,800 seconds.
- stale after 1,800 seconds.
- future timestamps beyond the configured skew tolerance are stale.

### Live dashboard heart rate

- numeric live value through ten seconds.
- no numeric live value after ten seconds.
- older values may remain in history with explicit historical labeling.

### Other watch observations

Recency is metric-specific. Completed events such as sleep and exercise use neutral states such as recent or latest recorded rather than pretending to be a current vital.

### Fitbit and Google Health observations

- They retain provider measurement time, Personal State receipt time, age, source device evidence, and synchronization limitations.
- They are classified as recent or latest recorded, never as a direct-live stream.
- Exact Fitbit Air attribution requires both Fitbit platform evidence and a matching Air device name; otherwise attribution remains external or unknown.

## Threshold rules

The current user-specific threshold is 80 mg/dL with a default near margin of 10 mg/dL. It supports conversational context only.

- Below threshold: fresh or recent value less than 80.
- Near threshold: fresh or recent value within the configured margin around 80.
- Above threshold: fresh or recent value above the near band.
- Unknown stale: latest value exists but freshness does not permit comparison.
- Unavailable: no value exists.

Agents cannot change this threshold and must not treat it as a treatment instruction.

## Error and privacy behavior

- Public errors are sanitized and do not include secrets or health payloads.
- Dashboard and export responses use `Cache-Control: no-store`.
- Unknown fields, duplicate JSON keys, unsupported metrics, invalid units, non-finite numbers, excessive depth, and prohibited route or location data are rejected.
- Batch writes are transactional. A rejected batch is not partially committed.
- Repeated valid batches are idempotent.
