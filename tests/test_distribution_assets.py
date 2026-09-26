from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _gradle_value(path: Path, name: str) -> str:
    match = re.search(rf"\b{name}\s*=\s*(?:\"([^\"]+)\"|(\d+))", path.read_text(encoding="utf-8"))
    assert match, f"{name} missing from {path}"
    return next(value for value in match.groups() if value is not None)


def test_phone_and_watch_are_one_play_identity_with_distinct_versions() -> None:
    phone = ROOT / "android" / "health-connect-companion" / "app" / "build.gradle.kts"
    watch = ROOT / "android" / "health-connect-companion" / "wear" / "build.gradle.kts"

    assert _gradle_value(phone, "applicationId") == "ai.clinicianassist.personalstate"
    assert _gradle_value(watch, "applicationId") == "ai.clinicianassist.personalstate"
    assert _gradle_value(phone, "versionName") == _gradle_value(watch, "versionName") == "0.2.0"
    assert _gradle_value(phone, "versionCode") != _gradle_value(watch, "versionCode")


def test_wear_build_is_non_standalone_and_watch_only() -> None:
    manifest = (
        ROOT
        / "android"
        / "health-connect-companion"
        / "wear"
        / "src"
        / "main"
        / "AndroidManifest.xml"
    ).read_text(encoding="utf-8")

    assert 'android:name="android.hardware.type.watch" android:required="true"' in manifest
    assert 'android:name="com.google.android.wearable.standalone" android:value="false"' in manifest


def test_private_artifact_types_are_ignored() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    for pattern in ("*.db", "*.jsonl", "*.apk", "*.jks", "*pairing*.json", "settings.psd1"):
        assert pattern in ignore


def test_combined_android_installer_requires_both_apps() -> None:
    installer = (ROOT / "installer" / "Install-AndroidCompanions.ps1").read_text(encoding="utf-8")

    assert "personal-state-phone.apk" in installer
    assert "personal-state-watch.apk" in installer
    assert "install -r $phoneApk" in installer
    assert "install -r $watchApk" in installer


def test_main_installer_offers_phone_and_watch_as_one_flow() -> None:
    installer = (ROOT / "installer" / "Install-PersonalState.ps1").read_text(encoding="utf-8")

    assert "SkipAndroidCompanions" in installer
    assert 'Join-Path $releaseRoot "Install-AndroidCompanions.ps1"' in installer
    assert "& $androidInstaller" in installer
