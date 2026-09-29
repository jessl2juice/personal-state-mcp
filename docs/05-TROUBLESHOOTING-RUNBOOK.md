# Personal State Troubleshooting Runbook

Document version: 1.0  
Last verified: 2026-09-29
Intended readers: user and trusted operators

## First principle

Never make old data look live. Recovery is complete only when timestamps continue advancing under normal use. A single new reading after connecting USB, opening a debugger, or waking a device is not proof of a stable fix.

For safety-sensitive questions, use the official Libre or Samsung application and established care guidance while troubleshooting Personal State.

## Dashboard says `No live data` for heart rate

This is the intended display when the newest direct heart-rate measurement is more than ten seconds old. The watch normally delivers every five seconds, so the card remains stable while still withholding data after two expected deliveries are missed.

### User checks

1. Look at the Galaxy Watch. Confirm it is on the wrist with good sensor contact.
2. Open the Personal State watch app.
3. Confirm monitoring is on and the status is not `Monitoring stopped`.
4. Confirm the persistent `Personal State monitoring` notification is present.
5. Confirm the phone and watch are Bluetooth-connected.
6. Open the Personal State phone app and confirm it is paired.
7. Keep the phone on a working network and wait up to one minute.

The current watch release automatically restarts its own stalled heart stream after 45 seconds. Version 0.4.2 also retries after boot and user unlock, and accepts an app-private resume request from the paired phone only when monitoring was previously enabled. If another workout application owns Health Services, Personal State waits and resumes afterward.

### Operator checks

1. Compare the latest watch-screen sample time, phone relay time, server ingest time, and dashboard time.
2. Determine where timestamps stop advancing: watch sensor, watch-to-phone, phone-to-server, or dashboard.
3. Confirm phone and watch version 0.4.2 or later.
4. Confirm phone and watch package signatures match.
5. Confirm the watch foreground service is running and has heart-rate, notification, and background access.
6. Confirm the phone received either Wear OS message or Data Item events.
7. Confirm authenticated ingest succeeds and replay protection is not rejecting new batches.
8. Observe at least five minutes after recovery with USB and debugger disconnected.

Do not use permanent USB attachment as a workaround. If USB changes behavior, investigate background execution, process state, Data Layer delivery, and network reachability.

## Watch shows a value but the phone does not

Likely boundary: Wear OS Data Layer.

1. Confirm Bluetooth connection and that both devices use the same Personal State package identity.
2. Confirm both APKs are signed by the same certificate.
3. Confirm Google Play services and Wear OS connectivity are healthy.
4. Leave the watch app and phone app open briefly to force a visible status refresh.
5. Restart monitoring from the watch app.
6. If maintenance tooling is required, enable watch wireless debugging only for the maintenance session and turn it off afterward.

The watch sends both an immediate message and an urgent latest-value Data Item. Failure of one path should not permanently stop delivery.

## Phone shows live heart rate but dashboard does not

Likely boundary: phone-to-ingest or server processing.

1. Confirm the phone reports successful pairing and no upload error.
2. Confirm internet access on the phone.
3. Confirm the ingest hostname health check is reachable.
4. Verify the dedicated ingest Access service token is active.
5. Verify device HMAC credentials match the server-side credential store.
6. Check sanitized server status for authentication, schema, replay, or database errors.
7. Confirm the dashboard reads the same SQLite database used by the ingest service.

Do not reuse the interactive dashboard login as the phone's ingest identity.

## Heart-rate line has gaps

The chart bridges gaps up to one minute and breaks for longer gaps. A break means there was not a continuous recorded stream.

Check:

- Wrist contact and watch fit.
- Watch battery and power-saving state.
- Whether another workout app owned Health Services.
- Whether monitoring or its persistent notification stopped.
- Bluetooth and phone connectivity.
- Phone upload timing and server acceptance.

Do not fill longer gaps with invented samples. Display smoothing changes the line shape only within recorded segments.

## Glucose is stale or missing

1. Check the official Libre app first.
2. Confirm the sensor is active and the official app has a current value.
3. Confirm the dedicated follower account still receives shared data.
4. Confirm its password remains in the OS credential store.
5. Run a single guarded refresh or `personal-state collect-once`.
6. Review the sanitized collector result.
7. If authentication or endpoint behavior changed, stop repeated login attempts and inspect the adapter against current upstream behavior.

Personal State must not present a stale stored glucose value as current or interpret it against the 80 mg/dL context threshold.

## Fitbit or Google Health is connected but no data appears

1. Open the Fitbit application on the phone and complete a device synchronization.
2. Run `personal-state google-health-status` and confirm the expected account grant and paired-device metadata.
3. Run `personal-state google-health-sync --hours 36`.
4. Check sanitized adapter errors for an expired grant, denied scope, provider delay, or unsupported data type.
5. Confirm `GoogleHealthEnabled = $true` for background collection.

Do not label a last synchronized Fitbit value as live. Fitbit and Google Health remain subject to their own mobile-app and cloud synchronization cadence.

## Dashboard does not open

### Through the private hostname

1. Confirm the user completes the newest Cloudflare Access one-time code only once.
2. Refresh the page after successful Access authentication.
3. Confirm the Cloudflare Tunnel task and process are running.
4. Confirm the Access application still allows the intended identity.
5. Confirm the tunnel routes only to `http://127.0.0.1:8766`.

### On the Windows host

1. Check `%LOCALAPPDATA%\PersonalStateMCP\production-health.json`.
2. Confirm the dashboard scheduled task is running.
3. Confirm `http://127.0.0.1:8766/api/health` responds locally.
4. Confirm the dashboard is bound only to loopback.
5. Confirm the configured allowed Host values include the protected public hostname.

## Dashboard access code says it was already used

One-time codes are single-use and may be invalidated by an earlier tab or automatic submission.

1. Close duplicate sign-in tabs.
2. Request a new code.
3. Use only the newest message.
4. Submit it once in the same browser session.
5. Refresh the protected hostname after successful sign-in.

Do not disable Access to work around sign-in friction.

## Companion appears unpaired after an update

1. Stop before uninstalling.
2. Confirm the update APK has the same package name and signing certificate.
3. Confirm installation was performed as an in-place update.
4. Check whether Android cleared app data or whether the previous install was removed.
5. If pairing is truly gone, rotate the old device credentials before creating a replacement pairing file.
6. Delete the plaintext replacement file immediately after import.

Connected-device test setup can clear encrypted pairing and Health Connect grants. Do not run it against the production companion application.

## Data exists but source attribution is unclear

Use the exact attribution state. Do not upgrade `samsung_health_unattributed` to `watch_confirmed` without positive device evidence. Manual, phone, and external-device records must remain distinguishable.

## Service repeatedly stops

1. Review the production watchdog status and scheduled-task state.
2. Confirm the signed-in Windows session remains active because the current tasks use an interactive logon.
3. Check disk space, database integrity, Python environment, and tunnel process.
4. Confirm OS updates did not change permissions, startup policy, or network profile.
5. Record the sanitized error code and timestamp before restarting.
6. Restore one component at a time and verify data freshness after each step.

## Escalation record

For a persistent incident, record:

- Symptom and first observed time.
- Last known good measurement time for each stream.
- Phone and watch application versions.
- Which boundary stopped advancing.
- Sanitized error code and service state.
- Actions taken and whether they changed behavior.
- Verification duration after recovery.

Do not include credentials, device secrets, access codes, private IP addresses, or raw health payloads.
