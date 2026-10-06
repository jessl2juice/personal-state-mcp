$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "load_settings.ps1")
Import-PersonalStateSettings | Out-Null
Set-PersonalStateSourcePath -ProjectRoot $projectRoot

$python = Resolve-PersonalStatePython -ProjectRoot $projectRoot
$seconds = if ($env:PERSONAL_STATE_FITBIT_BLE_SECONDS) { [double]$env:PERSONAL_STATE_FITBIT_BLE_SECONDS } else { 86400 }
$arguments = @("-m", "personal_state_mcp.cli", "fitbit-ble-probe", "--seconds", ([string]$seconds))

if ($env:PERSONAL_STATE_FITBIT_BLE_ADDRESS) {
    $arguments += @("--address", $env:PERSONAL_STATE_FITBIT_BLE_ADDRESS)
}
if ($env:PERSONAL_STATE_FITBIT_BLE_NAME) {
    $arguments += @("--name", $env:PERSONAL_STATE_FITBIT_BLE_NAME)
}

& $python @arguments
