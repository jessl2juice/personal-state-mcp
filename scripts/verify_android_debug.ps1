param(
    [string]$Aapt2Path = "",
    [switch]$NoAapt2Override
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$androidRoot = Join-Path $projectRoot "android\health-connect-companion"
$gradleHome = Join-Path $androidRoot ".gradle"
$androidUserHome = Join-Path $androidRoot ".android-user-home"

New-Item -ItemType Directory -Path $gradleHome -Force | Out-Null
New-Item -ItemType Directory -Path $androidUserHome -Force | Out-Null

$previousGradleUserHome = $env:GRADLE_USER_HOME
$previousAndroidUserHome = $env:ANDROID_USER_HOME
$previousAndroidSdkHome = $env:ANDROID_SDK_HOME
$previousAndroidPrefsRoot = $env:ANDROID_PREFS_ROOT

function Get-AndroidSdkDir {
    $localProperties = Join-Path $androidRoot "local.properties"
    if (-not (Test-Path -LiteralPath $localProperties)) {
        return $env:ANDROID_HOME
    }
    $sdkLine = Get-Content -LiteralPath $localProperties |
        Where-Object { $_ -match '^sdk\.dir=' } |
        Select-Object -First 1
    if (-not $sdkLine) { return $env:ANDROID_HOME }
    return ($sdkLine -replace '^sdk\.dir=', '').Replace('\\', '\')
}

function Resolve-Aapt2 {
    if ($Aapt2Path) {
        return (Resolve-Path -LiteralPath $Aapt2Path).Path
    }
    $sdkDir = Get-AndroidSdkDir
    if (-not $sdkDir) { return "" }
    $buildTools = Join-Path $sdkDir "build-tools"
    if (-not (Test-Path -LiteralPath $buildTools)) { return "" }
    $candidate = Get-ChildItem -LiteralPath $buildTools -Directory |
        Sort-Object Name -Descending |
        ForEach-Object { Join-Path $_.FullName "aapt2.exe" } |
        Where-Object { Test-Path -LiteralPath $_ } |
        Select-Object -First 1
    return $candidate
}

try {
    $env:GRADLE_USER_HOME = $gradleHome
    $env:ANDROID_USER_HOME = $androidUserHome
    Remove-Item Env:ANDROID_SDK_HOME -ErrorAction SilentlyContinue
    Remove-Item Env:ANDROID_PREFS_ROOT -ErrorAction SilentlyContinue

    $gradleArgs = @(
        "--no-daemon",
        ":app:testDebugUnitTest",
        ":app:lintDebug",
        ":wear:lintDebug",
        ":app:assembleDebug",
        ":wear:assembleDebug"
    )
    if (-not $NoAapt2Override) {
        $resolvedAapt2 = Resolve-Aapt2
        if (-not $resolvedAapt2) {
            throw "Could not find aapt2.exe. Pass -Aapt2Path or use -NoAapt2Override."
        }
        $gradleArgs = @("-Pandroid.aapt2FromMavenOverride=$resolvedAapt2") + $gradleArgs
        Write-Host "Using AAPT2 override: $resolvedAapt2"
    }

    Push-Location $androidRoot
    try {
        & .\gradlew.bat @gradleArgs
        if ($LASTEXITCODE -ne 0) { throw "Android debug verification failed." }
    }
    finally {
        Pop-Location
    }

    $phoneApk = Join-Path $androidRoot "app\build\outputs\apk\debug\app-debug.apk"
    $wearApk = Join-Path $androidRoot "wear\build\outputs\apk\debug\wear-debug.apk"
    if (-not (Test-Path -LiteralPath $phoneApk)) { throw "Phone debug APK was not created." }
    if (-not (Test-Path -LiteralPath $wearApk)) { throw "Wear debug APK was not created." }

    Write-Host "Android debug verification passed."
    Write-Host "Phone APK: $phoneApk"
    Write-Host "Wear APK: $wearApk"
}
finally {
    if ($null -ne $previousGradleUserHome) { $env:GRADLE_USER_HOME = $previousGradleUserHome } else { Remove-Item Env:GRADLE_USER_HOME -ErrorAction SilentlyContinue }
    if ($null -ne $previousAndroidUserHome) { $env:ANDROID_USER_HOME = $previousAndroidUserHome } else { Remove-Item Env:ANDROID_USER_HOME -ErrorAction SilentlyContinue }
    if ($null -ne $previousAndroidSdkHome) { $env:ANDROID_SDK_HOME = $previousAndroidSdkHome } else { Remove-Item Env:ANDROID_SDK_HOME -ErrorAction SilentlyContinue }
    if ($null -ne $previousAndroidPrefsRoot) { $env:ANDROID_PREFS_ROOT = $previousAndroidPrefsRoot } else { Remove-Item Env:ANDROID_PREFS_ROOT -ErrorAction SilentlyContinue }
}
