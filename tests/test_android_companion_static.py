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


def test_phone_companion_exposes_fitbit_ble_without_location_permission() -> None:
    manifest = (ANDROID_MAIN / "AndroidManifest.xml").read_text(encoding="utf-8")
    layout = (ANDROID_MAIN / "res" / "layout" / "activity_main.xml").read_text(encoding="utf-8")
    service = (
        ANDROID_MAIN
        / "java"
        / "ai"
        / "clinicianassist"
        / "personalstate"
        / "FitbitBleHeartService.kt"
    ).read_text(encoding="utf-8")

    assert "android.permission.BLUETOOTH_SCAN" in manifest
    assert 'android:usesPermissionFlags="neverForLocation"' in manifest
    assert "android.permission.BLUETOOTH_CONNECT" in manifest
    assert "ACCESS_FINE_LOCATION" not in manifest
    assert "ACCESS_COARSE_LOCATION" not in manifest
    assert 'android:id="@+id/fitbitBleStartButton"' in layout
    assert 'android:id="@+id/fitbitBleStopButton"' in layout
    assert 'const val FITBIT_BLE_SOURCE_PACKAGE = "bluetooth.le.heart_rate_service"' in service
    assert "Direct Bluetooth LE Heart Rate Service sample received." in service
    assert "adapter.bondedDevices" in service
    assert "Connecting to bonded Fitbit Bluetooth device" in service
    assert "Share heart rate" in service
    assert "No standard Fitbit heart-rate characteristic found; Share heart rate mode may be off." in service
    assert '"sensor_contact"' not in service
    assert '"rr_intervals_ms"' not in service
    assert '"energy_expended_kj"' not in service


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
