param(
    [string]$InstallRoot = "$env:LOCALAPPDATA\Programs\PersonalState",
    [string]$DataRoot = "$env:LOCALAPPDATA\PersonalStateMCP",
    [switch]$DeleteData,
    [switch]$SkipTasks,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$tasks = @("Personal State MCP Dashboard", "Personal State MCP Collector", "Personal State MCP Production Watchdog")

if (-not $Yes) {
    $answer = Read-Host "Remove Personal State? Your health history will be kept unless -DeleteData is used. Type REMOVE"
    if ($answer -ne "REMOVE") { Write-Host "Nothing was removed."; exit 0 }
}

$settingsPath = Join-Path $DataRoot "settings.psd1"
$venvPython = Join-Path $InstallRoot "venv\Scripts\python.exe"
if ($DeleteData -and (Test-Path -LiteralPath $venvPython) -and (Test-Path -LiteralPath $settingsPath)) {
    . (Join-Path $InstallRoot "scripts\load_settings.ps1")
    Import-PersonalStateSettings -ConfigPath $settingsPath | Out-Null
    & $venvPython -m personal_state_mcp.cli delete-credentials --yes
}

if (-not $SkipTasks) {
    foreach ($task in $tasks) {
        Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction SilentlyContinue
    }
}

$shortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "Personal State.url"
$startupScript = Join-Path ([Environment]::GetFolderPath("Startup")) "Personal State Startup.cmd"
Remove-Item -LiteralPath $shortcut -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $startupScript -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $InstallRoot -Recurse -Force -ErrorAction SilentlyContinue
if ($DeleteData) {
    Remove-Item -LiteralPath $DataRoot -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "Personal State, local history, and configured credentials were removed."
} else {
    Write-Host "Personal State was removed. Local history remains in $DataRoot"
}
