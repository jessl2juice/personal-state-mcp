param(
    [int]$IntervalSeconds = 300,
    [switch]$Once,
    [string]$ConfigPath = "$env:LOCALAPPDATA\PersonalStateMCP\settings.psd1"
)

$ErrorActionPreference = "Continue"
$stateDirectory = Join-Path $env:LOCALAPPDATA "PersonalStateMCP"
$statePath = Join-Path $stateDirectory "production-health.json"
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "load_settings.ps1")
$settings = Import-PersonalStateSettings -ConfigPath $ConfigPath
$dashboardHealthHost = if ($settings.DashboardHealthHost) { $settings.DashboardHealthHost } else { "localhost" }
$cloudflareEnabled = $settings.CloudflareTunnelEnabled -eq $true
$dashboardTask = "Personal State MCP Dashboard"
$collectorTask = "Personal State MCP Collector"
$tunnelTask = "Personal State MCP Cloudflare Tunnel"

New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null

function Get-TaskState([string]$Name) {
    try {
        return (Get-ScheduledTask -TaskName $Name -ErrorAction Stop).State.ToString()
    }
    catch {
        return "Missing"
    }
}

function Start-TaskIfStopped([string]$Name) {
    $state = Get-TaskState $Name
    if ($state -notin @("Running", "Queued")) {
        Start-ScheduledTask -TaskName $Name -ErrorAction Stop
        return $true
    }
    return $false
}

function Test-Dashboard {
    try {
        $headers = @{ Host = $dashboardHealthHost }
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:8766/api/live" -Headers $headers -TimeoutSec 15
        return $null -ne $response.generated_at
    }
    catch {
        return $false
    }
}

do {
    $actions = [System.Collections.Generic.List[string]]::new()
    $dashboardHealthy = Test-Dashboard
    if (-not $dashboardHealthy) {
        try {
            Stop-ScheduledTask -TaskName $dashboardTask -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2
            Start-ScheduledTask -TaskName $dashboardTask -ErrorAction Stop
            $actions.Add("restarted dashboard")
            Start-Sleep -Seconds 10
            $dashboardHealthy = Test-Dashboard
        }
        catch {
            $actions.Add("dashboard restart failed")
        }
    }

    try {
        if (Start-TaskIfStopped $collectorTask) { $actions.Add("started collector") }
    }
    catch {
        $actions.Add("collector start failed")
    }

    $tunnelRunning = if ($cloudflareEnabled) {
        $null -ne (Get-Process -Name "cloudflared" -ErrorAction SilentlyContinue)
    } else {
        $false
    }
    if ($cloudflareEnabled -and -not $tunnelRunning) {
        try {
            Stop-ScheduledTask -TaskName $tunnelTask -ErrorAction SilentlyContinue
            Start-ScheduledTask -TaskName $tunnelTask -ErrorAction Stop
            $actions.Add("started tunnel")
            Start-Sleep -Seconds 5
            $tunnelRunning = $null -ne (Get-Process -Name "cloudflared" -ErrorAction SilentlyContinue)
        }
        catch {
            $actions.Add("tunnel start failed")
        }
    }

    [ordered]@{
        checked_at = (Get-Date).ToUniversalTime().ToString("o")
        dashboard_healthy = $dashboardHealthy
        dashboard_task = Get-TaskState $dashboardTask
        collector_task = Get-TaskState $collectorTask
        tunnel_task = Get-TaskState $tunnelTask
        tunnel_enabled = $cloudflareEnabled
        tunnel_process = $tunnelRunning
        actions = @($actions)
    } | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8

    if (-not $Once) { Start-Sleep -Seconds $IntervalSeconds }
} while (-not $Once)
