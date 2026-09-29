# Personal State MCP

Personal State is a private, read-only physiological context service. It combines Libre glucose, Galaxy Watch and Samsung Health records, and synchronized Fitbit/Google Health records in one normalized history, dashboard, and Model Context Protocol (MCP) server.

The primary use case is an authorized agent noticing that the user seems unusually confused, inconsistent, indecisive, or "off" and requesting time-bounded physiological context. Personal State reports recorded facts, timestamps, source, freshness, and limitations. It does not diagnose causality, automate treatment, replace clinician judgment, or act as an alarm. Official Libre, Samsung, Fitbit, and Google applications remain authoritative for their device features and safety notices.

## Start Here

- **New developer:** follow [Developer Quick Start](#developer-quick-start).
- **Release installer user:** follow [Install a Packaged Release](#install-a-packaged-release).
- **Operator:** read [Installation and Operations](docs/02-INSTALLATION-AND-OPERATIONS.md) and [Troubleshooting](docs/05-TROUBLESHOOTING-RUNBOOK.md).
- **Agent or API developer:** read [API and Data Reference](docs/04-API-AND-DATA-REFERENCE.md).
- **Clinical reviewer:** read [User and Clinician Guide](docs/01-USER-AND-CLINICIAN-GUIDE.md).
- **Security reviewer:** read [Architecture, Security, and Privacy](docs/03-ARCHITECTURE-SECURITY-PRIVACY.md).

The complete set is indexed in [Personal State Documentation](docs/00-DOCUMENTATION-INDEX.md).

## System At A Glance

| Source | Path into Personal State | Timing promise |
| --- | --- | --- |
| FreeStyle Libre 3 Plus | Official Libre sharing -> dedicated LibreLinkUp follower -> Python adapter | Near real time when Abbott's follower service is current; stale readings are withheld from threshold context |
| Galaxy Watch heart rate | Wear OS Health Services -> watch app -> phone relay -> authenticated ingest | Labeled live only while the measured sample is no more than 10 seconds old |
| Samsung Health history | Samsung Health -> Health Connect or licensed Samsung Health Data SDK -> phone app -> authenticated ingest | Recorded history with source timestamps; never promoted to live without direct-watch evidence |
| Fitbit Air / Google wearable | Fitbit app sync -> Google Health API v4 -> Python adapter | Synchronized records, never second-by-second live data |
| Google Fit and LibreView exports | User-owned archive -> explicit importer -> normalized SQLite history | Historical only, with original timestamps and provenance |

Every public health response carries measurement time, receipt or synchronization time when available, storage time, age, freshness, provenance, and a safety boundary. Missing data remains missing; the system does not invent samples or infer normal physiology from absence.

## Current Release

- Python service: `0.4.2`
- Android phone companion: `0.4.2`, version code `40200`
- Wear OS companion: `0.4.2`, version code `40201`
- Python support: 3.11 or later; Python 3.12 is the release and CI baseline
- Android build baseline: JDK 17, Gradle 8.11.1, compile SDK 36
- Storage: local SQLite, indefinite glucose retention by default, 3,650-day wearable retention by default

## Developer Quick Start

These steps bring up the Python service, dashboard, MCP server, and tests on a clean Windows 10 or 11 machine. Libre, Android, Cloudflare, and Google Health are optional integrations and can be added after the empty local service is running.

### 1. Install prerequisites

Required:

- Git for Windows.
- 64-bit Python 3.12 recommended; Python 3.11 is the minimum.
- Windows PowerShell 5.1 or PowerShell 7.

Optional:

- Current Node.js LTS for the dashboard JavaScript syntax check. Node is not needed at runtime.
- JDK 17 and Android SDK Platform 36 for phone/watch development. Android Studio is the simplest way to install both.
- Android platform tools (`adb`) for real-device installation.
- A Cloudflare account and `cloudflared` only for private remote access.
- A Google Cloud OAuth client only for Fitbit/Google Health synchronization.

Confirm the core tools:

```powershell
git --version
py -3.12 --version
```

### 2. Clone and create the environment

```powershell
git clone https://github.com/jessl2juice/personal-state-mcp.git
cd personal-state-mcp
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev,mcp,keychain]"
```

The editable install creates these commands inside `.venv\Scripts`:

- `personal-state`: collection, credentials, status, imports, and Google Health commands.
- `personal-state-dashboard`: local dashboard.
- `personal-state-mcp`: stdio MCP server.
- `personal-state-takeout`: local Google Takeout search index.

Use the explicit executable path below so a fresh shell does not depend on virtual-environment activation.

### 3. Run tests before adding private data

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

When Node.js is installed:

```powershell
node --check .\src\personal_state_mcp\dashboard_static\app.js
```

Expected Python result for release 0.4.2 is 66 passing tests. Tests use synthetic data only.

### 4. Create local non-secret settings

Personal State reads environment variables. The supplied PowerShell launchers first load a non-secret settings file and then start the service.

```powershell
$settingsDir = Join-Path $env:LOCALAPPDATA "PersonalStateMCP"
New-Item -ItemType Directory -Force $settingsDir | Out-Null
Copy-Item .\config.psd1.example (Join-Path $settingsDir "settings.psd1")
notepad (Join-Path $settingsDir "settings.psd1")
```

Do not put passwords, OAuth client secrets, device secrets, Access tokens, or tunnel tokens in `settings.psd1`. They belong in the Windows credential store or DPAPI-protected files.

To load settings into the current development shell:

```powershell
. .\scripts\load_settings.ps1
Import-PersonalStateSettings | Out-Null
```

Direct `personal-state` commands read the current process environment. The `scripts\start_*.ps1` launchers load `%LOCALAPPDATA%\PersonalStateMCP\settings.psd1` automatically.

### 5. Start an empty local dashboard

No vendor account is required to verify the local UI and database path.

```powershell
.\.venv\Scripts\personal-state.exe status
.\.venv\Scripts\personal-state-dashboard.exe --host 127.0.0.1 --port 8766
```

Open [http://127.0.0.1:8766](http://127.0.0.1:8766). Stop the foreground server with `Ctrl+C`. The dashboard intentionally refuses non-loopback binds.

### 6. Register the MCP server

Run it directly:

```powershell
.\.venv\Scripts\personal-state-mcp.exe
```

Or register it with Codex from the repository root:

```powershell
$mcp = (Resolve-Path .\.venv\Scripts\personal-state-mcp.exe).Path
codex mcp add personal_state -- $mcp
```

The MCP transport is stdio. Pass only documented non-secret environment variables through the MCP host. The host id must be present in `PERSONAL_STATE_MCP_ALLOWED_HOSTS`.

At this point a new developer has a working local service. The following sections add real sources.

## Configure Data Sources

### LibreLinkUp

Use a dedicated LibreLinkUp follower account, not the primary Libre account paired to the sensor.

1. Verify the follower email.
2. Accept the sharing invitation from the sensor owner's Libre account.
3. Put the follower email in `settings.psd1` as `LibreEmail`.
4. Store the password in Windows Credential Manager:

```powershell
. .\scripts\load_settings.ps1
Import-PersonalStateSettings | Out-Null
.\.venv\Scripts\personal-state.exe set-libre-password dedicated-follower@example.com
.\.venv\Scripts\personal-state.exe collect-once
.\.venv\Scripts\personal-state.exe status
```

For CI or disposable local testing only, `LIBRELINKUP_PASSWORD` can override the keychain. Never commit it. The Libre-compatible interface is unofficial and can change without notice.

### Fitbit Air / Google Health

Google Health is read-only and optional. Personal State requests activity and fitness, health metrics and measurements, and sleep scopes. It does not request write, location, nutrition, ECG, irregular-rhythm, profile, or settings access.

1. Enable Google Health API in an isolated Google Cloud project.
2. Create the OAuth client and download its JSON file outside the repository.
3. Print the authorization URL:

```powershell
.\.venv\Scripts\personal-state.exe google-health-connect C:\private\google-oauth-client.json
```

4. Open the printed URL, approve the requested read-only scopes, and copy the complete redirected localhost URL or authorization code.
5. Complete authorization:

```powershell
.\.venv\Scripts\personal-state.exe google-health-connect C:\private\google-oauth-client.json --code "<complete redirected URL>"
.\.venv\Scripts\personal-state.exe google-health-status
.\.venv\Scripts\personal-state.exe google-health-sync --hours 36
```

The client secret, refresh token, redirect URI, and scopes are stored in the OS keychain. Enable scheduled collection with `GoogleHealthEnabled = $true` in `settings.psd1`. A longer import can be started explicitly:

```powershell
.\.venv\Scripts\personal-state.exe google-health-backfill --days 365
```

Fitbit data appears only after the Fitbit app synchronizes it to Google Health. It is always labeled as synchronized or last recorded, never live. Exact Fitbit Air attribution is used only when Google supplies matching platform and device evidence.

### Galaxy Phone And Watch

The Android project is [android/health-connect-companion](android/health-connect-companion):

- `app`: Android phone companion for Health Connect, optional Samsung Health Data SDK reads, watch relay, and authenticated upload.
- `wear`: Wear OS companion for passive heart-rate monitoring and short lease-bounded live sessions.

Build the public debug variants:

```powershell
cd .\android\health-connect-companion
.\gradlew.bat --no-daemon :app:testDebugUnitTest :app:lintDebug :wear:lintDebug :app:assembleDebug :wear:assembleDebug
cd ..\..
```

Outputs:

```text
android/health-connect-companion/app/build/outputs/apk/debug/app-debug.apk
android/health-connect-companion/wear/build/outputs/apk/debug/wear-debug.apk
```

Phone and watch packages must have the same package name and signing certificate for Wear OS Data Layer delivery. Android still requires the user to confirm app installation and health permissions.

The public build uses Health Connect and reports the proprietary Samsung adapter as not installed. A private Samsung-enabled build requires a legitimately obtained Samsung Health Data SDK 1.1.0 AAR:

```powershell
.\scripts\verify_samsung_sdk_build.ps1 -SamsungSdkAar "C:\private\samsung-health-data-api-1.1.0.aar"
```

Do not commit or redistribute the AAR.

For an owner deployment, create the temporary phone pairing file:

```powershell
.\.venv\Scripts\python.exe .\scripts\create_phone_pairing.py --device-id "<unique-device-id>" --endpoint "https://<private-ingest-host>/api/watch/ingest" --output "$env:TEMP\personal-state-pairing.json" --schema-v2
```

The command prompts for protected secrets as needed. Import the file through the phone app's document picker, then delete the plaintext file from the computer and phone. Grant only the health categories the user wants to share.

The watch uses battery-safe passive monitoring at rest. Opening the dashboard or accessing an MCP health tool creates a short renewable live lease; during that lease only, the watch runs a visible heart-rate foreground service with GPS disabled. A dashboard heart-rate number appears only while the direct measurement is no more than 10 seconds old.

## Run The Services

Use separate PowerShell windows:

```powershell
.\scripts\start_collector.ps1
```

```powershell
.\scripts\start_dashboard.ps1
```

```powershell
.\scripts\start_mcp.ps1
```

The collector persists Libre and enabled Google Health data. Phone/watch data arrives through the authenticated dashboard ingest endpoint.

## Configuration Reference

The defaults live in [src/personal_state_mcp/config.py](src/personal_state_mcp/config.py).

| Environment variable | Settings key | Default / purpose |
| --- | --- | --- |
| `PERSONAL_STATE_MCP_DATA_DIR` | `DataDirectory` | `%LOCALAPPDATA%\PersonalStateMCP` |
| `PERSONAL_STATE_MCP_DB` | none | `<data dir>\state.db` |
| `LIBRELINKUP_EMAIL` | `LibreEmail` | Dedicated follower identity |
| `PERSONAL_STATE_MCP_HOST_ID` | `HostId` | `default-local` |
| `PERSONAL_STATE_MCP_ALLOWED_HOSTS` | `AllowedHosts` | `default-local`; comma-separated |
| `PERSONAL_STATE_MCP_RATE_LIMIT_PER_MINUTE` | none | `30` per MCP host |
| `PERSONAL_STATE_MCP_OPPORTUNISTIC_REFRESH` | none | `false`; MCP reads do not contact vendors |
| `PERSONAL_STATE_MCP_MIN_POLL_SECONDS` | none | `60` |
| `PERSONAL_STATE_MCP_SOURCE_TIMEZONE` | `SourceTimezone` | Required for timestamp-naive LibreView imports |
| `PERSONAL_STATE_MCP_GLUCOSE_THRESHOLD` | `GlucoseThresholdMgDl` | `80` mg/dL, conversational context only |
| `PERSONAL_STATE_MCP_NEAR_THRESHOLD_MARGIN` | none | `10` mg/dL |
| `PERSONAL_STATE_MCP_FRESH_SECONDS` | none | `600` |
| `PERSONAL_STATE_MCP_RECENT_SECONDS` | none | `1800` |
| `PERSONAL_STATE_MCP_FUTURE_SKEW_SECONDS` | none | `300` |
| `PERSONAL_STATE_MCP_RETENTION_DAYS` | none | Unset, so glucose history is not pruned |
| `LIBRELINKUP_VERSION` | none | `4.16.0` compatibility header |
| `LIBRELINKUP_PRODUCT` | none | `llu.android` |
| `LIBRELINKUP_BASE_URL` | none | `https://api.libreview.io` |
| `PERSONAL_STATE_DASHBOARD_ALLOWED_HOSTS` | `DashboardAllowedHosts` | Additional public dashboard hosts |
| `PERSONAL_STATE_WATCH_ENABLED` | `WatchEnabled` | `false` |
| `PERSONAL_STATE_WATCH_INGEST_HOSTS` | `WatchIngestHosts` | Hosts allowed to serve ingest only |
| `PERSONAL_STATE_WATCH_DEVICE_ID` | `WatchDeviceId` | Paired device lookup |
| `PERSONAL_STATE_WATCH_RETENTION_DAYS` | none | `3650` |
| `PERSONAL_STATE_WATCH_MCP_METRICS` | none | Comma-separated agent exposure allowlist |
| `PERSONAL_STATE_GOOGLE_HEALTH_ENABLED` | `GoogleHealthEnabled` | Auto-enables when credentials exist unless explicitly disabled |
| `PERSONAL_STATE_GOOGLE_HEALTH_SYNC_SECONDS` | `GoogleHealthSyncSeconds` | `300` |
| `PERSONAL_STATE_GOOGLE_HEALTH_RECENT_HOURS` | `GoogleHealthRecentHours` | `36` |

Settings that affect thresholds, safety, retention, permissions, or agent exposure require explicit user action. No MCP tool can change them.

`LIBRELINKUP_PASSWORD`, `PERSONAL_STATE_WATCH_DEVICE_SECRET`, and `PERSONAL_STATE_WATCH_IDENTIFIER_KEY` are secret environment fallbacks for controlled testing. Production uses the OS credential store instead.

## MCP Surface

- `health.current_state()`: compact glucose plus allowlisted wearable context.
- `health.glucose()`: newest stored glucose with freshness and provenance.
- `health.glucose_recent(hours=3, limit=96)`: recent glucose history and gaps.
- `health.context()`: configured 80 mg/dL decision-support context, gated by freshness.
- `health.watch()`: latest allowlisted Galaxy, Samsung, Fitbit, and Google Health observations.
- `health.watch_recent(metric, hours=24, limit=200, cursor=None)`: paged history for one allowlisted metric.

All tools are read-only. They cannot alter credentials, permissions, thresholds, safety wording, or treatment.

## Dashboard Behavior

The consolidated dashboard provides one-glance source status followed by detailed graphs and records:

- Glucose and direct Galaxy heart rate with units, timestamps, age, freshness, and provenance.
- Day, week, month, year, and complete-history views on a truthful recorded-time axis.
- Source-aware Galaxy, Samsung Health, Google Fit archive, and Fitbit/Google Health records.
- Raw values in cards, tooltips, tables, exports, and APIs.
- Display-only heart-rate smoothing within continuous segments.
- A visible line break for gaps longer than one minute.
- Clinician-ready print and selected-range CSV exports.
- A one-second compact live refresh and a one-minute heavier history refresh.

The dashboard binds only to `127.0.0.1`. Remote access must use an authenticated private tunnel. Keep dashboard and device-ingest hostnames and Cloudflare Access policies separate.

## Historical Imports And Search

Inspect an archive before importing:

```powershell
.\.venv\Scripts\personal-state.exe inspect-history-export C:\path\to\archive.zip
```

Dry-run first, then repeat without `--dry-run`:

```powershell
.\.venv\Scripts\personal-state.exe import-libreview C:\path\to\glucose.csv --timezone America/Los_Angeles --dry-run
.\.venv\Scripts\personal-state.exe import-google-fit C:\path\to\takeout.zip --dry-run
```

Each real import creates a pre-import database backup and deduplicates records. Imported data remains historical and cannot occupy a live card.

Build a separate local full-text index for non-Fit Google Takeout content:

```powershell
.\.venv\Scripts\personal-state-takeout.exe index C:\path\to\takeout-001.zip C:\path\to\takeout-002.zip
.\.venv\Scripts\personal-state-takeout.exe search "search terms" --limit 20
.\.venv\Scripts\personal-state-takeout.exe status
```

See [Historical Backfill](docs/08-HISTORICAL-BACKFILL.md) before importing user data.

## Install A Packaged Release

The source repository and the signed release installer serve different audiences. A release ZIP is self-contained and installs for the current Windows user without administrator privileges.

1. Download and extract `Personal-State-0.4.2-Windows.zip`.
2. Verify `SHA256SUMS.txt`.
3. Run `Install Personal State.cmd`.
4. Enter the dedicated LibreLinkUp follower identity when prompted.
5. Run `Install Phone and Watch.cmd` to add both Android companions.
6. Open the **Personal State** desktop shortcut.

The installer places application files under `%LOCALAPPDATA%\Programs\PersonalState` and data under `%LOCALAPPDATA%\PersonalStateMCP`. Upgrades preserve history and credentials. See [Installer Quick Start](docs/INSTALLER-QUICK-START.md).

## Production On Windows

The packaged owner deployment uses four current-user scheduled tasks:

- `Personal State MCP Collector`
- `Personal State MCP Dashboard`
- `Personal State MCP Cloudflare Tunnel`
- `Personal State MCP Production Watchdog`

They run only while that Windows user has an interactive session. The watchdog records non-sensitive status at `%LOCALAPPDATA%\PersonalStateMCP\production-health.json`.

For private remote access:

1. Keep the origin at `http://127.0.0.1:8766`.
2. Add only the intended public dashboard hostname to `DashboardAllowedHosts`.
3. Protect it with Cloudflare Access.
4. Use a different hostname and service-token policy for phone ingest.
5. Store the tunnel token with the supplied DPAPI script, never in Git or task arguments.

Run [scripts/configure_production_tasks.ps1](scripts/configure_production_tasks.ps1) only after the installer-created collector, dashboard, and tunnel tasks exist.

## Build A Release

Initialize Android release signing once:

```powershell
.\scripts\initialize_android_signing.ps1
```

Build and verify:

```powershell
.\scripts\build_release.ps1 -Version 0.4.2
```

The build runs Python tests, creates the offline wheelhouse, runs Android release tests and lint, builds signed APK/AAB artifacts, writes SHA-256 checksums, and creates `release\Personal-State-0.4.2-Windows.zip`. Signing material and the optional Samsung SDK AAR remain outside the repository.

## Repository Map

```text
src/personal_state_mcp/        Python adapters, storage, service, dashboard, MCP, CLI
src/personal_state_mcp/
  dashboard_static/            Browser UI assets
android/health-connect-companion/
  app/                         Android phone companion
  wear/                        Wear OS companion
scripts/                       Launch, pairing, signing, release, and operations tools
installer/                     Current-user Windows installer and uninstallers
docs/                          User, clinician, engineering, security, and review record
tests/                         Synthetic Python contract and behavior tests
.github/workflows/ci.yml       Windows Python/web and Android CI
config.psd1.example            Non-secret owner settings template
```

## Verification Checklist

Before calling a new machine ready:

1. `python -m pytest -q` passes.
2. `personal-state status` reports the intended database path and host policy.
3. The dashboard opens locally and exposes no real value as live when data is absent or stale.
4. Libre collection succeeds only after the follower credential is stored.
5. Google Health status reports only the scopes and device metadata actually authorized.
6. Phone and watch APK signatures match before device installation.
7. Direct watch sample, phone relay, server ingest, and dashboard timestamps advance without USB or debugger dependence.
8. The ingest hostname cannot serve the dashboard.
9. No database, archive, OAuth JSON, pairing file, credential, tunnel token, signing key, proprietary AAR, screenshot, or real health payload is tracked by Git.

## Troubleshooting A Fresh Setup

- **`py -3.12` not found:** install 64-bit Python 3.12 and enable the Python launcher.
- **PowerShell blocks a script:** use `powershell.exe -NoProfile -ExecutionPolicy Bypass -File <script>`; do not weaken the machine-wide policy.
- **Keychain command fails:** reinstall with `.[keychain]` and confirm Windows Credential Manager is available for the signed-in user.
- **Dashboard shows no data:** this is correct before a successful collection, import, or authenticated phone upload.
- **Heart rate says no live data:** the newest direct watch sample is older than 10 seconds; follow the timestamp boundary checklist in the troubleshooting runbook.
- **Fitbit is connected but empty:** sync the Fitbit phone app, then run `google-health-status` and `google-health-sync`.
- **Gradle cannot find Android SDK:** install Platform 36 and set `ANDROID_HOME` or create the usual untracked `local.properties`.
- **Watch does not relay:** verify matching package/signing identity, permissions, Data Layer connectivity, and a running live lease.

## Security And Data Handling

- Secrets are stored in the OS keychain, Android Keystore, or DPAPI-protected files where practical.
- SQLite databases and exports contain sensitive health data; protect backups and sharing channels.
- The repository must contain synthetic fixtures only.
- The dashboard sends `Cache-Control: no-store` and rejects unapproved hosts.
- Device ingest uses Cloudflare Access service identity plus device HMAC, replay protection, strict schemas, and transactional writes.
- Delete configured credentials with `personal-state delete-credentials --yes` only after an explicit user decision.

## Design And Review Record

- [Detailed design](docs/design.md)
- [Galaxy Watch5 Pro design](docs/watch5-pro-design-addendum.md)
- [Google Health / Fitbit Air design](docs/google-health-fitbit-air-design.md)
- [Fable initial review](docs/review/fable-review.md)
- [Fable watch review](docs/review/fable-watch5-pro-review.md)
- [Fable Google Health / Fitbit review](docs/review/fable-google-health-fitbit-air-review.md)
- [Resolution logs](docs/review/resolution-log.md)

The current behavior, not an older design proposal, is authoritative. When code, versions, source behavior, safety boundaries, or deployment topology change, update this README and the documentation index in the same change.
