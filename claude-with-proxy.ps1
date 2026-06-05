# Claude Code 智能代理启动脚本
$ProgressPreference = "SilentlyContinue"
# 复制到 C:\Users\<用户名>\claude.ps1，修改下方路径后使用

# ====== 改这两处 ======
$CLAUDE_PROJECT_DIR = "<你的Claude Code项目目录>"
$PYTHON_PATH         = "<Python路径>\python.exe"
$SMART_PROXY_DIR     = "<smart-proxy项目目录>"
$LOCALMEMORY_MCP_CMD = "<LocalMemory MCP cmd路径>"
$CLAUDE_SLIM_MCP_CONFIG = Join-Path $env:USERPROFILE ".claude\mcp-slim.json"
$CLAUDE_MITM_TOKEN_CAPTURE_PREF = Join-Path $env:USERPROFILE ".smart-proxy\claude-mitm-token-capture.pref"
# =====================

$earlySmartProxyServiceStatusArgs = @("-sps", "--sps", "-proxy-status", "--proxy-status", "-smart-proxy-status", "--smart-proxy-status")
$earlySmartProxyServiceRestartArgs = @("-spr", "--spr", "-proxy-restart", "--proxy-restart", "-smart-proxy-restart", "--smart-proxy-restart")
if ($args.Count -eq 1 -and ($earlySmartProxyServiceStatusArgs -contains $args[0] -or $earlySmartProxyServiceRestartArgs -contains $args[0])) {
    $serviceScript = Join-Path $SMART_PROXY_DIR "install-smart-proxy-service.ps1"
    if (-not (Test-Path -LiteralPath $serviceScript)) {
        Write-Host "[service] 未找到服务管理脚本: $serviceScript" -ForegroundColor Red
        return
    }

    $serviceAction = if ($earlySmartProxyServiceRestartArgs -contains $args[0]) { "Restart" } else { "Status" }
    Write-Host "[service] 执行: $serviceAction" -ForegroundColor Cyan
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $serviceScript "-$serviceAction"
    return
}

Set-Location $CLAUDE_PROJECT_DIR

function Initialize-SlimMcpConfig {
    if (-not $LOCALMEMORY_MCP_CMD -or $LOCALMEMORY_MCP_CMD -like "<*") {
        return $null
    }

    $configDir = Split-Path -Parent $CLAUDE_SLIM_MCP_CONFIG
    New-Item -ItemType Directory -Force -Path $configDir | Out-Null

    $config = [ordered]@{
        mcpServers = [ordered]@{
            localmemory = [ordered]@{
                type = "stdio"
                command = "cmd"
                args = @("/c", $LOCALMEMORY_MCP_CMD)
                env = @{}
            }
        }
    }

    $json = $config | ConvertTo-Json -Depth 8
    [System.IO.File]::WriteAllText($CLAUDE_SLIM_MCP_CONFIG, $json, [System.Text.UTF8Encoding]::new($false))
    return $CLAUDE_SLIM_MCP_CONFIG
}

function Get-ClaudeMcpArgs {
    param(
        [string[]]$ExtraArgs = @()
    )

    if ($env:CLAUDE_FULL_MCP -eq "1") {
        Write-Host "[mcp] 完整模式: 使用全局 MCP 配置。" -ForegroundColor DarkGray
        return @()
    }

    foreach ($arg in @($ExtraArgs)) {
        if ($arg -in @("-h", "--help", "-v", "--version", "mcp", "plugin", "plugins", "agents", "auth", "doctor", "project", "setup-token", "install", "update", "upgrade")) {
            Write-Host "[mcp] 检测到 Claude 管理命令，跳过精简 MCP 注入。" -ForegroundColor DarkGray
            return @()
        }
        if ($arg -in @("--mcp-config", "--strict-mcp-config", "--bare")) {
            Write-Host "[mcp] 检测到自定义 MCP/裸模式参数，跳过精简 MCP 注入。" -ForegroundColor DarkGray
            return @()
        }
    }

    $configPath = Initialize-SlimMcpConfig
    if (-not $configPath) {
        Write-Host "[mcp] 未配置 LocalMemory MCP，使用全局 MCP 配置。" -ForegroundColor DarkGray
        return @()
    }

    Write-Host "[mcp] 精简模式: 仅加载 LocalMemory；临时完整 MCP 可先设置 CLAUDE_FULL_MCP=1。" -ForegroundColor DarkGray
    return @("--strict-mcp-config", "--mcp-config=$configPath")
}

function Test-LocalPort {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Port
    )

    return [bool](netstat -ano 2>$null | Select-String ":$Port.*LISTENING")
}

function Wait-LocalPort {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Port,
        [int]$TimeoutSeconds = 10
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-LocalPort $Port) {
            return $true
        }
        Start-Sleep -Milliseconds 150
    }
    return $false
}

function Wait-StatsDashboard {
    param(
        [int]$TimeoutSeconds = 10,
        [int]$RequestTimeoutSeconds = 1
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8890/api/runtime-status" -TimeoutSec $RequestTimeoutSeconds
            if ($response.StatusCode -eq 200) {
                return $true
            }
        }
        catch {
            Start-Sleep -Milliseconds 150
        }
    }
    return $false
}

function Test-MitmTokenCapture {
    param(
        [int]$Port = 8891
    )

    return [bool](Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Set-ClaudeStandardProxy {
    $proxyUrl = "http://127.0.0.1:8889"
    $env:HTTP_PROXY = $proxyUrl
    $env:HTTPS_PROXY = $proxyUrl
    $env:ALL_PROXY = $proxyUrl
    $env:http_proxy = $proxyUrl
    $env:https_proxy = $proxyUrl
    $env:all_proxy = $proxyUrl
    $env:NO_PROXY = "127.0.0.1,localhost"
    $env:no_proxy = "127.0.0.1,localhost"
}

function Ensure-MitmTokenCapture {
    param(
        [int]$Port = 8891
    )

    if (Test-MitmTokenCapture -Port $Port) {
        Write-Host "[mitm-token] 已运行: http://127.0.0.1:$Port" -ForegroundColor Green
        return $true
    }

    $scriptPath = Join-Path $SMART_PROXY_DIR "start-mitm-token-capture.ps1"
    if (-not (Test-Path -LiteralPath $scriptPath)) {
        Write-Host "[mitm-token] 未找到启动脚本: $scriptPath" -ForegroundColor Red
        return $false
    }

    Write-Host "[mitm-token] 启动 token capture sidecar..." -ForegroundColor Yellow
    & $scriptPath -Port $Port -Background
    Start-Sleep -Seconds 3
    if (Test-MitmTokenCapture -Port $Port) {
        Write-Host "[mitm-token] -> http://127.0.0.1:$Port" -ForegroundColor Green
        return $true
    }

    Write-Host "[mitm-token] sidecar 未监听 127.0.0.1:$Port，请查看 logs\mitm-token-capture.err.log" -ForegroundColor Red
    return $false
}

function Enable-ClaudeMitmTokenCapture {
    param(
        [int]$Port = 8891
    )

    if (-not (Ensure-MitmTokenCapture -Port $Port)) {
        return $false
    }

    $caPem = Join-Path $env:USERPROFILE ".mitmproxy\mitmproxy-ca-cert.pem"
    $caCer = Join-Path $env:USERPROFILE ".mitmproxy\mitmproxy-ca-cert.cer"
    if (-not (Test-Path -LiteralPath $caPem)) {
        Write-Host "[mitm-token] 未找到 mitmproxy CA PEM: $caPem" -ForegroundColor Red
        Write-Host "[mitm-token] 可先运行一次 mitmdump 生成证书。" -ForegroundColor Yellow
        return $false
    }

    $proxyUrl = "http://127.0.0.1:$Port"
    $env:HTTP_PROXY = $proxyUrl
    $env:HTTPS_PROXY = $proxyUrl
    $env:ALL_PROXY = $proxyUrl
    $env:http_proxy = $proxyUrl
    $env:https_proxy = $proxyUrl
    $env:all_proxy = $proxyUrl
    $env:NODE_EXTRA_CA_CERTS = $caPem
    $env:SSL_CERT_FILE = $caPem
    $env:REQUESTS_CA_BUNDLE = $caPem
    $env:NO_PROXY = "127.0.0.1,localhost"
    $env:no_proxy = "127.0.0.1,localhost"

    Write-Host "[mitm-token] Claude Code 本次会话将走 MITM 代理: $proxyUrl" -ForegroundColor Green
    Write-Host "[mitm-token] CA PEM: $caPem" -ForegroundColor DarkGray
    Write-Host "[mitm-token] Windows CA: $caCer" -ForegroundColor DarkGray
    Write-Host "[mitm-token] 捕获文件: $SMART_PROXY_DIR\logs\token-capture-$(Get-Date -Format yyyy-MM-dd).jsonl" -ForegroundColor DarkGray
    return $true
}

function Normalize-ClaudeMitmTokenCapturePreference {
    param(
        [string]$Value
    )

    if (-not $Value) {
        return ""
    }

    switch -Regex ($Value.Trim().ToLowerInvariant()) {
        "^(1|true|yes|y|on|enable|enabled)$" { return "enable" }
        "^(0|false|no|n|off|disable|disabled)$" { return "disable" }
        "^(ask|prompt)$" { return "ask" }
        default { return "" }
    }
}

function Get-ClaudeMitmTokenCapturePreference {
    if (-not (Test-Path -LiteralPath $CLAUDE_MITM_TOKEN_CAPTURE_PREF)) {
        return ""
    }

    try {
        $raw = [System.IO.File]::ReadAllText($CLAUDE_MITM_TOKEN_CAPTURE_PREF, [System.Text.Encoding]::UTF8)
        return (Normalize-ClaudeMitmTokenCapturePreference -Value $raw)
    }
    catch {
        return ""
    }
}

function Set-ClaudeMitmTokenCapturePreference {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("enable", "disable", "ask")]
        [string]$Preference
    )

    $prefDir = Split-Path -Parent $CLAUDE_MITM_TOKEN_CAPTURE_PREF
    New-Item -ItemType Directory -Force -Path $prefDir | Out-Null

    if ($Preference -eq "ask") {
        Remove-Item -LiteralPath $CLAUDE_MITM_TOKEN_CAPTURE_PREF -Force -ErrorAction SilentlyContinue
        Write-Host "[mitm-token] 已恢复为下次启动询问。" -ForegroundColor Green
        return
    }

    [System.IO.File]::WriteAllText(
        $CLAUDE_MITM_TOKEN_CAPTURE_PREF,
        $Preference,
        [System.Text.UTF8Encoding]::new($false)
    )
    $label = if ($Preference -eq "enable") { "启用" } else { "不启用" }
    Write-Host "[mitm-token] 已记住偏好: $label" -ForegroundColor Green
}

function Read-ClaudeMitmTokenCapturePreference {
    Write-Host ""
    Write-Host "=== Claude Code MITM Token Capture ===" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  [1] 启用并记住      (默认)"
    Write-Host "  [2] 仅本次启用"
    Write-Host "  [3] 本次不启用"
    Write-Host "  [4] 不启用并记住"
    Write-Host ""

    $choice = Read-Host "输入序号 (1/2/3/4，默认 1)"
    switch ($choice) {
        "2" { return "enable-once" }
        "3" { return "disable-once" }
        "4" {
            Set-ClaudeMitmTokenCapturePreference -Preference "disable"
            return "disable"
        }
        default {
            Set-ClaudeMitmTokenCapturePreference -Preference "enable"
            return "enable"
        }
    }
}

function Resolve-ClaudeMitmTokenCaptureMode {
    $override = Normalize-ClaudeMitmTokenCapturePreference -Value $env:CLAUDE_MITM_TOKEN_CAPTURE
    if ($override -eq "enable") {
        Write-Host "[mitm-token] 环境变量覆盖: 启用 (CLAUDE_MITM_TOKEN_CAPTURE=1)" -ForegroundColor DarkGray
        return "enable-once"
    }
    if ($override -eq "disable") {
        Write-Host "[mitm-token] 环境变量覆盖: 不启用 (CLAUDE_MITM_TOKEN_CAPTURE=0)" -ForegroundColor DarkGray
        return "disable-once"
    }
    if ($override -eq "ask") {
        Write-Host "[mitm-token] 环境变量覆盖: 本次询问 (CLAUDE_MITM_TOKEN_CAPTURE=ask)" -ForegroundColor DarkGray
        return (Read-ClaudeMitmTokenCapturePreference)
    }

    $saved = Get-ClaudeMitmTokenCapturePreference
    if ($saved -eq "enable") {
        Write-Host "[mitm-token] 当前偏好: 启用，自动启用 Token Capture。" -ForegroundColor DarkGray
        return "enable"
    }
    if ($saved -eq "disable") {
        Write-Host "[mitm-token] 当前偏好: 不启用，Claude Code 使用普通代理。" -ForegroundColor DarkGray
        return "disable"
    }

    return (Read-ClaudeMitmTokenCapturePreference)
}

function Use-ClaudeMitmTokenCapturePreference {
    param(
        [int]$Port = 8891
    )

    $mode = Resolve-ClaudeMitmTokenCaptureMode
    if ($mode -like "enable*") {
        if (-not (Enable-ClaudeMitmTokenCapture -Port $Port)) {
            Write-Host "[mitm-token] 启用失败，已保留原代理 127.0.0.1:8889。" -ForegroundColor Yellow
            Set-ClaudeStandardProxy
            return $false
        }
        return $true
    }

    Set-ClaudeStandardProxy
    Write-Host "[mitm-token] 本次未启用，Claude Code 继续使用普通代理 127.0.0.1:8889。" -ForegroundColor DarkGray
    return $false
}

function Open-ManagementPagesInChrome {
    $urls = @(
        "http://127.0.0.1:8890"
        "http://127.0.0.1:39393/dashboard"
    )

    $chromeCmd = Get-Command chrome.exe -ErrorAction SilentlyContinue
    $chromeCandidates = @(
        if ($chromeCmd) { $chromeCmd.Source }
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    )
    $chromePath = $chromeCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1

    if (-not $chromePath) {
        Write-Host "[browser] 未找到 Google Chrome，跳过自动打开管理页面。" -ForegroundColor Yellow
        return $false
    }

    try {
        Start-Process -FilePath $chromePath -ArgumentList $urls
        Write-Host "[browser] 已用 Google Chrome 打开管理页面。" -ForegroundColor Green
        return $true
    }
    catch {
        Write-Host "[browser] 打开 Google Chrome 失败: $($_.Exception.Message)" -ForegroundColor Yellow
        return $false
    }
}

# 检查 sidecar / dashboard 是否已在运行，没有则自动拉起
$proxyReady = Test-LocalPort "8889"
$dashboardReady = (Test-LocalPort "8890") -and (Wait-StatsDashboard -TimeoutSeconds 1 -RequestTimeoutSeconds 1)
if ($proxyReady -and -not $dashboardReady) {
    Write-Host "[proxy] 检测到旧版 sidecar 正在运行，但 dashboard 未启动。" -ForegroundColor Red
    Write-Host "[proxy] 请先停止占用 8889 的旧进程，再重新运行本脚本。" -ForegroundColor Yellow
    exit 1
}
if ($proxyReady -and $dashboardReady) {
    $sidecarStarted = $false
}
else {
    Write-Host "[proxy] 启动 sidecar + dashboard..." -ForegroundColor Yellow
    $logDir = Join-Path $SMART_PROXY_DIR "logs"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $outLog = Join-Path $logDir "smart-proxy.out.log"
    $errLog = Join-Path $logDir "smart-proxy.err.log"
    Start-Process -WindowStyle Hidden -FilePath $PYTHON_PATH -ArgumentList "$SMART_PROXY_DIR\smart-proxy.py" -RedirectStandardOutput $outLog -RedirectStandardError $errLog
    $sidecarStarted = $true
}

if ($sidecarStarted) {
    $proxyReady = Wait-LocalPort "8889" -TimeoutSeconds 5
    $dashboardPortReady = Wait-LocalPort "8890" -TimeoutSeconds 5
    $dashboardReady = $dashboardPortReady -and (Wait-StatsDashboard -TimeoutSeconds 8 -RequestTimeoutSeconds 1)
}
if (-not $proxyReady) {
    Write-Host "[proxy] 代理端口 8889 未就绪" -ForegroundColor Red
    Write-Host "[proxy] 日志: $SMART_PROXY_DIR\logs\smart-proxy.err.log" -ForegroundColor Yellow
    exit 1
}
if (-not $dashboardReady) {
    Write-Host "[proxy] Dashboard HTTP 未就绪: http://127.0.0.1:8890" -ForegroundColor Red
    Write-Host "[proxy] 日志: $SMART_PROXY_DIR\logs\smart-proxy.err.log" -ForegroundColor Yellow
    exit 1
}

Set-ClaudeStandardProxy
Write-Host "[proxy] -> 127.0.0.1:8889 (auto-detect)" -ForegroundColor Green
Write-Host "[stats] -> http://127.0.0.1:8890" -ForegroundColor Green
# Auto-open of management pages is currently disabled.
# Open-ManagementPagesInChrome | Out-Null
Write-Host ""

function Get-EnvValue {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if (-not $value) {
        $value = [Environment]::GetEnvironmentVariable($Name, "User")
    }
    if (-not $value) {
        $value = [Environment]::GetEnvironmentVariable($Name, "Machine")
    }
    return $value
}

function Write-ProviderHealthStatus {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Provider,
        [Parameter(Mandatory = $true)]
        [bool]$Ok,
        [Parameter(Mandatory = $true)]
        [string]$Status,
        [Parameter(Mandatory = $true)]
        [string]$Detail
    )

    $logDir = Join-Path $SMART_PROXY_DIR "logs"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $healthPath = Join-Path $logDir "provider-health.json"
    $payload = [ordered]@{
        checked_at = (Get-Date).ToUniversalTime().ToString("o")
        label = $Provider.Label
        base_url = $Provider.BaseUrl
        model = $Provider.Model
        ok = $Ok
        status = $Status
        detail = $Detail
    }
    $payload | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $healthPath -Encoding UTF8
}

function Test-ModelProviderHealth {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Provider,
        [Parameter(Mandatory = $true)]
        [string]$Token
    )

    $endpoint = "$($Provider.BaseUrl.TrimEnd('/'))/v1/messages"
    $body = @{
        model = $Provider.Model
        max_tokens = 1
        messages = @(@{ role = "user"; content = "ping" })
    } | ConvertTo-Json -Depth 5 -Compress
    $headers = @{
        "x-api-key" = $Token
        "Authorization" = "Bearer $Token"
        "anthropic-version" = "2023-06-01"
        "content-type" = "application/json"
    }

    try {
        $response = Invoke-WebRequest -UseBasicParsing -Method Post -Uri $endpoint -Headers $headers -Body $body -TimeoutSec 20
        Write-ProviderHealthStatus -Provider $Provider -Ok $true -Status "ok" -Detail "HTTP $($response.StatusCode)"
        Write-Host "[model-check] $($Provider.Label): OK" -ForegroundColor Green
        return $true
    }
    catch {
        $statusCode = $null
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            $statusCode = [int]$_.Exception.Response.StatusCode
        }
        $detail = if ($_.ErrorDetails.Message) { $_.ErrorDetails.Message } else { $_.Exception.Message }
        $lowerDetail = $detail.ToLowerInvariant()
        $status = "check_failed"
        if (($statusCode -eq 429) -or $lowerDetail.Contains("quota exhausted") -or $lowerDetail.Contains("insufficient") -or $lowerDetail.Contains("expired")) {
            $status = "quota_exhausted"
        }
        elseif (($statusCode -eq 401) -or ($statusCode -eq 403)) {
            $status = "auth_error"
        }
        elseif (($statusCode -eq 400) -or $lowerDetail.Contains("param incorrect") -or $lowerDetail.Contains("not supported model")) {
            $status = "param_error"
        }
        elseif (($statusCode -and $statusCode -ge 500) -or $lowerDetail.Contains("bad gateway")) {
            $status = "upstream_error"
        }

        Write-ProviderHealthStatus -Provider $Provider -Ok $false -Status $status -Detail "HTTP $statusCode $detail"
        if ($status -eq "quota_exhausted") {
            Write-Host "[model-check] $($Provider.Label): 额度/套餐不可用，检测到 quota exhausted 或 HTTP 429。" -ForegroundColor Red
            Write-Host "[model-check] 请续费/换模型后重新运行 claude.ps1。" -ForegroundColor Yellow
            return $false
        }
        if ($status -eq "auth_error") {
            Write-Host "[model-check] $($Provider.Label): 鉴权失败，检查 API Key。" -ForegroundColor Red
            return $false
        }
        if ($status -eq "param_error") {
            Write-Host "[model-check] $($Provider.Label): 参数不被上游接受，已阻止启动。" -ForegroundColor Red
            return $false
        }
        if ($status -eq "upstream_error") {
            Write-Host "[model-check] $($Provider.Label): 上游网关异常 HTTP $statusCode，已阻止启动，避免进入失败重试。" -ForegroundColor Red
            return $false
        }

        Write-Host "[model-check] $($Provider.Label): 健康检查未通过，已阻止启动；详情已写入 dashboard Doctor。" -ForegroundColor Yellow
        return $false
    }
}

function Set-ModelProvider {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Provider
    )

    $token = $null
    $tokenName = $null
    foreach ($candidate in @($Provider.TokenEnv)) {
        $token = Get-EnvValue $candidate
        if ($token) {
            $tokenName = $candidate
            break
        }
    }
    if (-not $token) {
        Write-Host "[model] 缺少环境变量: $(@($Provider.TokenEnv) -join ' / ')" -ForegroundColor Red
        Write-Host "请先设置后再启动，例如:" -ForegroundColor Yellow
        Write-Host "  setx $(@($Provider.TokenEnv)[0]) `"your-api-key`"" -ForegroundColor White
        exit 1
    }

    $env:ANTHROPIC_BASE_URL = $Provider.BaseUrl
    $env:ANTHROPIC_AUTH_TOKEN = $token
    $env:ANTHROPIC_MODEL = $Provider.Model
    $env:ANTHROPIC_DEFAULT_OPUS_MODEL = if ($Provider.OpusModel) { $Provider.OpusModel } else { $Provider.Model }
    $env:ANTHROPIC_DEFAULT_SONNET_MODEL = if ($Provider.SonnetModel) { $Provider.SonnetModel } else { $Provider.Model }
    $env:ANTHROPIC_DEFAULT_HAIKU_MODEL = if ($Provider.HaikuModel) { $Provider.HaikuModel } else { $Provider.Model }
    $env:ANTHROPIC_SMALL_FAST_MODEL = if ($Provider.SmallModel) { $Provider.SmallModel } else { $env:ANTHROPIC_DEFAULT_HAIKU_MODEL }
    $env:ANTHROPIC_REASONING_MODEL = if ($Provider.ReasoningModel) { $Provider.ReasoningModel } else { $Provider.Model }
    if ($Provider.SubagentModel) {
        $env:CLAUDE_CODE_SUBAGENT_MODEL = $Provider.SubagentModel
    } else {
        Remove-Item Env:CLAUDE_CODE_SUBAGENT_MODEL -ErrorAction SilentlyContinue
    }
    if ($Provider.EffortLevel) {
        $env:CLAUDE_CODE_EFFORT_LEVEL = $Provider.EffortLevel
    } else {
        Remove-Item Env:CLAUDE_CODE_EFFORT_LEVEL -ErrorAction SilentlyContinue
    }
    $env:CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = "1"

    Write-Host "[model] $($Provider.Label)" -ForegroundColor Green
    Write-Host "[base]  $($Provider.BaseUrl)" -ForegroundColor DarkGray
    Write-Host "[key]   ${tokenName}: set" -ForegroundColor DarkGray
    if (-not (Test-ModelProviderHealth -Provider $Provider -Token $token)) {
        exit 1
    }
    Write-Host ""
}

function Invoke-SmartProxyServiceTool {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet("Status", "Restart", "Install")]
        [string]$Action
    )

    $serviceScript = Join-Path $SMART_PROXY_DIR "install-smart-proxy-service.ps1"
    if (-not (Test-Path -LiteralPath $serviceScript)) {
        Write-Host "[service] 未找到服务管理脚本: $serviceScript" -ForegroundColor Red
        return $false
    }

    Write-Host "[service] 执行: $Action" -ForegroundColor Cyan
    $output = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $serviceScript "-$Action" 2>&1
    $exitCode = $LASTEXITCODE
    foreach ($line in $output) {
        Write-Host $line
    }
    if ($exitCode -ne 0) {
        Write-Host "[service] 命令失败: $Action" -ForegroundColor Red
        return $false
    }
    return $true
}

function Show-SmartProxyServiceMenu {
    Write-Host ""
    Write-Host "=== Smart Proxy 服务管理 ===" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  [1] 查看服务状态"
    Write-Host "  [2] 重启服务"
    Write-Host "  [3] 安装/修复并启动服务"
    Write-Host "  [4] MITM Token Capture: 启用并记住"
    Write-Host "  [5] MITM Token Capture: 不启用并记住"
    Write-Host "  [6] MITM Token Capture: 下次启动询问"
    Write-Host ""

    $serviceChoice = Read-Host "输入序号 (1/2/3/4/5/6，默认 1)"
    switch ($serviceChoice) {
        "2" {
            Invoke-SmartProxyServiceTool -Action "Restart" | Out-Null
        }
        "3" {
            Invoke-SmartProxyServiceTool -Action "Install" | Out-Null
        }
        "4" {
            Set-ClaudeMitmTokenCapturePreference -Preference "enable"
        }
        "5" {
            Set-ClaudeMitmTokenCapturePreference -Preference "disable"
        }
        "6" {
            Set-ClaudeMitmTokenCapturePreference -Preference "ask"
        }
        default {
            Invoke-SmartProxyServiceTool -Action "Status" | Out-Null
        }
    }
}

$providers = @{
    "1" = @{
        Label = "MiniMax-M2.7-highspeed"
        BaseUrl = "https://api.minimaxi.com/anthropic"
        Model = "MiniMax-M2.7-highspeed"
        EffortLevel = "max"
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
        TokenEnv = @("MIMO_API_KEY", "MIMO_ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN")
    }
    "5" = @{
        Label = "MiMo-V2.5"
        BaseUrl = "https://token-plan-cn.xiaomimimo.com/anthropic"
        Model = "mimo-v2.5"
        EffortLevel = "xhigh"
        TokenEnv = @("MIMO_API_KEY", "MIMO_ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN")
    }
}

# 如果带了参数，直接透传，跳过菜单
if ($args.Count -gt 0) {
    Set-ModelProvider $providers["1"]
    $mcpArgs = Get-ClaudeMcpArgs -ExtraArgs $args
    Write-Host "直接启动: claude $args" -ForegroundColor Yellow
    & claude @mcpArgs @args
    return
}

# 无参数 -> 显示选择菜单
Write-Host "=== 选择模型提供商 ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "  [1] MiniMax-M2.7-highspeed   (默认)"
Write-Host "  [2] deepseek-v4-pro[1m]"
Write-Host "  [3] deepseek-v4-flash[1m]"
Write-Host "  [4] MiMo-V2.5-Pro"
Write-Host "  [5] MiMo-V2.5"
Write-Host "  [6] Smart Proxy 服务管理"
Write-Host ""

$modelChoice = Read-Host "输入序号 (1/2/3/4/5/6，默认 1)"
if ($modelChoice -eq "6") {
    Show-SmartProxyServiceMenu
    return
}
if (-not $providers.ContainsKey($modelChoice)) {
    $modelChoice = "1"
}
Set-ModelProvider $providers[$modelChoice]
Use-ClaudeMitmTokenCapturePreference -Port 8891 | Out-Null
$mcpArgs = Get-ClaudeMcpArgs

Write-Host "=== 选择启动模式 ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "  [1] claude"
Write-Host "      安全模式，每次操作需要你手动批准"
Write-Host "      适用: 日常开发、代码审查、需要精细控制的场景"
Write-Host ""
Write-Host "  [2] claude --dangerously-skip-permissions"
Write-Host "      跳过所有权限提示，自动执行操作"
Write-Host "      适用: 自动化任务、你完全信任当前工作目录"
Write-Host ""
Write-Host "  [3] claude --dangerously-skip-permissions --resume"
Write-Host "      恢复上次会话 + 跳过权限，继续未完成的工作"
Write-Host "      适用: 中断后恢复，无需重新描述需求"
Write-Host ""

$choice = Read-Host "输入序号 (1/2/3，默认 2)"

switch ($choice) {
    "1" {
        Write-Host "安全模式启动..." -ForegroundColor Yellow
        & claude @mcpArgs
    }
    "3" {
        Write-Host "恢复历史会话..." -ForegroundColor Yellow
        & claude @mcpArgs --dangerously-skip-permissions --resume
    }
    default {
        & claude @mcpArgs --dangerously-skip-permissions
    }
}
