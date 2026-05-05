# 启动 Claude Code，HTTP_PROXY 指向智能代理 sidecar
# 用法: .\claude-with-proxy.ps1 [claude 参数...]

$env:HTTP_PROXY  = "http://127.0.0.1:8889"
$env:HTTPS_PROXY = "http://127.0.0.1:8889"

Write-Host "[claude launcher] proxy -> 127.0.0.1:8889" -ForegroundColor Green
& claude @args
