# Changelog

## v0.6.0

真实 Token 统计与请求来源追溯闭环：

- 新增可选 MITM Token Capture sidecar，使用 `start-mitm-token-capture.ps1` 启动 `127.0.0.1:8891`，仅捕获模型 API 响应中的 `usage` 字段。
- Dashboard “今日 Token”切换为 `token-capture-*.jsonl` 数据源，旧 Claude transcript 读取器仅保留兼容，不再参与默认统计。
- Claude 启动脚本模板新增 MITM 偏好记忆，支持启用并记住、本次启用、本次不启用、不启用并记住，以及 `CLAUDE_MITM_TOKEN_CAPTURE=1/0/ask` 覆盖。
- Profiler 与请求统计补充 User-Agent、源端口、PID、进程链和 evidence，便于定位浏览器、Claude Code CLI、Codex、Cockpit Tools 等来源。
- 流量分析页补充来源筛选、异常请求提示、峰值连接数和更紧凑的 Claude Code 面板布局。
- Doctor 改为检查 MITM Token Capture 文件状态，并继续展示数据库、资源、上游代理和网络连通性诊断。

## v0.5.0

Claude Code CLI 流量识别和服务商归因增强：

- 新增进程链识别，结合 `Windows Terminal -> PowerShell/cmd -> claude.cmd -> bun/node -> cli.cjs` 判断 Claude Code CLI 来源。
- 新增服务商分类器和 `provider-rules.json` 配置入口，区分模型服务商、Other 流量和 Unknown Provider。
- `/api/recent-requests` 与 `/api/traffic-analytics` 补充 Provider、Client evidence、置信度和 Claude Code 专属统计。
- 流量分析页新增 Claude Code CLI 识别面板，展示进程拓扑、服务商占比、最近错误、未知 Host 建议和能力边界。
- 过滤无 Host 的客户端断连噪声，避免 `(unknown)` 请求污染 Claude Code 服务商占比和失败率。
- watchdog 健康检查改用本地监听端口判断，减少自探测连接对流量统计的干扰。

## v0.4.1

运行稳定性、守护唯一性和 Doctor 数据库诊断性能改进：

- watchdog 增强进程消失归因日志，记录 PID、父进程、端口归属、重启前后快照和启动捕获文件。
- Windows Service 成为唯一推荐守护入口；旧 Startup fallback 会被清理，手动重复启动 watchdog 会自动退出。
- Doctor 数据库检查拆分完整性、统计查询、写入测试耗时，并缓存完整性校验，避免每次打开全库扫描。
- 统计库补充 `started_at` 相关索引，主要时间窗口查询改为索引友好的范围过滤。

## v0.4.0

将 smart-proxy 从普通后台脚本升级为 Windows Service 兜底的本地网关：

- 新增 `SmartProxyWatchdog` Windows Service，开机自动守护 watchdog。
- watchdog 增加 Antigravity `127.0.0.1:443` TLS relay 自愈，避免 hosts 指向本机后 relay 掉线造成连接拒绝。
- Dashboard 新增流量分析接口和页面，可按软件进程、模型厂商查看请求占比。
- Claude 启动脚本模板新增服务管理入口：`-sps` 查看状态，`-spr` 重启服务。
