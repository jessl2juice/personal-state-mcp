param(
    [string]$Version = "0.4.2",
    [string]$PythonPath = "$PSScriptRoot\..\.venv\Scripts\python.exe",
    [string]$SigningRoot = "$env:LOCALAPPDATA\PersonalStateMCP\release-signing",
    [string]$SamsungSdkAar = "",
    [switch]$SkipAndroid,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$releaseRoot = Join-Path $projectRoot "release"
$stageRoot = Join-Path $releaseRoot "Personal-State-$Version"
$wheelhouse = Join-Path $stageRoot "wheelhouse"
$androidOutput = Join-Path $stageRoot "android"
$playOutput = Join-Path $stageRoot "play"

if (-not (Test-Path -LiteralPath $PythonPath)) { throw "Python environment not found at $PythonPath" }
if ((Split-Path -Parent $releaseRoot) -ne $projectRoot) { throw "Unexpected release path." }

if (Test-Path -LiteralPath $releaseRoot) { Remove-Item -LiteralPath $releaseRoot -Recurse -Force }
New-Item -ItemType Directory -Path $wheelhouse -Force | Out-Null
New-Item -ItemType Directory -Path $androidOutput -Force | Out-Null
New-Item -ItemType Directory -Path $playOutput -Force | Out-Null

if (-not $SkipTests) {
    & $PythonPath -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Python tests failed." }
}

& $PythonPath -m pip wheel ".[mcp,keychain]" --wheel-dir $wheelhouse
if ($LASTEXITCODE -ne 0) { throw "Python release package build failed." }

if (-not $SkipAndroid) {
    $previousSamsungSdkAar = $env:SAMSUNG_HEALTH_DATA_SDK_AAR
    if ($SamsungSdkAar) {
        $resolvedSamsungSdkAar = (Resolve-Path -LiteralPath $SamsungSdkAar).Path
        if ([IO.Path]::GetExtension($resolvedSamsungSdkAar) -ne ".aar") {
            throw "SamsungSdkAar must point to the licensed Samsung Health Data SDK AAR."
        }
        $env:SAMSUNG_HEALTH_DATA_SDK_AAR = $resolvedSamsungSdkAar
    }
    if ($env:ANDROID_USER_HOME) {
        New-Item -ItemType Directory -Path $env:ANDROID_USER_HOME -Force | Out-Null
    }
    $keystore = Join-Path $SigningRoot "personal-state-release.jks"
    $passwordFile = Join-Path $SigningRoot "keystore-password.dat"
    $aliasFile = Join-Path $SigningRoot "keystore-alias.txt"
    if (-not (Test-Path -LiteralPath $keystore) -or -not (Test-Path -LiteralPath $passwordFile)) {
        throw "Android release signing is not initialized. Run scripts\initialize_android_signing.ps1 first."
    }

    $encryptedPassword = (Get-Content -LiteralPath $passwordFile -Raw).Trim()
    $securePassword = ConvertTo-SecureString $encryptedPassword
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
    try {
        $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
        $env:PERSONAL_STATE_ANDROID_KEYSTORE = $keystore
        $env:PERSONAL_STATE_ANDROID_STORE_PASSWORD = $password
        $env:PERSONAL_STATE_ANDROID_KEY_PASSWORD = $password
        $env:PERSONAL_STATE_ANDROID_KEY_ALIAS = (Get-Content -LiteralPath $aliasFile -Raw).Trim()
        $androidRoot = Join-Path $projectRoot "android\health-connect-companion"
        Push-Location $androidRoot
        try {
            & .\gradlew.bat --no-daemon :app:testDebugUnitTest :app:lintRelease :wear:lintRelease :app:assembleRelease :wear:assembleRelease :app:bundleRelease :wear:bundleRelease
            if ($LASTEXITCODE -ne 0) { throw "Android release build failed." }
        }
        finally {
            Pop-Location
        }
    }
    finally {
        Remove-Item Env:PERSONAL_STATE_ANDROID_KEYSTORE -ErrorAction SilentlyContinue
        Remove-Item Env:PERSONAL_STATE_ANDROID_STORE_PASSWORD -ErrorAction SilentlyContinue
        Remove-Item Env:PERSONAL_STATE_ANDROID_KEY_PASSWORD -ErrorAction SilentlyContinue
        Remove-Item Env:PERSONAL_STATE_ANDROID_KEY_ALIAS -ErrorAction SilentlyContinue
        if ($null -ne $previousSamsungSdkAar) {
            $env:SAMSUNG_HEALTH_DATA_SDK_AAR = $previousSamsungSdkAar
        } else {
            Remove-Item Env:SAMSUNG_HEALTH_DATA_SDK_AAR -ErrorAction SilentlyContinue
        }
        if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
        $password = $null
    }

}

$phoneApkSource = Join-Path $projectRoot "android\health-connect-companion\app\build\outputs\apk\release\app-release.apk"
$watchApkSource = Join-Path $projectRoot "android\health-connect-companion\wear\build\outputs\apk\release\wear-release.apk"
$phoneBundleSource = Join-Path $projectRoot "android\health-connect-companion\app\build\outputs\bundle\release\app-release.aab"
$watchBundleSource = Join-Path $projectRoot "android\health-connect-companion\wear\build\outputs\bundle\release\wear-release.aab"
$androidArtifacts = @($phoneApkSource, $watchApkSource, $phoneBundleSource, $watchBundleSource)
if (@($androidArtifacts | Where-Object { -not (Test-Path -LiteralPath $_) }).Count) {
    throw "Signed Android release artifacts are missing. Run the build without -SkipAndroid."
}
Copy-Item -LiteralPath $phoneApkSource -Destination (Join-Path $androidOutput "personal-state-phone.apk")
Copy-Item -LiteralPath $watchApkSource -Destination (Join-Path $androidOutput "personal-state-watch.apk")
Copy-Item -LiteralPath $phoneBundleSource -Destination (Join-Path $playOutput "personal-state-phone.aab")
Copy-Item -LiteralPath $watchBundleSource -Destination (Join-Path $playOutput "personal-state-watch.aab")

Copy-Item -Path (Join-Path $projectRoot "installer\*") -Destination $stageRoot -Recurse -Force
Copy-Item -LiteralPath (Join-Path $projectRoot "scripts") -Destination (Join-Path $stageRoot "scripts") -Recurse -Force
Copy-Item -LiteralPath (Join-Path $projectRoot "docs\INSTALLER-QUICK-START.md") -Destination (Join-Path $stageRoot "READ ME FIRST.md")
Copy-Item -LiteralPath (Join-Path $projectRoot "LICENSE") -Destination $stageRoot

$hashes = Get-ChildItem -LiteralPath $stageRoot -File -Recurse | Sort-Object FullName | ForEach-Object {
    $relative = $_.FullName.Substring($stageRoot.Length + 1)
    $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $relative"
}
$hashes | Set-Content -LiteralPath (Join-Path $stageRoot "SHA256SUMS.txt") -Encoding ascii

$zipPath = Join-Path $releaseRoot "Personal-State-$Version-Windows.zip"
Compress-Archive -LiteralPath $stageRoot -DestinationPath $zipPath -CompressionLevel Optimal
Write-Host "Release created: $zipPath"
