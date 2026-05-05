# Claude Code 智能代理启动脚本
# 复制到 C:\Users\<用户名>\claude.ps1，修改下方路径后使用

# ====== 改这两处 ======
$CLAUDE_PROJECT_DIR = "<你的Claude Code项目目录>"
$PYTHON_PATH         = "<Python路径>\python.exe"
$SMART_PROXY_DIR     = "<smart-proxy项目目录>"
# =====================

Set-Location $CLAUDE_PROJECT_DIR

# 检查 sidecar 是否已在运行，没有则自动拉起
$sidecarPort = netstat -ano 2>$null | Select-String ":8889.*LISTENING"
if (-not $sidecarPort) {
    Write-Host "[proxy] 启动 sidecar..." -ForegroundColor Yellow
    Start-Process -WindowStyle Hidden -FilePath $PYTHON_PATH -ArgumentList "$SMART_PROXY_DIR\smart-proxy.py"
    Start-Sleep -Seconds 1
}

$env:HTTP_PROXY  = "http://127.0.0.1:8889"
$env:HTTPS_PROXY = "http://127.0.0.1:8889"
Write-Host "[proxy] -> 127.0.0.1:8889 (auto-detect)" -ForegroundColor Green
Write-Host ""

# 如果带了参数，直接透传，跳过菜单
if ($args.Count -gt 0) {
    Write-Host "直接启动: claude $args" -ForegroundColor Yellow
    & claude @args
    return
}

# 无参数 -> 显示选择菜单
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
