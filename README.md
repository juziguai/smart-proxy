# smart-proxy

<p align="center">
  <a href="https://github.com/juziguai/smart-proxy/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
  <a href="https://www.python.org"><img src="https://img.shields.io/badge/Python-3.8+-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.8+"></a>
  <a href="https://www.microsoft.com/windows"><img src="https://img.shields.io/badge/Platform-Windows-0078D6?style=for-the-badge&logo=windows&logoColor=white" alt="Windows"></a>
  <a href="https://github.com/juziguai/smart-proxy"><img src="https://img.shields.io/badge/Claude%20Code-Sidecar-FF6B35?style=for-the-badge" alt="Claude Code Sidecar"></a>
</p>

Claude Code 智能代理 sidecar —— 自动检测 Windows 系统代理状态，动态切换直连/代理模式。

## 背景

Claude Code CLI 通过 `HTTP_PROXY` / `HTTPS_PROXY` 环境变量决定是否走代理。但这个变量是**启动时一次性读取**的，运行中无法动态切换：

- 启动时开着代理 → 中途关掉 → 后续请求全挂，必须重启
- 启动时没开代理 → 中途想开 → 不会生效，必须重启

smart-proxy 在 Claude Code 和网络之间加了一层薄薄的本地代理（sidecar），每 3 秒重新检测一次 Windows 系统代理状态，**中途开关即时生效**。

## 特性

<table>
<tr><td><b>动态代理切换</b></td><td>每 3 秒检测系统代理状态，中途开关即时生效，无需重启 Claude Code</td></tr>
<tr><td><b>白名单直连</b></td><td>国内域名强制直连，跳过代理，延迟降低 10 倍+</td></tr>
<tr><td><b>零依赖</b></td><td>纯 Python 标准库，无需安装第三方包</td></tr>
<tr><td><b>透明转发</b></td><td>不解密 TLS，纯字节管道转发，附加延迟 < 1ms</td></tr>
<tr><td><b>自动守护</b></td><td>启动脚本自动检测并拉起 sidecar，无需手动管理</td></tr>
<tr><td><b>本地统计面板</b></td><td>内置 Web UI，展示请求、token、费用、趋势、Host 排名、最近请求和运行状态</td></tr>
<tr><td><b>兼容性强</b></td><td>不依赖特定代理软件，支持 v2rayA、Clash、SSR 等</td></tr>
</table>

## 前置要求

- **操作系统**: Windows 10/11
- **Python**: 3.8+（仅使用标准库，无第三方依赖）
- **代理软件**: 任意支持 Windows 系统代理的代理软件（v2rayA、Clash、SSR 等）

## 快速开始

### 1. 克隆并配置启动脚本

```powershell
git clone https://github.com/juziguai/smart-proxy.git
```

将 `claude-with-proxy.ps1` 复制到你方便的位置（如 `C:\Users\<用户名>\claude.ps1`），修改顶部三个路径：

```powershell
$CLAUDE_PROJECT_DIR = "<你的Claude Code项目目录>"
$PYTHON_PATH         = "<Python路径>\python.exe"
$SMART_PROXY_DIR     = "<smart-proxy项目目录>"
```

### 2. 使用

```powershell
.\claude.ps1
```

脚本自动完成 sidecar 守护 + 代理注入 + 启动模式选择：

```
=== 选择启动模式 ===

  [1] claude
      安全模式，每次操作需要你手动批准
      适用: 日常开发、代码审查、需要精细控制的场景

  [2] claude --dangerously-skip-permissions
      跳过所有权限提示，自动执行操作
      适用: 自动化任务、你完全信任当前工作目录

  [3] claude --dangerously-skip-permissions --resume
      恢复上次会话 + 跳过权限，继续未完成的工作
      适用: 中断后恢复，无需重新描述需求

输入序号 (1/2/3，默认 2)
```

也可以直接带参数跳过菜单：

```powershell
.\claude.ps1 --dangerously-skip-permissions --resume
```

### 3. 仅启动 sidecar（不启动 Claude Code）

```powershell
python smart-proxy.py
```

或通过 vbs 无窗口后台启动：

```powershell
start "" "<项目路径>\start-proxy.vbs"
```

## 完整流程图

```
┌────────────────────────────── 启动阶段 ──────────────────────────────┐
│                                                                       │
│  .\claude.ps1                                                         │
│       │                                                               │
│       ├─ 检查 127.0.0.1:8889 代理端口                                 │
│       ├─ 检查 127.0.0.1:8890 统计面板端口                              │
│       │     ├─ 两个端口都可用 → 跳过 sidecar 启动                      │
│       │     ├─ 8889 可用但 8890 不可用 → 提示停止旧版 sidecar           │
│       │     └─ 任意端口未运行 → 后台启动 smart-proxy.py                │
│       │                                                               │
│       ├─ 等待 dashboard API 返回 HTTP 200                              │
│       ├─ 写入日志 logs/smart-proxy.out.log / smart-proxy.err.log       │
│       ├─ 设置 HTTP_PROXY / HTTPS_PROXY = http://127.0.0.1:8889         │
│       ├─ 显示版本、模型提供商、启动模式菜单                            │
│       └─ 启动 Claude Code                                              │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘


┌────────────────────────────── 运行时链路 ─────────────────────────────┐
│                                                                       │
│  Claude Code                                                          │
│       │                                                               │
│       ├─ API 请求 ───────────────► 127.0.0.1:8889 (proxy sidecar)      │
│       │                                │                              │
│       │                                ├─ 提取 CONNECT target / Host   │
│       │                                ├─ 匹配 whitelist.txt           │
│       │                                │    ├─ 命中 → direct_whitelist │
│       │                                │    └─ 未命中 → 读系统代理     │
│       │                                │                              │
│       │                                ├─ ProxyEnable=0 → direct ─────► 目标服务器
│       │                                └─ ProxyEnable=1 → proxy ──────► 127.0.0.1:10808
│       │                                                               │   │
│       │                                                               │   └─► 出站代理 / 目标服务器
│       │                                                               │
│       └─ 本地 transcript JSONL ──► ~/.claude/projects/**/*.jsonl       │
│                                        │                              │
│                                        └─ usage_ingestion 后台扫描     │
│                                             input/output/cache/model   │
│                                                                       │
│  smart-proxy.py 同一进程内提供：                                        │
│       ├─ 127.0.0.1:8889  代理服务                                      │
│       └─ 127.0.0.1:8890  dashboard + stats/diagnostics API             │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘


┌────────────────────────────── 统计数据流 ─────────────────────────────┐
│                                                                       │
│  proxy request completed                                              │
│       ├─ 记录 method / host / route / success                            │
│       └─ 记录 connect_latency / duration                                 │
│                                                                       │
│  Claude transcript usage                                              │
│       └─ 读取 message.model + message.usage                            │
│          input_tokens / output_tokens                                  │
│          cache_read_input_tokens / cache_creation_input_tokens          │
│                                                                       │
│  SQLite: smart-proxy-stats.db                                          │
│       ├─ proxy_requests                                                │
│       └─ usage_events                                                  │
│                                                                       │
│  Dashboard: http://127.0.0.1:8890                                      │
│       ├─ 总请求数 / 成功率 / 平均建连 / 平均持续 / 路由拆分             │
│       ├─ 总 token / 输入 / 输出 / cache read / cache write             │
│       ├─ 模型榜单：按模型展示 total、input、output、cache read/write   │
│       ├─ 预估费用与 token/费用趋势图                                    │
│       └─ Host 统计 / 最近请求 / 当前代理运行状态                       │
│                                                                       │
│  说明：smart-proxy 不解密 HTTPS，不读取 API key；token 来自 Claude Code │
│  已写入本地 transcript 的 usage 字段。                                  │
│                                                                       │
└───────────────────────────────────────────────────────────────────────┘
```

## 白名单

可选功能。创建 `whitelist.txt` 文件（与 `smart-proxy.py` 同目录），一行一个域名，支持通配符（fnmatch 语法）：

```
*.baidu.com
*.taobao.com
*.bilibili.com
*.cn
localhost
127.0.0.1
```

- 文件不存在或为空 → 原流程不变
- 命中白名单 → 跳过注册表检测，强制直连
- 60 秒自动重载文件变更

### 测试验证

代理软件设为全局模式时的耗时对比：

| 域名 | 耗时 | 路径 |
|------|------|------|
| baidu.com | 169ms | 直连（白名单命中） |
| qq.com | 147ms | 直连（白名单命中） |
| douban.com | 2.3s | 代理（白名单未命中） |
| 36kr.com | 1.6s | 代理（白名单未命中） |
| google.com | 0.99s | 代理（国外） |

白名单命中直连约 0.15 秒，未命中走代理约 2 秒，差异 10 倍+。

## 本地统计面板

smart-proxy 会在同一个 Python 进程里同时启动两个本地服务：

| 服务 | 地址 | 说明 |
|------|------|------|
| 代理 sidecar | `http://127.0.0.1:8889` | Claude Code 的 `HTTP_PROXY` / `HTTPS_PROXY` 指向这里 |
| 统计面板 | `http://127.0.0.1:8890` | 本地 Web UI，展示请求统计和 Claude Code token 用量 |

通过 `claude-with-proxy.ps1` 启动时，脚本会先检查 `8889` 和 `8890` 是否已在监听：

- 两个端口都已运行：跳过启动，直接继续 Claude Code 启动流程
- 任意端口未运行：后台启动 `smart-proxy.py`
- 启动后复查端口，确认代理和 dashboard 都可用
- dashboard 可用时会打印 `http://127.0.0.1:8890`

统计面板包含：

- 控制台式页签结构：总览、Providers、Requests、Usage & Cost、Whitelist、Doctor
- 总请求数、成功/失败、成功率、平均建连耗时、平均持续时长
- 直连、白名单直连、上游代理的路由拆分
- 输入 token、输出 token、cache read、cache write
- 模型拆分榜单：按模型展示总 token、输入、输出、cache read、cache write
- API 预估费用：DeepSeek API 模型按官方价格估算，套餐模型标记为套餐内
- 趋势图：默认展示全部模型的 token 与预估费用走势，也支持多选模型后查看筛选后的趋势
- 运行告警：汇总当前范围内带错误原因的失败 Host、慢建连请求和严重程度；旧版无错误原因的 CONNECT 断流记录只保留在统计里，不触发告警
- 代理状态：当前 Windows 系统代理是否启用、上游代理地址、白名单路径、白名单条目数和加载时间
- Host 统计：按域名展示请求次数、成功/失败、失败率、慢建连数、平均建连耗时、平均持续时长和路由构成，方便定位慢建连或失败集中在哪个上游
- 最近请求：展示最新代理事件的 host、方法、路由、成功状态、建连耗时、持续时长和错误信息；失败请求和慢建连请求会高亮
- 编辑布局：打开后可拖动模块调整顺序，关闭后锁定页面；布局保存在当前浏览器本地，并支持恢复默认
- 日 / 周 / 月 / 全部范围切换
- 清除 smart-proxy 请求统计按钮

token 数据来自 Claude Code 本地 transcript：

```text
%USERPROFILE%\.claude\projects\**\*.jsonl
```

如果设置了 `CLAUDE_CONFIG_DIR`，则读取：

```text
%CLAUDE_CONFIG_DIR%\projects\**\*.jsonl
```

smart-proxy 不解密 HTTPS，也不会读取 API key。token 用量来自 Claude Code 已经写入本地 JSONL 的 `message.usage` 字段。清除按钮默认只清除 smart-proxy 自己记录的请求统计，不删除 Claude Code transcript。

启动脚本会把 sidecar 日志写入 `logs/smart-proxy.out.log` 和 `logs/smart-proxy.err.log`，并等待 dashboard API 返回 HTTP 200 后再继续启动 Claude Code，避免只看到端口监听但页面实际不可用。

### 费用估算

费用统计是本地预估值，不等同于服务商最终账单：

- `deepseek-v4-flash`、`deepseek-v4-pro`：按 DeepSeek 官方“模型 & 价格”页面的百万 tokens 单价估算。
- `MiniMax-*`、`mimo-*`：按 token plan 套餐处理，不计入 API 现金费用。
- 其他未知模型：保留 token 统计，但费用标记为未计价。
- 页面展示费用时四舍五入到 2 位小数；API 内部保留原始浮点估算值。

当前 DeepSeek 价格来源：

```text
https://api-docs.deepseek.com/zh-cn/quick_start/pricing/
```

DeepSeek Pro 页面价格包含截至 `2026-05-31 23:59` 的优惠价；如果官方价格调整，更新 `pricing.py` 即可。

## 配置

### 命令行参数

```powershell
python smart-proxy.py [OPTIONS]
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--port` | `8889` | 监听端口 |
| `--cache-sec` | `3` | 注册表缓存时间（秒） |
| `--whitelist-file` | `whitelist.txt` | 白名单文件路径 |
| `--whitelist-reload` | `60` | 白名单重载间隔（秒） |

### 环境变量

启动脚本自动设置以下环境变量：

```powershell
$env:HTTP_PROXY = "http://127.0.0.1:8889"
$env:HTTPS_PROXY = "http://127.0.0.1:8889"
```

## 工作原理

### 注册表检测

smart-proxy 读取 Windows 注册表中的系统代理设置：

```
HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings
├── ProxyEnable (DWORD): 0=直连, 1=代理
└── ProxyServer (STRING): 代理地址（如 127.0.0.1:10090）
```

结果缓存 3 秒，避免频繁注册表读取。

### CONNECT 隧道

HTTPS 请求通过 HTTP CONNECT 方法建立隧道：

1. 客户端发送 `CONNECT host:443 HTTP/1.1`
2. smart-proxy 根据白名单/注册表决定直连或转发
3. 建立双向字节管道，透明转发加密流量

### 白名单匹配

使用 Python `fnmatch` 模块进行通配符匹配：

- `*.baidu.com` 匹配 `www.baidu.com`、`map.baidu.com` 等
- `*.cn` 匹配所有 `.cn` 结尾的域名
- `localhost` 精确匹配

## 兼容性

- 不依赖特定代理软件。只要代理软件正确设置了 Windows 系统代理（注册表 `ProxyEnable` + `ProxyServer`），就能自动识别
- 已验证：v2rayA、Clash、SSR
- 仅 Windows（依赖注册表读取系统代理状态）

## 故障排除

### 端口被占用

```
OSError: [Errno 10048] Only one usage of each socket address
```

**解决**：检查 8889 端口是否已被占用：

```powershell
netstat -ano | findstr :8889
```

终止占用进程，或使用 `--port` 参数指定其他端口。

### 代理不生效

1. **检查 sidecar 是否运行**：
   ```powershell
   netstat -ano | findstr :8889
   ```

2. **检查环境变量**：
   ```powershell
   echo $env:HTTP_PROXY
   echo $env:HTTPS_PROXY
   ```

3. **检查系统代理状态**：
   ```powershell
   reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Internet Settings" /v ProxyEnable
   ```

### 白名单不生效

1. **检查白名单文件路径**：确保 `whitelist.txt` 与 `smart-proxy.py` 同目录

2. **检查域名格式**：
   - 错误：`baidu.com`（不会匹配 `www.baidu.com`）
   - 正确：`*.baidu.com`

3. **查看日志**：观察 sidecar 输出的 `-> direct (whitelist)` 日志

### Claude Code 无法启动

确保 Python 路径正确：

```powershell
python --version
```

如果使用虚拟环境，确保激活后再启动。

## 文件

| 文件 | 说明 |
|------|------|
| `smart-proxy.py` | 核心 sidecar，监听 127.0.0.1:8889 |
| `whitelist.txt` | 白名单域名配置（可选，一行一个） |
| `claude-with-proxy.ps1` | Claude Code 启动脚本模板（需替换路径） |
| `start-proxy.vbs` | Windows 无窗口后台启动脚本 |

## 贡献

欢迎一起完善 smart-proxy！不管是修 bug、加功能，还是适配其他系统，都欢迎 PR。

### 我们欢迎的贡献

- **适配其他系统**：macOS（networksetup）、Linux（gsettings/env）的代理检测
- **新功能**：规则引擎、日志增强、配置文件支持等
- **白名单扩充**：添加更多国内常见域名
- **文档完善**：使用示例、FAQ、翻译等
- **Bug 修复**：任何影响稳定性的问题

### 协作流程

1. **先开 Issue 讨论**：大改动先讨论方案，避免重复劳动
2. **Fork + 分支**：
   ```powershell
   git checkout -b feature/your-feature
   ```
3. **保持单一职责**：一个 PR 只做一件事，方便 review
4. **测试验证**：附上测试结果（截图或命令输出）
5. **提交 PR**：说明改了什么、为什么改、怎么测的

### 提交规范

```
feat: 新功能描述
fix: 修复 xxx 问题
docs: 更新文档
refactor: 重构 xxx
test: 添加测试
```

### 开发环境

```powershell
git clone https://github.com/juziguai/smart-proxy.git
cd smart-proxy

# 直接运行（无需安装依赖）
python smart-proxy.py

# 测试白名单
curl -x http://127.0.0.1:8889 https://www.baidu.com
```

### 代码规范

- 使用 Python 标准库，不引入第三方依赖
- 保持代码简洁，单个文件不超过 500 行
- 添加新功能时同步更新白名单示例
- 跨平台适配请放在独立文件（如 `proxy_macos.py`、`proxy_linux.py`）

## 许可证

MIT - 详见 [LICENSE](LICENSE)
