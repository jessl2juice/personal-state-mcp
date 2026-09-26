param(
    [Parameter(Mandatory = $true)]
    [string]$SamsungSdkAar
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$aar = (Resolve-Path -LiteralPath $SamsungSdkAar).Path
if ([IO.Path]::GetExtension($aar) -ne ".aar") {
    throw "SamsungSdkAar must point to the licensed Samsung Health Data SDK AAR."
}

$trackedAars = @(& git -c "safe.directory=$projectRoot" -C $projectRoot ls-files "*.aar")
if ($LASTEXITCODE -ne 0) { throw "Could not inspect tracked files." }
if ($trackedAars.Count) { throw "A proprietary AAR is tracked by Git: $($trackedAars -join ', ')" }

$previousAar = $env:SAMSUNG_HEALTH_DATA_SDK_AAR
$env:SAMSUNG_HEALTH_DATA_SDK_AAR = $aar
try {
    $androidRoot = Join-Path $projectRoot "android\health-connect-companion"
    Push-Location $androidRoot
    try {
        & .\gradlew.bat --no-daemon :app:testDebugUnitTest :app:lintRelease :app:assembleRelease
        if ($LASTEXITCODE -ne 0) { throw "Samsung-enabled Android verification build failed." }
    }
    finally {
        Pop-Location
    }

    $apk = Get-ChildItem -LiteralPath (Join-Path $androidRoot "app\build\outputs\apk\release") -Filter "*.apk" |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1 -ExpandProperty FullName
    if (-not $apk) { throw "Samsung-enabled release APK was not created." }
    $analyzer = Join-Path $env:ANDROID_HOME "cmdline-tools\latest\bin\apkanalyzer.bat"
    if (-not (Test-Path -LiteralPath $analyzer)) {
        $analyzer = (Get-Command apkanalyzer.bat -ErrorAction Stop).Source
    }
    $mapping = Join-Path $androidRoot "app\build\outputs\mapping\release"
    $packages = & $analyzer dex packages --defined-only --proguard-folder $mapping $apk
    if ($LASTEXITCODE -ne 0) { throw "Could not inspect the minified release APK." }
    if (($packages -join "`n") -notmatch "SamsungHealthDataSdkAdapter") {
        throw "The optional Samsung reader was removed from the minified release APK."
    }
    Write-Host "Samsung-enabled debug tests, release lint, minified build, and class-retention checks passed."
}
finally {
    if ($null -ne $previousAar) {
        $env:SAMSUNG_HEALTH_DATA_SDK_AAR = $previousAar
    } else {
        Remove-Item Env:SAMSUNG_HEALTH_DATA_SDK_AAR -ErrorAction SilentlyContinue
    }
}
