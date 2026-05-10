# Smart Proxy setup script for a new Windows machine.
# Usage: powershell -ExecutionPolicy Bypass -File setup.ps1
# Requires: Python 3.8+ and Claude Code available on PATH.

param(
    [string]$SmartProxyDir = "",
    [string]$ClaudeCodeDir = "",
    [string]$PythonPath = "",
    [string]$ClaudePs1Path = "$env:USERPROFILE\claude.ps1"
)

$ErrorActionPreference = "Stop"

function Test-Python38OrNewer {
    param([string]$Candidate)

    try {
        $versionText = & $Candidate --version 2>&1
        if ($versionText -match "Python 3\.(\d+)\.(\d+)") {
            return ([int]$Matches[1] -ge 8)
        }
    } catch {
        return $false
    }

    return $false
}

function Resolve-PythonPath {
    param([string]$RequestedPath)

    if ($RequestedPath) {
        if ((Test-Path $RequestedPath) -and (Test-Python38OrNewer $RequestedPath)) {
            return (Resolve-Path $RequestedPath).Path
        }
        throw "PythonPath was provided but is not a valid Python 3.8+ executable: $RequestedPath"
    }

    $candidates = @(
        "python3",
        "python",
        "C:\Python314\python.exe",
        "C:\Python313\python.exe",
        "C:\Python312\python.exe",
        "C:\Python311\python.exe",
        "C:\Python310\python.exe",
        "C:\Python39\python.exe",
        "C:\Python38\python.exe"
    )

    foreach ($candidate in $candidates) {
        if (Test-Python38OrNewer $candidate) {
            $command = Get-Command $candidate -ErrorAction SilentlyContinue
            if ($command) {
                return $command.Source
            }
            return $candidate
        }
    }

    throw "Python 3.8+ was not found. Install it from https://www.python.org/downloads/."
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Smart Proxy setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "[1/5] Checking Python..." -ForegroundColor Yellow
$PythonPath = Resolve-PythonPath $PythonPath
$pythonVersion = & $PythonPath --version
Write-Host "  Found: $PythonPath ($pythonVersion)" -ForegroundColor Green

Write-Host "[2/5] Preparing install directory..." -ForegroundColor Yellow
if (-not $SmartProxyDir) {
    $SmartProxyDir = Join-Path $env:USERPROFILE "smart-proxy"
}
if (-not (Test-Path $SmartProxyDir)) {
    New-Item -ItemType Directory -Path $SmartProxyDir -Force | Out-Null
}
$SmartProxyDir = (Resolve-Path $SmartProxyDir).Path
Write-Host "  smart-proxy: $SmartProxyDir" -ForegroundColor Green

Write-Host "[3/5] Copying files..." -ForegroundColor Yellow
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$files = @("smart-proxy.py", "whitelist.txt", "start-proxy.vbs", "claude-with-proxy.ps1", "README.md")
foreach ($file in $files) {
    $src = Join-Path $scriptDir $file
    $dst = Join-Path $SmartProxyDir $file
    if (Test-Path $src) {
        Copy-Item $src $dst -Force
        Write-Host "  $file" -ForegroundColor Gray
    } else {
        Write-Host "  Skipped $file (source file not found)" -ForegroundColor DarkGray
    }
}
Write-Host "  Files copied" -ForegroundColor Green

Write-Host "[4/5] Generating start-proxy.vbs..." -ForegroundColor Yellow
$vbsContent = @"
Set oShell = CreateObject("WScript.Shell")
Set fso    = CreateObject("Scripting.FileSystemObject")
proxyDir   = fso.GetParentFolderName(WScript.ScriptFullName)
oShell.Run "$PythonPath """ & proxyDir & "\smart-proxy.py""", 0, False
"@
Set-Content -Path (Join-Path $SmartProxyDir "start-proxy.vbs") -Value $vbsContent -Encoding ASCII
Write-Host "  start-proxy.vbs (Python: $PythonPath)" -ForegroundColor Gray

Write-Host "[5/5] Generating Claude launcher..." -ForegroundColor Yellow
if (-not $ClaudeCodeDir) {
    $knownClaudeDirs = @(
        (Join-Path $env:USERPROFILE ".claude"),
        (Join-Path $env:APPDATA "npm\node_modules\@anthropic-ai\claude-code")
    )
    foreach ($dir in $knownClaudeDirs) {
        if (Test-Path $dir) {
            Write-Host "  Claude Code directory candidate: $dir" -ForegroundColor DarkGray
        }
    }

    $claudeExe = (Get-Command claude -ErrorAction SilentlyContinue).Source
    if ($claudeExe) {
        Write-Host "  Found claude command: $claudeExe" -ForegroundColor Green
    }
}

$claudePs1Content = @"
# Smart Proxy + Claude Code launcher
# Generated: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")

`$sidecarPort = netstat -ano 2>`$null | Select-String ":8889.*LISTENING"
function Test-LocalPort {
    param(
        [Parameter(Mandatory = `$true)]
        [string]`$Port
    )

    return [bool](netstat -ano 2>`$null | Select-String ":`$Port.*LISTENING")
}

function Wait-StatsDashboard {
    param(
        [int]`$TimeoutSeconds = 10
    )

    `$deadline = (Get-Date).AddSeconds(`$TimeoutSeconds)
    while ((Get-Date) -lt `$deadline) {
        try {
            `$response = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8890/api/summary?range=day" -TimeoutSec 2
            if (`$response.StatusCode -eq 200) {
                return `$true
            }
        }
        catch {
            Start-Sleep -Milliseconds 300
        }
    }
    return `$false
}

`$proxyReady = Test-LocalPort "8889"
`$dashboardReady = Test-LocalPort "8890"
if (`$proxyReady -and -not `$dashboardReady) {
    Write-Host "[proxy] Existing sidecar is running, but dashboard is not available." -ForegroundColor Red
    Write-Host "[proxy] Stop the old process using port 8889, then run this script again." -ForegroundColor Yellow
    exit 1
}
if (-not (`$proxyReady -and `$dashboardReady)) {
    Write-Host "[proxy] Starting sidecar + dashboard..." -ForegroundColor Yellow
    `$logDir = Join-Path "$SmartProxyDir" "logs"
    New-Item -ItemType Directory -Force -Path `$logDir | Out-Null
    `$outLog = Join-Path `$logDir "smart-proxy.out.log"
    `$errLog = Join-Path `$logDir "smart-proxy.err.log"
    Start-Process -WindowStyle Hidden -FilePath "$PythonPath" -ArgumentList "$SmartProxyDir\smart-proxy.py" -RedirectStandardOutput `$outLog -RedirectStandardError `$errLog
}

`$proxyReady = Test-LocalPort "8889"
`$dashboardReady = (Test-LocalPort "8890") -and (Wait-StatsDashboard)
if (-not `$proxyReady) {
    Write-Host "[proxy] Proxy port 8889 is not ready" -ForegroundColor Red
    Write-Host "[proxy] Log: $SmartProxyDir\logs\smart-proxy.err.log" -ForegroundColor Yellow
    exit 1
}
if (-not `$dashboardReady) {
    Write-Host "[proxy] Dashboard HTTP is not ready: http://127.0.0.1:8890" -ForegroundColor Red
    Write-Host "[proxy] Log: $SmartProxyDir\logs\smart-proxy.err.log" -ForegroundColor Yellow
    exit 1
}

`$env:HTTP_PROXY  = "http://127.0.0.1:8889"
`$env:HTTPS_PROXY = "http://127.0.0.1:8889"
Write-Host "[proxy] -> 127.0.0.1:8889 (auto-detect)" -ForegroundColor Green
Write-Host "[stats] -> http://127.0.0.1:8890" -ForegroundColor Green
Write-Host ""

function Get-EnvValue {
    param(
        [Parameter(Mandatory = `$true)]
        [string]`$Name
    )

    `$value = [Environment]::GetEnvironmentVariable(`$Name, "Process")
    if (-not `$value) {
        `$value = [Environment]::GetEnvironmentVariable(`$Name, "User")
    }
    if (-not `$value) {
        `$value = [Environment]::GetEnvironmentVariable(`$Name, "Machine")
    }
    return `$value
}

function Set-ModelProvider {
    param(
        [Parameter(Mandatory = `$true)]
        [hashtable]`$Provider
    )

    `$token = `$null
    `$tokenName = `$null
    foreach (`$candidate in @(`$Provider.TokenEnv)) {
        `$token = Get-EnvValue `$candidate
        if (`$token) {
            `$tokenName = `$candidate
            break
        }
    }
    if (-not `$token) {
        Write-Host "[model] Missing environment variable: `$(@(`$Provider.TokenEnv) -join ' / ')" -ForegroundColor Red
        Write-Host "Set it first, for example:" -ForegroundColor Yellow
        Write-Host "  setx `$(@(`$Provider.TokenEnv)[0]) `"your-api-key`"" -ForegroundColor White
        exit 1
    }

    `$env:ANTHROPIC_BASE_URL = `$Provider.BaseUrl
    `$env:ANTHROPIC_AUTH_TOKEN = `$token
    `$env:ANTHROPIC_MODEL = `$Provider.Model
    `$env:ANTHROPIC_DEFAULT_OPUS_MODEL = if (`$Provider.OpusModel) { `$Provider.OpusModel } else { `$Provider.Model }
    `$env:ANTHROPIC_DEFAULT_SONNET_MODEL = if (`$Provider.SonnetModel) { `$Provider.SonnetModel } else { `$Provider.Model }
    `$env:ANTHROPIC_DEFAULT_HAIKU_MODEL = if (`$Provider.HaikuModel) { `$Provider.HaikuModel } else { `$Provider.Model }
    `$env:ANTHROPIC_SMALL_FAST_MODEL = if (`$Provider.SmallModel) { `$Provider.SmallModel } else { `$env:ANTHROPIC_DEFAULT_HAIKU_MODEL }
    `$env:ANTHROPIC_REASONING_MODEL = if (`$Provider.ReasoningModel) { `$Provider.ReasoningModel } else { `$Provider.Model }
    if (`$Provider.SubagentModel) {
        `$env:CLAUDE_CODE_SUBAGENT_MODEL = `$Provider.SubagentModel
    } else {
        Remove-Item Env:CLAUDE_CODE_SUBAGENT_MODEL -ErrorAction SilentlyContinue
    }
    if (`$Provider.EffortLevel) {
        `$env:CLAUDE_CODE_EFFORT_LEVEL = `$Provider.EffortLevel
    } else {
        Remove-Item Env:CLAUDE_CODE_EFFORT_LEVEL -ErrorAction SilentlyContinue
    }
    `$env:CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = "1"

    Write-Host "[model] `$(`$Provider.Label)" -ForegroundColor Green
    Write-Host "[base]  `$(`$Provider.BaseUrl)" -ForegroundColor DarkGray
    Write-Host "[key]   `${tokenName}: set" -ForegroundColor DarkGray
    Write-Host ""
}

`$providers = @{
    "1" = @{
        Label = "MiniMax-M2.7-highspeed"
        BaseUrl = "https://api.minimaxi.com/anthropic"
        Model = "MiniMax-M2.7-highspeed"
        EffortLevel = "xhigh"
        TokenEnv = "MINIMAX_API_KEY"
    }
    "2" = @{
        Label = "deepseek-v4-pro[1m]"
        BaseUrl = "https://api.deepseek.com/anthropic"
        Model = "deepseek-v4-pro[1m]"
        OpusModel = "deepseek-v4-pro[1m]"
        SonnetModel = "deepseek-v4-pro[1m]"
        HaikuModel = "deepseek-v4-flash"
        SmallModel = "deepseek-v4-flash"
        SubagentModel = "deepseek-v4-flash"
        EffortLevel = "max"
        TokenEnv = "DEEPSEEK_API_KEY"
    }
    "3" = @{
        Label = "deepseek-v4-flash[1m]"
        BaseUrl = "https://api.deepseek.com/anthropic"
        Model = "deepseek-v4-flash[1m]"
        OpusModel = "deepseek-v4-flash[1m]"
        SonnetModel = "deepseek-v4-flash[1m]"
        HaikuModel = "deepseek-v4-flash"
        SmallModel = "deepseek-v4-flash"
        SubagentModel = "deepseek-v4-flash"
        EffortLevel = "max"
        TokenEnv = "DEEPSEEK_API_KEY"
    }
    "4" = @{
        Label = "MiMo-V2.5-Pro"
        BaseUrl = "https://token-plan-cn.xiaomimimo.com/anthropic"
        Model = "mimo-v2.5-pro"
        EffortLevel = "xhigh"
        TokenEnv = @("MIMO_API_KEY", "ANTHROPIC_AUTH_TOKEN")
    }
    "5" = @{
        Label = "MiMo-V2.5"
        BaseUrl = "https://token-plan-cn.xiaomimimo.com/anthropic"
        Model = "mimo-v2.5"
        EffortLevel = "xhigh"
        TokenEnv = @("MIMO_API_KEY", "ANTHROPIC_AUTH_TOKEN")
    }
}

if (`$args.Count -gt 0) {
    Set-ModelProvider `$providers["1"]
    Write-Host "Starting directly: claude `$args" -ForegroundColor Yellow
    & claude @args
    return
}

Write-Host "=== Select model provider ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "  [1] MiniMax-M2.7-highspeed   (default)"
Write-Host "  [2] deepseek-v4-pro[1m]"
Write-Host "  [3] deepseek-v4-flash[1m]"
Write-Host "  [4] MiMo-V2.5-Pro"
Write-Host "  [5] MiMo-V2.5"
Write-Host ""

`$modelChoice = Read-Host "Enter 1/2/3/4/5 (default 1)"
if (-not `$providers.ContainsKey(`$modelChoice)) {
    `$modelChoice = "1"
}
Set-ModelProvider `$providers[`$modelChoice]

Write-Host "=== Select launch mode ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "  [1] claude"
Write-Host "      Safe mode; operations require manual approval"
Write-Host ""
Write-Host "  [2] claude --dangerously-skip-permissions"
Write-Host "      Skip permission prompts"
Write-Host ""
Write-Host "  [3] claude --dangerously-skip-permissions --resume"
Write-Host "      Resume the previous conversation and skip permission prompts"
Write-Host ""

`$choice = Read-Host "Enter 1/2/3 (default 2)"

switch (`$choice) {
    "1" {
        Write-Host "Starting safe mode..." -ForegroundColor Yellow
        & claude
    }
    "3" {
        Write-Host "Resuming previous conversation..." -ForegroundColor Yellow
        & claude --dangerously-skip-permissions --resume
    }
    default {
        & claude --dangerously-skip-permissions
    }
}
"@
Set-Content -Path $ClaudePs1Path -Value $claudePs1Content -Encoding UTF8
Write-Host "  claude.ps1 -> $ClaudePs1Path" -ForegroundColor Gray

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Setup complete" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Start with:" -ForegroundColor Cyan
Write-Host "  powershell -File $ClaudePs1Path" -ForegroundColor White
Write-Host ""
Write-Host "Core files:" -ForegroundColor Cyan
Write-Host "  $SmartProxyDir\smart-proxy.py" -ForegroundColor White
Write-Host "  $SmartProxyDir\whitelist.txt" -ForegroundColor White
Write-Host "  $ClaudePs1Path" -ForegroundColor White
Write-Host ""
Write-Host "Edit whitelist:" -ForegroundColor Cyan
Write-Host "  $SmartProxyDir\whitelist.txt" -ForegroundColor White
