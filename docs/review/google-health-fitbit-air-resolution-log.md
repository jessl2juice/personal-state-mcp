# Google Health / Fitbit Air Review Resolution Log

Date: 2026-09-29
Updated: 2026-10-02

| Finding | Resolution |
| --- | --- |
| Adapter-specific live status | `observation_recency` labels only `wear_health_services` heart rate within ten seconds as live. Google Health observations remain recent or last recorded. |
| Fail-closed Fitbit Air attribution | Exact attribution requires `platform=FITBIT` plus device display name `Fitbit Air` or `Air`; otherwise the state is `external_device`. |
| Least-privilege MCP access | Fitbit observations reuse the existing metric allowlist. OAuth excludes write, location, nutrition, ECG, IRN, profile, and settings scopes. |
| Honest provider cadence | Design and MCP limitations state that Google Health and Health Connect Fitbit records appear only after app/provider synchronization and are not direct live data. |
| Bounded history | Backfill uses sequential 14-day windows and existing idempotent observation storage. Raw provider responses are not persisted. |
| Partial consent and secrets | Data-type failures are isolated. OAuth client secret and refresh token use the OS keychain and are included in credential deletion. |

Status: resolved in implementation; synthetic review gate approved.

2026-10-02 field addendum:

- OAuth consent, Fitbit Air source labeling, Health Connect Fitbit upload, and Google Health synchronization were completed.
- The original cadence finding remains true for Google Health and Health Connect. It no longer describes the direct Fitbit Bluetooth lane.
- Direct Fitbit Air heart rate is now a separate `fitbit_ble_heart_rate` source. It may be labeled live only while its newest measured sample is no more than ten seconds old.
- A real Fitbit/Galaxy disagreement was checked against manual pulse and supported Fitbit for that session. Casey must therefore use source confidence and conflict handling, not a hardcoded Galaxy preference.
- Remaining release validation is longer field behavior: reconnect after range loss, battery impact, outdoor/gym behavior, and no-Galaxy fallback operation.
