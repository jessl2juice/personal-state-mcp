param(
    [string]$CloudflaredPath = "$env:LOCALAPPDATA\PersonalStateMCP\cloudflared.exe",
    [string]$TokenPath = "$env:LOCALAPPDATA\PersonalStateMCP\cloudflare-tunnel-token.dat"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $CloudflaredPath)) {
    throw "cloudflared was not found at $CloudflaredPath"
}

if (-not (Test-Path -LiteralPath $TokenPath)) {
    throw "The encrypted Cloudflare tunnel token was not found at $TokenPath"
}

$secureToken = Get-Content -LiteralPath $TokenPath -Raw | ConvertTo-SecureString
$tokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)

try {
    $env:TUNNEL_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer)
    & $CloudflaredPath tunnel --no-autoupdate run
    exit $LASTEXITCODE
}
finally {
    Remove-Item Env:TUNNEL_TOKEN -ErrorAction SilentlyContinue
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer)
}
