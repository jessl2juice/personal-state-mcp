# Personal State Installer Quick Start

## What this installs

Personal State gives you a private physiological history dashboard and a read-only MCP service for agents you choose to connect. The Windows installer adds:

- a private local dashboard at `http://127.0.0.1:8766/`;
- a background LibreLinkUp collector;
- long-term SQLite history stored under your Windows profile;
- the `health.*` MCP tools;
- an optional paired phone and Wear OS watch companion.

Personal State is not an alarm system, diagnosis system, treatment recommender, or medical device. Keep Libre and device safety alerts enabled.

## Before you begin

You need:

- Windows 10 or 11;
- 64-bit Python 3.12; setup can install it through Windows Package Manager when it is missing;
- a dedicated LibreLinkUp follower account that has accepted the sensor owner's invitation;
- for watch data, an Android phone, a paired Wear OS watch, and temporary developer access during companion installation.

Do not use the sensor owner's primary Libre account as the follower account.

## Install Personal State

1. Extract the complete release ZIP to a normal folder.
2. Double-click **Install Personal State.cmd**.
3. Enter the dedicated LibreLinkUp follower email.
4. Enter its password in the protected prompt. The password goes to Windows Credential Manager and is not written to the settings file.
5. At **Phone and watch setup**, press Enter to continue with both companion apps.
6. After setup completes, open the **Personal State** desktop shortcut.

The installer creates two current-user background tasks. They start the dashboard and collector after sign-in and restart them after ordinary failures. Administrator access is not required.

## Add the phone and watch

The main installer starts this guided setup automatically. To rerun only the device step later, double-click **Install Phone and Watch.cmd**.

1. Connect the Android phone by USB and approve USB debugging.
2. On the watch, open **Settings > Developer options > Wireless debugging**.
3. Choose **Pair new device** and enter the displayed address and code when setup asks.
4. Enter the watch's normal wireless-debugging address when setup asks.
5. Let setup install the phone app and then the matching watch app.
6. Open Personal State once on each device and approve only the requested health permissions.

Android requires the wearer to confirm software installation and health permissions. The phone app cannot silently install software on a watch. The combined installer performs every step the platform permits automatically.

For a future Google Play release, the phone and watch builds share one app identity and listing. Installing the phone app makes the matching watch app available for the paired watch; the user still confirms the watch installation.

## Connect the MCP

The installed MCP launcher is:

```text
%LOCALAPPDATA%\Programs\PersonalState\scripts\start_mcp.ps1
```

Configure the agent host to run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%LOCALAPPDATA%\Programs\PersonalState\scripts\start_mcp.ps1"
```

The MCP is local and read-only. Agents cannot change thresholds, credentials, safety instructions, or treatment.

## Data and privacy

- Health history: `%LOCALAPPDATA%\PersonalStateMCP\state.db`
- Non-secret settings: `%LOCALAPPDATA%\PersonalStateMCP\settings.psd1`
- Libre password and device credentials: OS credential stores
- Application files: `%LOCALAPPDATA%\Programs\PersonalState`

The default dashboard listens only on this computer. Do not expose port 8766 directly to the internet. Remote access requires a separately configured, authenticated tunnel and access policy.

## Upgrade or remove

Run a newer **Install Personal State.cmd** to upgrade in place. Your database and credentials are preserved.

Run **Uninstall Personal State.cmd** to remove the application while preserving history. An administrator can run `Uninstall-PersonalState.ps1 -DeleteData` when the user explicitly wants the local history and configured credentials removed too.

## First checks

After installation:

1. The dashboard opens at `http://127.0.0.1:8766/`.
2. Glucose shows a measurement time, received time, age, freshness, and provenance.
3. The official Libre app remains responsible for alerts.
4. Heart rate appears in the live position only when the sample is no more than 60 seconds old.
5. Old or missing watch data is labeled clearly and never presented as live.
