param(
    [string]$ConfigPath = "$env:LOCALAPPDATA\PersonalStateMCP\settings.psd1",
    [int]$Port = 8766
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "load_settings.ps1")
Import-PersonalStateSettings -ConfigPath $ConfigPath | Out-Null
$python = Resolve-PersonalStatePython -ProjectRoot $projectRoot

Set-Location $projectRoot
& $python -m personal_state_mcp.dashboard --host 127.0.0.1 --port $Port
