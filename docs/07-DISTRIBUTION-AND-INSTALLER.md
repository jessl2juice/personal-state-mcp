# Distribution and Installer

Document version: 1.0
Last verified: 2026-09-29
Intended readers: release engineers, installers, and operators

## Release model

Personal State 0.4.2 ships as a versioned Windows ZIP. It contains an offline Python wheelhouse, signed Android APKs, Google Play app bundles, checksums, user-facing setup launchers, and the runtime scripts used by the scheduled tasks.

The release package must never contain a health database, history export, pairing file, credential, tunnel token, signing key, device screenshot, or machine-specific settings file.

## Windows installation contract

The installer is current-user only and does not require administrator privileges. It:

1. verifies 64-bit Python 3.12 and offers a user-scoped Windows Package Manager installation when it is missing;
2. creates an isolated virtual environment under `%LOCALAPPDATA%\Programs\PersonalState`;
3. installs from the bundled offline wheelhouse;
4. writes non-secret settings under `%LOCALAPPDATA%\PersonalStateMCP`;
5. stores the Libre password in Windows Credential Manager;
6. registers dashboard and collector tasks at sign-in;
7. creates a local dashboard shortcut;
8. preserves data and credentials on upgrade;
9. offers the combined phone-and-watch installation immediately, unless explicitly skipped with `-SkipAndroidCompanions`.

The installer derives the Python package version from the bundled Personal State wheel, so the package installed from a correctly assembled release cannot silently drift from the ZIP version.

Uninstall preserves data by default. Destructive removal requires the explicit `-DeleteData` switch and deletes configured credentials before removing the application environment.

## Android distribution contract

The phone and Wear OS apps have:

- the same package name: `ai.clinicianassist.personalstate`;
- the same release signing identity;
- different version codes across form factors;
- a non-standalone Wear OS declaration because the watch relay depends on the phone app;
- one guided Windows setup that installs both APKs in sequence.
- a first-run phone flow that requests full-history and background access, then imports all records exposed by Health Connect and Samsung Health after explicit user approval.

For Google Play, upload the phone and watch bundles to the same app listing and enable the Wear OS form factor. Google Play distributes the appropriate build to each device. Android and Wear OS still require user confirmation; silent watch installation from the phone APK is neither supported nor appropriate.

## Signing key custody

`scripts/initialize_android_signing.ps1` creates a release keystore outside the repository under the current user's local application data. Its random password is encrypted with Windows DPAPI. Back up the complete signing directory to a protected recovery location. Never commit it or place it in a release ZIP.

CI releases should inject the keystore and passwords through protected repository secrets. A release built with a different signing identity cannot update an existing phone or watch installation.

## Build and verify

Run:

```powershell
.\scripts\build_release.ps1
```

The release build runs Python tests, builds all dependency wheels, runs Android unit tests and release lint, creates signed APK and AAB artifacts, computes SHA-256 checksums, and creates `release\Personal-State-0.4.2-Windows.zip`.

Before publication:

1. extract the ZIP to a clean path;
2. compare files with `SHA256SUMS.txt`;
3. install on a clean Windows user profile;
4. verify dashboard and collector tasks;
5. verify password storage and a Libre collection;
6. install both Android companions with the combined setup;
7. verify the APK signatures match;
8. verify live heart rate is withheld after ten seconds without a fresh sample;
9. uninstall and confirm history is retained;
10. repeat with destructive removal in a disposable profile.
