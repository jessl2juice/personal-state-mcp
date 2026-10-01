from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[1]
ANDROID_MAIN = ROOT / "android" / "health-connect-companion" / "app" / "src" / "main"


def test_default_health_connect_button_requests_core_permissions_only() -> None:
    main_activity = (
        ANDROID_MAIN
        / "java"
        / "ai"
        / "clinicianassist"
        / "personalstate"
        / "MainActivity.kt"
    ).read_text(encoding="utf-8")

    assert "binding.permissionButton.setOnClickListener" in main_activity
    assert "permissionRequest.launch(HealthConnectSync.CORE_READ_PERMISSIONS)" in main_activity
    assert "binding.optionalPermissionButton.setOnClickListener" in main_activity
    assert "permissionRequest.launch(HealthConnectSync.OPTIONAL_READ_PERMISSIONS)" in main_activity
    assert "permissionRequest.launch(HealthConnectSync.READ_PERMISSIONS)" not in main_activity


def test_sensitive_health_connect_categories_are_optional() -> None:
    sync_source = (
        ANDROID_MAIN
        / "java"
        / "ai"
        / "clinicianassist"
        / "personalstate"
        / "HealthConnectSync.kt"
    ).read_text(encoding="utf-8")
    core_block = sync_source.split("val CORE_READ_PERMISSIONS", 1)[1].split("val OPTIONAL_READ_PERMISSIONS", 1)[0]
    optional_block = sync_source.split("val OPTIONAL_READ_PERMISSIONS", 1)[1].split("val READ_PERMISSIONS", 1)[0]

    assert "HeartRateRecord::class" in core_block
    assert "SleepSessionRecord::class" in core_block
    assert "BloodPressureRecord::class" not in core_block
    assert "BloodGlucoseRecord::class" not in core_block
    assert "NutritionRecord::class" not in core_block

    assert "BloodPressureRecord::class" in optional_block
    assert "BloodGlucoseRecord::class" in optional_block
    assert "NutritionRecord::class" in optional_block


def test_android_layout_exposes_optional_permission_button() -> None:
    layout = (ANDROID_MAIN / "res" / "layout" / "activity_main.xml").read_text(encoding="utf-8")
    strings = (ANDROID_MAIN / "res" / "values" / "strings.xml").read_text(encoding="utf-8")

    assert 'android:id="@+id/optionalPermissionButton"' in layout
    assert 'name="optional_permissions"' in strings


def test_fitbit_health_connect_origin_is_labeled_as_external_device() -> None:
    sync_source = (
        ANDROID_MAIN
        / "java"
        / "ai"
        / "clinicianassist"
        / "personalstate"
        / "HealthConnectSync.kt"
    ).read_text(encoding="utf-8")

    assert 'FITBIT_HEALTH_CONNECT_PACKAGES: Set<String> = setOf("com.fitbit.FitbitMobile", "com.fitbit.fitbitmobile")' in sync_source
    assert 'sourcePackage in FITBIT_HEALTH_CONNECT_PACKAGES -> "external_device"' in sync_source
    assert "Health Connect metadata identifies Fitbit app origin" in sync_source
