$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$taskNames = @(
    "Personal State MCP Collector",
    "Personal State MCP Dashboard",
    "Personal State MCP Cloudflare Tunnel"
)
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

foreach ($taskName in $taskNames) {
    Get-ScheduledTask -TaskName $taskName -ErrorAction Stop | Out-Null
    Set-ScheduledTask -TaskName $taskName -Settings $settings | Out-Null
}

$watchdogName = "Personal State MCP Production Watchdog"
$watchdogScript = Join-Path $PSScriptRoot "production_watchdog.ps1"
$watchdogAction = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$watchdogScript`"" `
    -WorkingDirectory $projectRoot
$watchdogTrigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$watchdogPrincipal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $watchdogName `
    -Description "Checks and recovers the local Personal State dashboard, collector, and Cloudflare tunnel." `
    -Action $watchdogAction `
    -Trigger $watchdogTrigger `
    -Principal $watchdogPrincipal `
    -Settings $settings `
    -Force | Out-Null

Start-ScheduledTask -TaskName $watchdogName
