param(
    [int]$Port = 8891,
    [string]$ListenHost = "127.0.0.1",
    [string]$AllowedHosts = "",
    [string]$CaptureDir = "",
    [switch]$Background
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Addon = Join-Path $Root "smart_proxy\mitm_token_capture_addon.py"

if (-not (Test-Path -LiteralPath $Addon)) {
    throw "addon not found: $Addon"
}

$Python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $Python) {
    throw "python.exe not found."
}

try {
    & $Python -c "import mitmproxy.tools.main" 2>$null
}
catch {
    throw "mitmproxy Python package not found for $Python. Install mitmproxy first."
}

$Existing = Get-NetTCPConnection `
    -LocalAddress $ListenHost `
    -LocalPort $Port `
    -State Listen `
    -ErrorAction SilentlyContinue
if ($Existing) {
    throw "port $ListenHost`:$Port already listening by PID $($Existing.OwningProcess)"
}

if ($AllowedHosts) {
    $env:SMART_PROXY_MITM_ALLOWED_HOSTS = $AllowedHosts
}
if ($CaptureDir) {
    $env:SMART_PROXY_TOKEN_CAPTURE_DIR = $CaptureDir
}

$Cert = Join-Path $env:USERPROFILE ".mitmproxy\mitmproxy-ca-cert.cer"
Write-Host "[mitm-token] python  : $Python"
Write-Host "[mitm-token] addon   : $Addon"
Write-Host "[mitm-token] listen  : $ListenHost`:$Port"
Write-Host "[mitm-token] output  : $(if ($CaptureDir) { $CaptureDir } else { Join-Path $Root 'logs' })"
Write-Host "[mitm-token] CA cert : $Cert"
Write-Host "[mitm-token] note    : trust the CA manually before HTTPS clients can be decrypted."

$MitmCode = "from mitmproxy.tools.main import mitmdump; mitmdump()"
$Args = @(
    "-c", $MitmCode,
    "-s", $Addon,
    "--listen-host", $ListenHost,
    "--listen-port", "$Port",
    "--set", "connection_strategy=lazy"
)
$BackgroundArgumentLine = (
    '-c "{0}" -s "{1}" --listen-host "{2}" --listen-port {3} --set connection_strategy=lazy' -f
    $MitmCode,
    $Addon,
    $ListenHost,
    $Port
)

if ($Background) {
    $LogDir = Join-Path $Root "logs"
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    $OutLog = Join-Path $LogDir "mitm-token-capture.out.log"
    $ErrLog = Join-Path $LogDir "mitm-token-capture.err.log"
    $Process = Start-Process `
        -FilePath $Python `
        -ArgumentList $BackgroundArgumentLine `
        -WorkingDirectory $Root `
        -RedirectStandardOutput $OutLog `
        -RedirectStandardError $ErrLog `
        -WindowStyle Hidden `
        -PassThru
    Write-Host "[mitm-token] started PID $($Process.Id)"
    Write-Host "[mitm-token] stdout : $OutLog"
    Write-Host "[mitm-token] stderr : $ErrLog"
    return
}

& $Python @Args
