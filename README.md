# smart-proxy

Windows 本地智能代理 sidecar。

当前版本：`v0.5.0`

它固定监听 `127.0.0.1:8889`，由 Claude Code、Antigravity、Cockpit Tools 等客户端连接；请求进来后，smart-proxy 会按当前 Windows 系统代理状态、白名单和本地规则决定直连还是转发到上游代理。

同时提供一个本地 Dashboard：`http://127.0.0.1:8890`，用于查看连接、延迟、错误、用量和运行状态。

## 更新摘要

`v0.5.0` 聚焦 Claude Code CLI 流量识别和服务商归因：

- Claude Code CLI 增加进程链识别和证据展示，可区分 `bun/node/cli.cjs` 与普通脚本流量。
- 服务商识别抽出为 `provider-rules.json` 规则体系，支持 MiMo、DeepSeek、MiniMax、Anthropic、OpenAI、Google 等模型服务商。
- 流量分析页新增 Claude Code 专属面板，展示进程拓扑、服务商占比、错误、未知 Host 建议和能力边界。
- 统计口径过滤无 Host 断连噪声，避免 `(unknown)` 污染服务商占比和失败率。

完整版本历史见 [CHANGELOG.md](CHANGELOG.md)。

## 特性

- 自动读取 Windows 系统代理，运行中切换代理也能生效。
- 支持 `whitelist.txt`，命中域名直接连接，减少不必要代理绕路。
- 不解密 HTTPS，不读取 API key，只做 CONNECT/HTTP 透明转发。
- 记录本地连接统计、Host 状态、客户端来源和 Claude transcript 用量。
- 内置本地 Dashboard、流量分析和 Doctor 诊断页。
- 纯 Python 标准库实现，无第三方运行依赖。

## 快速开始

```powershell
git clone https://github.com/juziguai/smart-proxy.git
cd smart-proxy
python smart-proxy.py
```

启动后：

```text
代理入口:     http://127.0.0.1:8889
Dashboard:   http://127.0.0.1:8890
```

安装 Windows Service 守护：

```powershell
.\install-smart-proxy-service.ps1
```

服务状态与重启：

```powershell
.\install-smart-proxy-service.ps1 -Status
.\install-smart-proxy-service.ps1 -Restart
```

如果配合 Claude Code 启动脚本使用，把 [claude-with-proxy.ps1](claude-with-proxy.ps1) 复制到自己的用户目录，并按脚本顶部提示填写：

```powershell
$CLAUDE_PROJECT_DIR = "<你的 Claude Code 项目目录>"
$PYTHON_PATH         = "<Python 路径>\python.exe"
$SMART_PROXY_DIR     = "<smart-proxy 项目目录>"
```

然后运行：

```powershell
.\claude.ps1
```

脚本会自动拉起 smart-proxy，并设置：

```powershell
$env:HTTP_PROXY  = "http://127.0.0.1:8889"
$env:HTTPS_PROXY = "http://127.0.0.1:8889"
```

## 配置

默认配置可以通过环境变量覆盖：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SMART_PROXY_LISTEN_HOST` | `127.0.0.1` | 代理监听地址 |
| `SMART_PROXY_LISTEN_PORT` | `8889` | 代理监听端口 |
| `SMART_PROXY_DASHBOARD_HOST` | `127.0.0.1` | Dashboard 监听地址 |
| `SMART_PROXY_DASHBOARD_PORT` | `8890` | Dashboard 监听端口 |
| `SMART_PROXY_CACHE_SEC` | `3` | Windows 系统代理状态缓存秒数 |
| `SMART_PROXY_WHITELIST_FILE` | `whitelist.txt` | 白名单文件路径 |
| `SMART_PROXY_STATS_DB_FILE` | `smart-proxy-stats.db` | 本地统计数据库 |

也可以设置 `SMART_PROXY_CONFIG` 指向一个 JSON 配置文件。

## 白名单

`whitelist.txt` 是本地白名单配置文件。

示例：

```text
*.baidu.com
*.taobao.com
*.cn
localhost
127.0.0.1
```

命中白名单的 Host 会跳过系统代理检测，直接连接目标服务器。

## Dashboard

打开：

```text
http://127.0.0.1:8890
```

主要功能：

- 查看代理是否运行、上游代理是否可用。
- 查看请求数、成功率、延迟、错误和最近请求。
- 按 Host、客户端、模型查看统计。
- 按软件进程和模型厂商查看流量占比。
- 查看 Claude transcript 中的 token 用量和预估费用。
- 编辑本地白名单。
- 运行 Doctor 诊断。

## 项目结构

```text
smart-proxy/
│
├── README.md                          # 项目自述文件
├── AGENTS.md                          # 项目协作和隐私提交规则
├── .gitignore                         # Git 忽略规则
│
├── smart-proxy.py                     # 稳定启动入口
├── smart-proxy-service.py             # Windows Service 入口
├── claude-with-proxy.ps1              # Claude Code 启动脚本模板
├── setup.ps1                          # 新机器初始化辅助脚本
├── install-smart-proxy-service.ps1    # 安装和管理 Windows Service
├── install-smart-proxy-watchdog.ps1   # 安装 watchdog 守护脚本
├── smart-proxy-watchdog.ps1           # 本地常驻守护脚本
├── start-proxy.vbs                    # Windows 无窗口启动入口
│
├── smart_proxy/                       # 核心 Python 包
│   ├── proxy.py                       # 代理主流程和服务入口
│   ├── config.py                      # 配置读取与默认值
│   ├── windows_service.py             # Windows Service 宿主实现
│   ├── windows_network.py             # Windows 代理读取和进程归因
│   ├── whitelist.py                   # 白名单加载、匹配、保存
│   ├── stats_store.py                 # SQLite 统计数据层
│   ├── stats_server.py                # Dashboard API 服务
│   ├── claude_usage_reader.py         # Claude transcript 用量读取
│   ├── usage_ingestion.py             # 用量后台导入
│   └── pricing.py                     # 模型费用估算
│
├── web/                               # Dashboard 前端静态资源
│   ├── dashboard.html                 # 页面结构
│   ├── dashboard.css                  # 页面样式
│   └── dashboard.js                   # 页面交互和 API 请求
│
├── claude_usage_reader.py             # 兼容旧导入名
├── pricing.py                         # 兼容旧导入名
├── smart_proxy_config.py              # 兼容旧导入名
├── smart_proxy_whitelist.py           # 兼容旧导入名
├── stats_server.py                    # 兼容旧导入名
├── stats_store.py                     # 兼容旧导入名
└── usage_ingestion.py                 # 兼容旧导入名
```

## 故障排查

检查端口：

```powershell
netstat -ano | findstr :8889
netstat -ano | findstr :8890
```

检查代理环境变量：

```powershell
echo $env:HTTP_PROXY
echo $env:HTTPS_PROXY
```

检查 Windows 系统代理：

```powershell
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v ProxyEnable
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v ProxyServer
```

如果 Dashboard 打不开，先确认 `python smart-proxy.py` 是否仍在运行，再看本地 `logs/` 目录里的输出。
