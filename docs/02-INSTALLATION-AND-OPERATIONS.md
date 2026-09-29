# Personal State Installation and Operations Guide

Document version: 1.0  
Last verified: 2026-09-29
Intended readers: trusted installers and operators

## Supported production-pilot topology

- Windows host running the Python service, SQLite history, dashboard, collector, and Cloudflare Tunnel.
- FreeStyle Libre 3 Plus shared to a dedicated LibreLinkUp follower account.
- Android phone companion 0.4.2 or later.
- Galaxy Watch companion 0.4.2 or later on a supported Wear OS watch.
- Optional Fitbit or Google wearable connected through the read-only Google Health API.
- Private dashboard protected by Cloudflare Access.
- Separate authenticated ingest hostname and policy for the phone companion.

The dashboard origin must remain bound to loopback. Do not expose port 8766 directly to a LAN or the public internet.

## Prerequisites

- Git, Python 3.11 or later, and PowerShell 5.1 or later. Python 3.12 is the release and CI baseline.
- A Windows account with access to its OS credential store.
- A working Libre follower invitation and dedicated follower account.
- A supported Android phone with Health Connect.
- A paired Wear OS watch with heart-rate permission.
- Cloudflare Tunnel and Access configured with separate dashboard and ingest applications.
- Stable signing identity shared by the phone and watch applications.

## Server installation

From the project root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev,mcp,keychain]"
```

Configure non-secret settings in the service environment. Store the Libre password, watch device secret, watch identifier key, Cloudflare tunnel token, and Access service credentials in their designated OS-protected stores. Do not place them in source control, documentation, task arguments, screenshots, or MCP configuration.

Required Libre setup:

```powershell
$env:LIBRELINKUP_EMAIL = "dedicated-follower@example.invalid"
personal-state set-libre-password dedicated-follower@example.invalid
```

The example address is intentionally nonfunctional. Use the dedicated follower identity provisioned for the deployment.

Common settings:

```text
PERSONAL_STATE_MCP_DB
PERSONAL_STATE_MCP_HOST_ID
PERSONAL_STATE_MCP_ALLOWED_HOSTS
PERSONAL_STATE_MCP_SOURCE_TIMEZONE
PERSONAL_STATE_DASHBOARD_ALLOWED_HOSTS
PERSONAL_STATE_WATCH_ENABLED
PERSONAL_STATE_WATCH_INGEST_HOSTS
PERSONAL_STATE_WATCH_RETENTION_DAYS
PERSONAL_STATE_WATCH_MCP_METRICS
PERSONAL_STATE_GOOGLE_HEALTH_ENABLED
PERSONAL_STATE_GOOGLE_HEALTH_SYNC_SECONDS
PERSONAL_STATE_GOOGLE_HEALTH_RECENT_HOURS
```

Keep the default glucose threshold at 80 mg/dL unless the user explicitly changes it outside agent control.

The complete source-development sequence, repository map, Android prerequisites, and smoke tests are maintained in the root [README](../README.md). Direct CLI commands read environment variables. The supplied `scripts\start_*.ps1` launchers load `%LOCALAPPDATA%\PersonalStateMCP\settings.psd1` first.

## Libre activation

1. Confirm the dedicated follower account is verified.
2. Confirm the primary Libre account has shared data with that follower.
3. Store the follower password in the Windows credential store.
4. Run `personal-state collect-once`.
5. Run `personal-state status` and confirm a successful collection without printing secrets.
6. Confirm the dashboard shows measurement time, receipt time, source, and age.

The Libre-compatible interface is unofficial and may change. Keep the official Libre app as the alert and source-of-truth layer.

## Google Health and Fitbit activation

1. Enable Google Health API in an isolated Google Cloud project.
2. Create a read-only OAuth client and download its JSON file outside the repository.
3. Run `personal-state google-health-connect C:\private\client.json` and open the printed authorization URL.
4. Run the command again with `--code` and the returned code or complete redirected localhost URL.
5. Run `personal-state google-health-status` and confirm the expected scopes and paired-device metadata.
6. Run `personal-state google-health-sync --hours 36`.
7. Set `GoogleHealthEnabled = $true` in the non-secret settings file for scheduled collection.

OAuth client secret and refresh token material is stored in the OS credential store. Google Health observations are synchronized records and never receive the direct-watch live label. Fitbit data may remain absent until the Fitbit phone application completes its own synchronization.

## Phone companion activation

1. Build or obtain the signed phone APK that matches the deployment signing identity.
2. Generate a one-device pairing file with the private ingest URL, device id, device secret, and ingest Access service-token values.
3. Transfer the pairing file to the phone through a trusted path.
4. Open Personal State on the phone and import the file using Android's document picker.
5. Delete the plaintext pairing file from both computer and phone immediately after import.
6. Grant only the intended read permissions in Health Connect.
7. Grant history or background permission only when the user explicitly wants those capabilities.
8. Run Sync now and verify the server reports a successful companion upload.

The phone stores pairing material with Android Keystore-backed encryption. It requests no Health Connect write permission and no exercise-route or location permission.

## Watch companion activation

1. Install the watch APK signed with the same certificate and package name as the phone app.
2. Open Personal State on the watch.
3. Grant heart-rate, notification, and background or all-time health access requested by the watch.
4. Tap Start monitoring.
5. Confirm the persistent `Personal State monitoring` notification is present.
6. Confirm the watch screen reports `Live to phone` and a recent value.
7. Confirm the phone app shows a recent watch heart-rate value.
8. Confirm the dashboard's live heart-rate timestamp advances with each five-second watch delivery and remains within ten seconds.
9. Disconnect installation tooling and observe the feed for at least five minutes before declaring activation complete.

The watch sends each new sample over two Wear OS Data Layer paths: an immediate message and an urgent latest-value Data Item. The phone deduplicates the paths by measurement timestamp. The watch restarts its own stalled Health Services exercise stream after 45 seconds without a sample when another application does not own the exercise session.

## Production services

The Windows production pilot uses these scheduled tasks:

- `Personal State MCP Collector`
- `Personal State MCP Dashboard`
- `Personal State MCP Cloudflare Tunnel`
- `Personal State MCP Production Watchdog`

The first three restart after failure, run on battery power, and have no execution timeout. The watchdog runs in the signed-in user's interactive session, checks service health every five minutes, restarts failed tasks, and writes a non-sensitive status file to:

```text
%LOCALAPPDATA%\PersonalStateMCP\production-health.json
```

The private tunnel token is protected with Windows DPAPI and stored outside the repository at:

```text
%LOCALAPPDATA%\PersonalStateMCP\cloudflare-tunnel-token.dat
```

## Routine operating checks

### Daily

- Confirm the dashboard opens through Access.
- Confirm glucose age and heart-rate age are plausible.
- Confirm a live heart-rate number is shown only when age is ten seconds or less.
- Confirm the watch notification remains present when monitoring is intended.

### Weekly

- Review `production-health.json` for repeated recoveries.
- Confirm collector, dashboard, tunnel, and watchdog tasks are present and running.
- Review dashboard coverage and identify unexplained gaps.
- Confirm disk space is sufficient for the SQLite database and backups.

### After any update

- Verify phone and watch package signatures before installation.
- Use in-place updates. Do not uninstall a paired production app unless credentials and permissions are intentionally being reset.
- Confirm installed versions and preserve the phone's original first-install time.
- Verify live heart-rate timestamps advance for at least five minutes without USB or debugger dependence.
- Run synthetic tests before using real-data verification.

## Backup, export, retention, and deletion

The primary long-term record is the local SQLite database. Protect backups as health data.

Glucose controls:

```powershell
personal-state export-history .\glucose-history.jsonl
personal-state prune-history --days 365
personal-state delete-history --yes
```

The dashboard provides selected-range CSV exports for glucose and watch data. Exports contain sensitive health information and should be shared only through an approved secure channel.

Watch retention defaults to 3,650 days. Glucose retention is indefinite unless configured. Changes to retention are operator actions and must not be exposed to an agent.

## MCP registration

Run the MCP server over stdio:

```powershell
personal-state-mcp
```

Register the executable with the chosen MCP host and pass only documented non-secret settings. Keep passwords and device secrets in the OS credential store. Verify the configured host id is included in the allowlist before enabling agent access.

## Decommissioning

1. Export any history the user wants to retain.
2. Stop and remove the four scheduled tasks.
3. Revoke the Cloudflare tunnel and ingest credentials.
4. Remove the Libre follower relationship if no longer needed.
5. Delete OS-stored application credentials.
6. Delete the local database and protected token files using an approved process.
7. Uninstall the phone and watch applications.
8. Confirm public DNS and Access applications no longer route to the origin.

Document the date, operator, retained exports, revoked credentials, and deletion verification without recording secret values.
