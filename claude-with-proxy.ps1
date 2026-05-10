# Claude Code 智能代理启动脚本
# 复制到 C:\Users\<用户名>\claude.ps1，修改下方路径后使用

# ====== 改这两处 ======
$CLAUDE_PROJECT_DIR = "<你的Claude Code项目目录>"
$PYTHON_PATH         = "<Python路径>\python.exe"
$SMART_PROXY_DIR     = "<smart-proxy项目目录>"
# =====================

Set-Location $CLAUDE_PROJECT_DIR

function Test-LocalPort {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Port
    )

    return [bool](netstat -ano 2>$null | Select-String ":$Port.*LISTENING")
}

function Wait-StatsDashboard {
    param(
        [int]$TimeoutSeconds = 10
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8890/api/summary?range=day" -TimeoutSec 2
            if ($response.StatusCode -eq 200) {
                return $true
            }
        }
        catch {
            Start-Sleep -Milliseconds 300
        }
    }
    return $false
}

# 检查 sidecar / dashboard 是否已在运行，没有则自动拉起
$proxyReady = Test-LocalPort "8889"
$dashboardReady = Test-LocalPort "8890"
if ($proxyReady -and -not $dashboardReady) {
    Write-Host "[proxy] 检测到旧版 sidecar 正在运行，但 dashboard 未启动。" -ForegroundColor Red
    Write-Host "[proxy] 请先停止占用 8889 的旧进程，再重新运行本脚本。" -ForegroundColor Yellow
    exit 1
}
if (-not ($proxyReady -and $dashboardReady)) {
    Write-Host "[proxy] 启动 sidecar + dashboard..." -ForegroundColor Yellow
    $logDir = Join-Path $SMART_PROXY_DIR "logs"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $outLog = Join-Path $logDir "smart-proxy.out.log"
    $errLog = Join-Path $logDir "smart-proxy.err.log"
    Start-Process -WindowStyle Hidden -FilePath $PYTHON_PATH -ArgumentList "$SMART_PROXY_DIR\smart-proxy.py" -RedirectStandardOutput $outLog -RedirectStandardError $errLog
}

$proxyReady = Test-LocalPort "8889"
$dashboardReady = (Test-LocalPort "8890") -and (Wait-StatsDashboard)
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

$env:HTTP_PROXY  = "http://127.0.0.1:8889"
$env:HTTPS_PROXY = "http://127.0.0.1:8889"
Write-Host "[proxy] -> 127.0.0.1:8889 (auto-detect)" -ForegroundColor Green
Write-Host "[stats] -> http://127.0.0.1:8890" -ForegroundColor Green
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
    Write-Host ""
}

$providers = @{
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

# 如果带了参数，直接透传，跳过菜单
if ($args.Count -gt 0) {
    Set-ModelProvider $providers["1"]
    Write-Host "直接启动: claude $args" -ForegroundColor Yellow
    & claude @args
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
Write-Host ""

$modelChoice = Read-Host "输入序号 (1/2/3/4/5，默认 1)"
if (-not $providers.ContainsKey($modelChoice)) {
    $modelChoice = "1"
}
Set-ModelProvider $providers[$modelChoice]

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
        & claude
    }
    "3" {
        Write-Host "恢复历史会话..." -ForegroundColor Yellow
        & claude --dangerously-skip-permissions --resume
    }
    default {
        & claude --dangerously-skip-permissions
    }
}
