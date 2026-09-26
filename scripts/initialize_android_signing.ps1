param(
    [string]$SigningRoot = "$env:LOCALAPPDATA\PersonalStateMCP\release-signing",
    [string]$KeytoolPath = ""
)

$ErrorActionPreference = "Stop"
$keystore = Join-Path $SigningRoot "personal-state-release.jks"
$passwordFile = Join-Path $SigningRoot "keystore-password.dat"
$aliasFile = Join-Path $SigningRoot "keystore-alias.txt"
$alias = "personal-state"

if ((Test-Path -LiteralPath $keystore) -and (Test-Path -LiteralPath $passwordFile)) {
    Write-Host "Android release signing already exists at $SigningRoot"
    exit 0
}

if (-not $KeytoolPath) {
    $keytool = Get-Command keytool.exe -ErrorAction SilentlyContinue
    if ($keytool) { $KeytoolPath = $keytool.Source }
}
if (-not $KeytoolPath -or -not (Test-Path -LiteralPath $KeytoolPath)) {
    throw "keytool.exe was not found. Install JDK 17 or pass -KeytoolPath."
}

New-Item -ItemType Directory -Path $SigningRoot -Force | Out-Null
$bytes = New-Object byte[] 36
[Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
$password = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', 'A').Replace('/', 'B')
$env:PSM_ANDROID_KEY_PASSWORD = $password

try {
    & $KeytoolPath -genkeypair -v -keystore $keystore -alias $alias -keyalg RSA -keysize 4096 -validity 10000 -dname "CN=Personal State Release, O=Personal State, C=US" -storepass:env PSM_ANDROID_KEY_PASSWORD -keypass:env PSM_ANDROID_KEY_PASSWORD
    if ($LASTEXITCODE -ne 0) { throw "Android release signing key creation failed." }
    ConvertTo-SecureString $password -AsPlainText -Force | ConvertFrom-SecureString | Set-Content -LiteralPath $passwordFile -Encoding ascii
    $alias | Set-Content -LiteralPath $aliasFile -Encoding ascii
}
finally {
    Remove-Item Env:PSM_ANDROID_KEY_PASSWORD -ErrorAction SilentlyContinue
}

Write-Host "Created the Android release signing identity at $SigningRoot"
Write-Warning "Back up this directory securely. Losing it prevents compatible app updates."
