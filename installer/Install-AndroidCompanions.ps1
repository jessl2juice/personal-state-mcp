param(
    [string]$AdbPath = "",
    [string]$PhoneSerial = "",
    [string]$WatchAddress = "",
    [string]$WatchPairAddress = "",
    [string]$WatchPairCode = ""
)

$ErrorActionPreference = "Stop"
$releaseRoot = if (Test-Path -LiteralPath (Join-Path $PSScriptRoot "android")) {
    $PSScriptRoot
} else {
    Split-Path -Parent $PSScriptRoot
}
$phoneApk = Join-Path $releaseRoot "android\personal-state-phone.apk"
$watchApk = Join-Path $releaseRoot "android\personal-state-watch.apk"
$toolsRoot = Join-Path $env:LOCALAPPDATA "PersonalStateMCP\tools"

if (-not (Test-Path -LiteralPath $phoneApk) -or -not (Test-Path -LiteralPath $watchApk)) {
    throw "This setup package does not contain both the phone and watch apps."
}

function Find-Adb {
    if ($AdbPath -and (Test-Path -LiteralPath $AdbPath)) { return $AdbPath }
    $command = Get-Command adb.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidates = @(
        "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe",
        "$toolsRoot\platform-tools\adb.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }

    $answer = Read-Host "Google Android Platform Tools are required. Download them from Google now? (Y/N)"
    if ($answer -notmatch "^[Yy]") { throw "Android companion setup was cancelled." }
    New-Item -ItemType Directory -Path $toolsRoot -Force | Out-Null
    $zipPath = Join-Path $toolsRoot "platform-tools.zip"
    Invoke-WebRequest -Uri "https://dl.google.com/android/repository/platform-tools-latest-windows.zip" -OutFile $zipPath
    Expand-Archive -LiteralPath $zipPath -DestinationPath $toolsRoot -Force
    Remove-Item -LiteralPath $zipPath -Force
    return "$toolsRoot\platform-tools\adb.exe"
}

function Get-ConnectedDevices([string]$Adb) {
    $lines = & $Adb devices
    return @($lines | Select-Object -Skip 1 | Where-Object { $_ -match "\sdevice$" } | ForEach-Object { ($_ -split "\s+")[0] })
}

$adb = Find-Adb
& $adb start-server | Out-Null

Write-Host "`nConnect the phone by USB and allow USB debugging." -ForegroundColor Cyan
if (-not $PhoneSerial) {
    $devices = Get-ConnectedDevices $adb
    if ($devices.Count -eq 1) {
        $PhoneSerial = $devices[0]
    } else {
        $PhoneSerial = Read-Host "Phone device ID shown by 'adb devices'"
    }
}
if (-not $PhoneSerial) { throw "No phone was selected." }

Write-Host "Installing Personal State on the phone..."
& $adb -s $PhoneSerial install -r $phoneApk
if ($LASTEXITCODE -ne 0) { throw "The phone app could not be installed." }

Write-Host "`nOn the watch, open Settings > Developer options > Wireless debugging." -ForegroundColor Cyan
if (-not $WatchPairAddress) {
    $WatchPairAddress = Read-Host "Watch pairing address (IP:pairing-port), or press Enter if already paired"
}
if ($WatchPairAddress) {
    if (-not $WatchPairCode) { $WatchPairCode = Read-Host "Six-digit watch pairing code" }
    & $adb pair $WatchPairAddress $WatchPairCode
    if ($LASTEXITCODE -ne 0) { throw "The watch could not be paired." }
}
if (-not $WatchAddress) {
    $WatchAddress = Read-Host "Watch debugging address (IP:debugging-port)"
}
if (-not $WatchAddress) { throw "No watch debugging address was supplied." }
& $adb connect $WatchAddress
if ($LASTEXITCODE -ne 0) { throw "The watch could not be connected." }

Write-Host "Installing Personal State on the watch..."
& $adb -s $WatchAddress install -r $watchApk
if ($LASTEXITCODE -ne 0) { throw "The watch app could not be installed." }

Write-Host "`nBoth companion apps are installed." -ForegroundColor Green
Write-Host "Open Personal State on the phone, pair it with the dashboard, and approve Health Connect."
Write-Host "Open Personal State on the watch once and approve heart-rate permissions."
