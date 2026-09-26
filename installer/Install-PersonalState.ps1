param(
    [string]$InstallRoot = "$env:LOCALAPPDATA\Programs\PersonalState",
    [string]$DataRoot = "$env:LOCALAPPDATA\PersonalStateMCP",
    [string]$PythonPath = "",
    [string]$LibreEmail = "",
    [string]$SourceTimezone = "",
    [switch]$SkipCredentialPrompt,
    [switch]$SkipScheduledTasks,
    [switch]$SkipShortcut,
    [switch]$SkipAndroidCompanions,
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"
$releaseRoot = if (Test-Path -LiteralPath (Join-Path $PSScriptRoot "wheelhouse")) {
    $PSScriptRoot
} else {
    Split-Path -Parent $PSScriptRoot
}
$wheelhouse = Join-Path $releaseRoot "wheelhouse"
$scriptsSource = Join-Path $releaseRoot "scripts"
$androidSource = Join-Path $releaseRoot "android"
$settingsPath = Join-Path $DataRoot "settings.psd1"
$requestedLibreEmail = $LibreEmail

function Write-Step([string]$Message) {
    Write-Host "`n== $Message" -ForegroundColor Cyan
}

function Find-Python {
    $candidates = @()
    if ($PythonPath -and (Test-Path -LiteralPath $PythonPath)) {
        $candidates += ,@($PythonPath)
    }
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        $candidates += ,@($launcher.Source, "-3.12")
    }
    $localPython = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
    if (Test-Path -LiteralPath $localPython) { $candidates += ,@($localPython) }
    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python -and $python.Source -notlike "*\WindowsApps\python.exe") {
        $candidates += ,@($python.Source)
    }

    foreach ($candidate in $candidates) {
        $exe = $candidate[0]
        $prefix = @($candidate | Select-Object -Skip 1)
        $pythonInfo = & $exe @prefix -c "import struct, sys; print(f'{sys.version_info.major}.{sys.version_info.minor}|{struct.calcsize(chr(80))*8}')" 2>$null
        $parts = [string]$pythonInfo -split "\|"
        if ($LASTEXITCODE -eq 0 -and [version]$parts[0] -eq [version]"3.12" -and $parts[1] -eq "64") {
            return @{ Exe = $exe; Prefix = $prefix }
        }
    }
    throw "Python 3.12 (64-bit) was not found."
}

function Install-PythonIfApproved {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "Install 64-bit Python 3.12 from https://www.python.org/downloads/windows/ and run setup again."
    }
    $answer = Read-Host "Personal State needs 64-bit Python 3.12. Install it now with Windows Package Manager? (Y/N)"
    if ($answer -notmatch "^[Yy]") {
        throw "Python installation was declined. Install 64-bit Python 3.12 and run setup again."
    }
    & $winget.Source install --id Python.Python.3.12 --exact --scope user --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw "Windows Package Manager could not install Python 3.12." }
}

function Escape-Psd1([string]$Value) {
    return $Value.Replace("'", "''")
}

function Format-Psd1Array($Values) {
    if ($null -eq $Values) { return "@()" }
    $quoted = @($Values) | ForEach-Object { "'$(Escape-Psd1 ([string]$_))'" }
    return "@($($quoted -join ', '))"
}

function Register-BackgroundTask([string]$Name, [string]$ScriptPath) {
    $arguments = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$ScriptPath`""
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments -WorkingDirectory $InstallRoot
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
    Register-ScheduledTask -TaskName $Name -Description "Personal State private local service." -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
}

if (-not (Test-Path -LiteralPath $wheelhouse)) {
    throw "This setup package is incomplete: wheelhouse was not found."
}
$projectWheel = Get-ChildItem -LiteralPath $wheelhouse -Filter "personal_state_mcp-*.whl" | Sort-Object Name -Descending | Select-Object -First 1
if (-not $projectWheel) { throw "This setup package is incomplete: the Personal State package was not found." }

Write-Step "Checking this computer"
try {
    $python = Find-Python
}
catch {
    Install-PythonIfApproved
    $python = Find-Python
}
New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
New-Item -ItemType Directory -Path $DataRoot -Force | Out-Null
$existingSettings = @{}
if (Test-Path -LiteralPath $settingsPath) {
    $existingSettings = Import-PowerShellDataFile -LiteralPath $settingsPath
}

Write-Step "Installing Personal State"
$venvPath = Join-Path $InstallRoot "venv"
& $python.Exe @($python.Prefix) -m venv $venvPath
if ($LASTEXITCODE -ne 0) { throw "Python could not create the private Personal State environment." }
$venvPython = Join-Path $venvPath "Scripts\python.exe"
& $venvPython -m pip install --disable-pip-version-check --no-index --find-links $wheelhouse "personal-state-mcp[mcp,keychain]==0.2.0"
if ($LASTEXITCODE -ne 0) { throw "Personal State packages could not be installed." }

Copy-Item -LiteralPath $scriptsSource -Destination (Join-Path $InstallRoot "scripts") -Recurse -Force
if (Test-Path -LiteralPath $androidSource) {
    Copy-Item -LiteralPath $androidSource -Destination (Join-Path $InstallRoot "android") -Recurse -Force
}

$existingLibreEmail = if ($existingSettings.LibreEmail) { [string]$existingSettings.LibreEmail } else { "" }
$credentialChanged = $requestedLibreEmail -and $requestedLibreEmail -ne $existingLibreEmail
if (-not $LibreEmail -and $existingLibreEmail) {
    $LibreEmail = [string]$existingSettings.LibreEmail
}
if (-not $LibreEmail -and -not $SkipCredentialPrompt) {
    $LibreEmail = Read-Host "Dedicated LibreLinkUp follower email"
    $credentialChanged = [bool]$LibreEmail
}
if (-not $SourceTimezone) {
    $SourceTimezone = if ($existingSettings.SourceTimezone) { [string]$existingSettings.SourceTimezone } else { "America/Los_Angeles" }
}
$phoneApk = Join-Path $InstallRoot "android\personal-state-phone.apk"
$hostId = if ($existingSettings.HostId) { [string]$existingSettings.HostId } else { "personal-state-local" }
$allowedHosts = if ($existingSettings.AllowedHosts) { @($existingSettings.AllowedHosts) } else { @("personal-state-local") }
$threshold = if ($existingSettings.GlucoseThresholdMgDl) { [int]$existingSettings.GlucoseThresholdMgDl } else { 80 }
$dashboardHosts = @($existingSettings.DashboardAllowedHosts)
$watchEnabled = $existingSettings.WatchEnabled -eq $true
$watchHosts = @($existingSettings.WatchIngestHosts)
$watchDeviceId = if ($existingSettings.WatchDeviceId) { [string]$existingSettings.WatchDeviceId } else { "" }
$tunnelEnabled = $existingSettings.CloudflareTunnelEnabled -eq $true
$healthHost = if ($existingSettings.DashboardHealthHost) { [string]$existingSettings.DashboardHealthHost } else { "localhost" }
$watchEnabledLiteral = if ($watchEnabled) { "`$true" } else { "`$false" }
$tunnelEnabledLiteral = if ($tunnelEnabled) { "`$true" } else { "`$false" }
$settings = @"
@{
    DataDirectory = '$(Escape-Psd1 $DataRoot)'
    LibreEmail = '$(Escape-Psd1 $LibreEmail)'
    HostId = '$(Escape-Psd1 $hostId)'
    AllowedHosts = $(Format-Psd1Array $allowedHosts)
    SourceTimezone = '$(Escape-Psd1 $SourceTimezone)'
    GlucoseThresholdMgDl = $threshold
    DashboardAllowedHosts = $(Format-Psd1Array $dashboardHosts)
    CompanionApkPath = '$(Escape-Psd1 $phoneApk)'
    WatchEnabled = $watchEnabledLiteral
    WatchIngestHosts = $(Format-Psd1Array $watchHosts)
    WatchDeviceId = '$(Escape-Psd1 $watchDeviceId)'
    CloudflareTunnelEnabled = $tunnelEnabledLiteral
    DashboardHealthHost = '$(Escape-Psd1 $healthHost)'
}
"@
$settings | Set-Content -LiteralPath $settingsPath -Encoding utf8

if ($LibreEmail -and $credentialChanged -and -not $SkipCredentialPrompt) {
    Write-Step "Protecting the LibreLinkUp credential"
    $env:LIBRELINKUP_EMAIL = $LibreEmail
    & $venvPython -m personal_state_mcp.cli set-libre-password $LibreEmail
    if ($LASTEXITCODE -ne 0) { throw "The LibreLinkUp password was not stored." }
}

if (-not $SkipScheduledTasks) {
    Write-Step "Starting private background services"
    $dashboardScript = Join-Path $InstallRoot "scripts\start_dashboard.ps1"
    $collectorScript = Join-Path $InstallRoot "scripts\start_collector.ps1"
    try {
        Register-BackgroundTask "Personal State MCP Dashboard" $dashboardScript
        Register-BackgroundTask "Personal State MCP Collector" $collectorScript
        Start-ScheduledTask -TaskName "Personal State MCP Dashboard"
        Start-ScheduledTask -TaskName "Personal State MCP Collector"
    }
    catch {
        $startup = [Environment]::GetFolderPath("Startup")
        $startupScript = Join-Path $startup "Personal State Startup.cmd"
        @"
@echo off
start "Personal State Dashboard" /min powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "$dashboardScript"
start "Personal State Collector" /min powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "$collectorScript"
"@ | Set-Content -LiteralPath $startupScript -Encoding ascii
        Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $dashboardScript)
        Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $collectorScript)
        Write-Warning "Scheduled tasks were unavailable. Personal State was added to the current user's Startup folder instead."
    }
}

if (-not $SkipShortcut) {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $shortcut = Join-Path $desktop "Personal State.url"
    @"
[InternetShortcut]
URL=http://127.0.0.1:8766/
IconFile=%SystemRoot%\System32\imageres.dll
IconIndex=15
"@ | Set-Content -LiteralPath $shortcut -Encoding ascii
}

Write-Step "Installation complete"
Write-Host "Your health history stays in $DataRoot"
Write-Host "Dashboard: http://127.0.0.1:8766/"

$androidInstaller = Join-Path $releaseRoot "Install-AndroidCompanions.ps1"
if (-not (Test-Path -LiteralPath $androidInstaller)) {
    $androidInstaller = Join-Path $releaseRoot "installer\Install-AndroidCompanions.ps1"
}

if (-not $SkipAndroidCompanions -and (Test-Path -LiteralPath $androidInstaller)) {
    Write-Step "Phone and watch setup"
    $installCompanions = Read-Host "Install the matching phone and watch apps now? [Y/n]"
    if ([string]::IsNullOrWhiteSpace($installCompanions) -or $installCompanions.Trim().ToLowerInvariant() -in @("y", "yes")) {
        & $androidInstaller
    }
    else {
        Write-Host "Companion setup skipped. Run 'Install Phone and Watch.cmd' later to install both apps together."
    }
}

if (-not $NoLaunch -and -not $SkipScheduledTasks) {
    Start-Sleep -Seconds 4
    Start-Process "http://127.0.0.1:8766/"
}
