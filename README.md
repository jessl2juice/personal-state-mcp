# Personal State MCP

Local read-only MCP server for selective physiological context when an agent notices the user seems unusually confused, inconsistent, indecisive, or "off."

This is not an alarm system, diagnosis system, treatment recommender, or medical device. The official Libre app/sensor remains the safety alert layer.

## Documentation

Start with the [Personal State Documentation Set](docs/00-DOCUMENTATION-INDEX.md). It links the user and clinician guide, installation and operations, architecture and security, API reference, troubleshooting runbook, and release governance.

## Current Scope

- First adapter: FreeStyle Libre 3 Plus through a LibreLinkUp/LibreView-compatible follower path.
- Second adapter: Samsung Health on a Galaxy phone through a read-only Android Health Connect companion, with Galaxy Watch5 Pro attribution when source metadata proves it.
- Stable MCP tools:
  - `health.current_state()`
  - `health.glucose()`
  - `health.glucose_recent(hours=3, limit=96)`
  - `health.context()`
  - `health.watch()`
  - `health.watch_recent(metric, hours=24, limit=100, cursor=None)`
- Local SQLite history so agents are not limited to the upstream short history window.
- Provenance, measurement timestamp, received timestamp, stored timestamp, age, and freshness in every response.
- User-specific decision-support context threshold: `80 mg/dL`.
- Read-only tools only. Agents cannot change thresholds, credentials, polling, or safety instructions.
- Private dashboard with separate glucose and watch views, source-labeled history, category coverage, freshness, provenance, and CSV export.

## Research Notes

See [docs/design.md](docs/design.md) for the detailed design and source links. In short:

- Libre 3 Plus can share through the Libre app, LibreView, and LibreLinkUp.
- The current practical programmatic path is unofficial LibreLinkUp/LibreView-compatible access and can break without notice.
- `lozit/mcp-freestyle` is a current, useful reference, but this project does not depend on it because this project needs Python, long-term local persistence, and a different MCP contract.
- Samsung Health can export supported categories to Health Connect. The connector filters reads to Samsung Health-origin records and never requests write, route, or location permissions.
- Samsung's stress score, resting heart rate, HRV, skin temperature, floors, and activity-tracker active-time summary are not in Samsung's documented Health Connect export mapping. The Health Connect adapter labels those categories by source and never infers stress from heart rate, sleep, or other signals.
- The server now has a closed v2 contract for richer Samsung Health Data SDK records: continuous blood oxygen, sleep sessions and score, skin temperature, Energy Score, irregular-rhythm notifications, sleep-apnea detected signs, floors, and active time. These metrics are denied to MCP agents by default.
- The Samsung SDK phone adapter is gated on obtaining the proprietary SDK and Samsung authorization. The SDK binary is not committed or redistributed by this public repository.
- Future Fitbit work should target Google Health API rather than new reliance on the legacy Fitbit Web API.

## Install

For a normal Windows installation:

1. Download and extract `Personal-State-0.2.0-Windows.zip`.
2. Double-click `Install Personal State.cmd`.
3. Enter the dedicated LibreLinkUp follower email and password when prompted.
4. Open the new **Personal State** desktop shortcut.
5. Double-click `Install Phone and Watch.cmd` when adding the Android companions. That guided setup installs both apps in one run.

The Windows release uses 64-bit Python 3.12. Setup offers to install it through Windows Package Manager when it is missing. The release is otherwise self-contained and uses no administrator privileges. Local history is retained during upgrades and normal uninstall. See [Installer Quick Start](docs/INSTALLER-QUICK-START.md) for the complete user path.

For development from source:

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e ".[mcp,keychain]"
```

If `python` is not on PATH in this Codex workspace, use the bundled Python shown in Codex workspace dependencies.

## Configure LibreLinkUp

Use a LibreLinkUp follower account, not the primary Libre account paired to the sensor.

```powershell
$env:LIBRELINKUP_EMAIL = "follower@example.com"
personal-state set-libre-password follower@example.com
```

Optional environment:

```powershell
$env:PERSONAL_STATE_MCP_DB = "$env:LOCALAPPDATA\PersonalStateMCP\state.db"
$env:PERSONAL_STATE_MCP_HOST_ID = "codex-local"
$env:PERSONAL_STATE_MCP_ALLOWED_HOSTS = "codex-local"
$env:PERSONAL_STATE_MCP_SOURCE_TIMEZONE = "America/Los_Angeles"
```

Inside a restricted Codex workspace, point `PERSONAL_STATE_MCP_DB` at a writable workspace path.

For CI or temporary local testing only, `LIBRELINKUP_PASSWORD` can override the keychain.

## Collect

```powershell
personal-state collect-once
personal-state status
```

For long-term history, run a local background loop:

```powershell
personal-state collect-loop
```

MCP tool calls do not refresh upstream data by default. Set `PERSONAL_STATE_MCP_OPPORTUNISTIC_REFRESH=true` only if you want read calls to trigger conservative refresh attempts.

## Dashboard

Run the local-only dashboard:

```powershell
.\.venv\Scripts\python.exe -m personal_state_mcp.dashboard --host 127.0.0.1 --port 8766
```

Open [http://127.0.0.1:8766](http://127.0.0.1:8766).

The dashboard includes:

- Current glucose and heart rate with units, measurement time, receipt or upload time, data age, and freshness. A numeric heart-rate value appears in the live card only while its measurement is no more than 60 seconds old; older values are withheld from the live position and labeled as historical context.
- Day, week, month, and year views with Day as the default and separate glucose and heart-rate lanes on one recorded-time axis.
- Exact per-stream coverage and an explicit sync-failure state when a recent companion upload does not contain current heart-rate samples.
- Descriptive glucose and heart-rate facts, context bands, data gaps, and a clinician-ready print view.
- Latest-reading provenance, collector run history, and selected-range CSV export.
- A guarded manual refresh that respects the minimum polling interval.
- An Ask Codex composer that prepares a selective MCP request without embedding an API key or sending data directly from the page.
- A Watch view for Health Connect read/upload times, heart-rate samples, daily steps, sleep sessions, Samsung category coverage, source attribution, recent records, and watch-specific CSV export.
- Phone companion version 0.2.0 reads heart rate from every Health Connect origin, migrates obsolete origin-filtered cursors automatically, preserves each record's actual source and device attribution, and receives direct watch samples over both Wear OS message and urgent Data Item transports.

Dashboard user stories and acceptance criteria for the primary user, primary care, endocrinology, and cardiology are recorded in [`docs/dashboard-user-stories.md`](docs/dashboard-user-stories.md).

The server refuses non-loopback binds and non-local Host headers. State-changing requests require a per-process request token. The Windows production installation uses four sign-in tasks:

- `Personal State MCP Collector`
- `Personal State MCP Dashboard`
- `Personal State MCP Cloudflare Tunnel`
- `Personal State MCP Production Watchdog`

All four run only in the signed-in user's interactive session. The collector, dashboard, and tunnel restart once per minute after failure, run on battery power, and have no execution timeout. The watchdog verifies local dashboard and process/task health every five minutes and records its latest non-sensitive result at `%LOCALAPPDATA%\PersonalStateMCP\production-health.json`.

For a Cloudflare Tunnel deployment, keep the dashboard bound to `127.0.0.1` and explicitly allow only the public hostname at the origin:

```powershell
$env:PERSONAL_STATE_DASHBOARD_ALLOWED_HOSTS = "health.example.com"
```

A private tunnel can route a user-controlled hostname to `http://127.0.0.1:8766`. Cloudflare Access must protect the hostname and validate Access identity before forwarding a request to the local origin.

The connector startup task runs [`scripts/start_cloudflare_tunnel.ps1`](scripts/start_cloudflare_tunnel.ps1). Its tunnel token is encrypted for the current Windows user with DPAPI and stored outside the repository at `%LOCALAPPDATA%\PersonalStateMCP\cloudflare-tunnel-token.dat`. Do not create an unauthenticated public route or commit the token.

## Samsung Health and Live Watch Companions

The Android project is in [`android/health-connect-companion`](android/health-connect-companion). The phone app is read-only, accepts a small pairing file through Android's document picker, stores credentials with Android Keystore-backed encryption, and rereads Health Connect after failed uploads instead of retaining a second health-data queue on the phone.

The phone's Health Connect path supplies history but does not guarantee current Samsung heart-rate delivery. The `wear` module runs an explicit Wear OS health foreground service backed by Health Services `ExerciseClient`, requests heart rate only, disables GPS, and uses the supported five-second screen-off batching mode. It relays each new direct sample to the matching signed phone app over both the immediate message path and an urgent latest-value Data Item. The Data Item survives brief Bluetooth, network, phone-sleep, and process interruptions; the phone deduplicates both paths by measurement time. A 45-second watch-side stream watchdog restarts this app's stalled exercise session automatically. The Data Layer channel requires the same package name and signing certificate on watch and phone. The dashboard polls a compact `/api/live` response every two seconds; the heavier selected-period history refresh remains once per minute.

The heart-rate chart keeps the recorded values and timestamps unchanged while drawing a centered, weighted seven-sample display curve within each continuous segment. Current values, tooltips, tables, exports, and APIs continue to use the raw measurements. For every heart-rate source, the line stays connected across gaps of up to one minute and shows a break when the gap is longer than one minute. Larger missing intervals remain visible rather than being interpolated.

The build is verified against Android API 36 and Health Connect `1.1.0`. Development APKs are generated at:

```text
android/health-connect-companion/app/build/outputs/apk/debug/app-debug.apk
android/health-connect-companion/wear/build/outputs/apk/debug/wear-debug.apk
```

The main Windows installer continues directly into a guided phone-and-watch setup by default. It installs the signed phone APK and then the matching signed watch APK in the same flow; `Install Phone and Watch.cmd` is also included for rerunning only that step. For broad distribution, the two builds use the same package name and signing identity and are prepared for one Google Play listing. Wear OS still requires the user to confirm installation and health permissions; Android does not permit a phone app to silently install another app on a watch.

Real-device activation remains intentionally separate from the synthetic build:

1. Create a dedicated Cloudflare Access service token and a separate ingest hostname/application. Do not reuse the interactive dashboard login.
2. Create a single-device pairing file containing the HTTPS ingest endpoint, device id, device secret, and Cloudflare Access service-token values.
3. Install the companion on the Galaxy phone, choose the pairing file, and grant only the desired Health Connect read categories. Older-history and background access are separate choices.
4. Confirm that Samsung Health is allowed to write the desired categories to Health Connect, then run the first sync.
5. Install the matching watch APK, grant heart-rate, notification, and all-time health access, then start monitoring. A persistent watch notification makes collection visible and provides a route back to the Stop control.

Use `scripts/create_phone_pairing.py` to store the Access credential in the OS keychain and create the temporary pairing file. Remove the plaintext file from the computer and phone immediately after Android imports it. Do not run connected-device tests against the production companion application because Android test setup can clear its encrypted pairing and Health Connect grants.

The Health Connect companion cannot trigger a Galaxy Watch-to-Samsung Health sync and cannot read data that Samsung does not export. Continuous monitoring uses more watch battery, yields while another exercise app owns Health Services, and cannot produce readings while the watch is off-body or has poor sensor contact. It is not an alarm or safety monitor. Samsung Health and Samsung Health Monitor remain authoritative for device features and official notices.

### Samsung Health Data SDK expansion

The non-proprietary server side is implemented behind schema `personal-state-watch-batch/v2`. It includes adapter-scoped identities and deletes, complete sleep-family reconciliation, per-adapter availability with a 26-hour freshness limit, source-aware dashboard labels, provenance-rich CSV export, a production-rejected synthetic adapter, and default-denied MCP policy for every new metric. The normative contract is [docs/watch-ingest-schema-v2.json](docs/watch-ingest-schema-v2.json).

The real phone reader is intentionally not bundled yet. Shipping it requires the owner or distributor to obtain Samsung Health Data SDK 1.1.0 under Samsung's terms, register the Android package and signing certificate, receive the needed data-type authorization, and complete real-device validation. A missing SDK is reported as `adapter_not_installed`; it is never presented as absent or normal physiology.

## Run MCP

Command for an MCP host:

```powershell
personal-state-mcp
```

Default transport is stdio through the Python MCP SDK.

Codex can register it globally with:

```powershell
codex mcp add personal_state -- .\.venv\Scripts\personal-state-mcp.exe
```

Pass the documented non-secret environment settings through the MCP host configuration. Keep the LibreLinkUp password in the OS credential store; never put it in MCP configuration.

## History Controls

```powershell
personal-state export-history .\glucose-history.jsonl
personal-state prune-history --days 365
personal-state delete-history --yes
```

## Test

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --check .\src\personal_state_mcp\dashboard_static\app.js
```

Android verification uses JDK 17, Gradle 8.11.1, Android API 36, and the project tasks `:app:testDebugUnitTest`, `:app:assembleDebug`, `:app:lintDebug`, `:wear:assembleDebug`, and `:wear:lintDebug`.

Tests use synthetic data only. Do not commit real glucose readings, account ids, sensor serials, credentials, screenshots, or payloads.

## Fable Review

The requested independent review is saved at [docs/review/fable-review.md](docs/review/fable-review.md).

The resolution log is saved at [docs/review/resolution-log.md](docs/review/resolution-log.md).

The Watch5 Pro design addendum, Fable review, and resolution log are saved at [docs/watch5-pro-design-addendum.md](docs/watch5-pro-design-addendum.md), [docs/review/fable-watch5-pro-review.md](docs/review/fable-watch5-pro-review.md), and [docs/review/watch5-pro-resolution-log.md](docs/review/watch5-pro-resolution-log.md).
