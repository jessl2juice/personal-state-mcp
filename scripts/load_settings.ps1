$ErrorActionPreference = "Stop"

function Import-PersonalStateSettings {
    param(
        [string]$ConfigPath = "$env:LOCALAPPDATA\PersonalStateMCP\settings.psd1"
    )

    $settings = @{}
    if (Test-Path -LiteralPath $ConfigPath) {
        $settings = Import-PowerShellDataFile -LiteralPath $ConfigPath
    }

    $mapping = [ordered]@{
        DataDirectory = "PERSONAL_STATE_MCP_DATA_DIR"
        LibreEmail = "LIBRELINKUP_EMAIL"
        HostId = "PERSONAL_STATE_MCP_HOST_ID"
        AllowedHosts = "PERSONAL_STATE_MCP_ALLOWED_HOSTS"
        SourceTimezone = "PERSONAL_STATE_MCP_SOURCE_TIMEZONE"
        GlucoseThresholdMgDl = "PERSONAL_STATE_MCP_GLUCOSE_THRESHOLD"
        DashboardAllowedHosts = "PERSONAL_STATE_DASHBOARD_ALLOWED_HOSTS"
        CompanionApkPath = "PERSONAL_STATE_COMPANION_APK"
        WatchEnabled = "PERSONAL_STATE_WATCH_ENABLED"
        WatchIngestHosts = "PERSONAL_STATE_WATCH_INGEST_HOSTS"
        WatchDeviceId = "PERSONAL_STATE_WATCH_DEVICE_ID"
    }

    foreach ($entry in $mapping.GetEnumerator()) {
        if (-not $settings.ContainsKey($entry.Key)) { continue }
        $value = $settings[$entry.Key]
        if ($null -eq $value -or $value -eq "") { continue }
        if ($value -is [System.Array]) { $value = $value -join "," }
        if ($value -is [bool]) { $value = $value.ToString().ToLowerInvariant() }
        Set-Item -LiteralPath "Env:$($entry.Value)" -Value ([string]$value)
    }

    return $settings
}

function Resolve-PersonalStatePython {
    param([string]$ProjectRoot)

    $candidate = Join-Path $ProjectRoot "venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $candidate) { return $candidate }

    $candidate = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $candidate) { return $candidate }

    throw "Personal State's Python environment is missing. Run the installer again to repair it."
}
