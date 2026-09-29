# Google Health / Fitbit Air Review Resolution Log

Date: 2026-09-29

| Finding | Resolution |
| --- | --- |
| Adapter-specific live status | `observation_recency` labels only `wear_health_services` heart rate within ten seconds as live. Google Health observations remain recent or last recorded. |
| Fail-closed Fitbit Air attribution | Exact attribution requires `platform=FITBIT` plus device display name `Fitbit Air` or `Air`; otherwise the state is `external_device`. |
| Least-privilege MCP access | Fitbit observations reuse the existing metric allowlist. OAuth excludes write, location, nutrition, ECG, IRN, profile, and settings scopes. |
| Honest provider cadence | Design and MCP limitations state that Fitbit data appears only after Fitbit-app synchronization and is not second-by-second. |
| Bounded history | Backfill uses sequential 14-day windows and existing idempotent observation storage. Raw provider responses are not persisted. |
| Partial consent and secrets | Data-type failures are isolated. OAuth client secret and refresh token use the OS keychain and are included in credential deletion. |

Status: resolved in implementation; synthetic review gate approved. Real-account validation remains open until OAuth consent and a Fitbit Air sync are completed.
