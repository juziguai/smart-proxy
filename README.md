# smart-proxy

Claude Code 智能代理 sidecar —— 自动检测 Windows 系统代理状态，动态切换直连/代理模式。

## 背景

Claude Code CLI 通过 `HTTP_PROXY` / `HTTPS_PROXY` 环境变量决定是否走代理。但这个变量是**启动时一次性读取**的，运行中无法动态切换：

- 启动时开着代理 → 中途关掉 → 后续请求全挂，必须重启
- 启动时没开代理 → 中途想开 → 不会生效，必须重启

smart-proxy 在 Claude Code 和网络之间加了一层薄薄的本地代理（sidecar），每 3 秒重新检测一次 Windows 系统代理状态，**中途开关即时生效**。

## 完整流程图

```
                          ┌─── 启动阶段 ──────────────────────────┐
                          │                                        │
     .\claude.ps1 ────────┤                                        │
                          │  [1] cd 到项目目录                      │
                          │  [2] 检测 :8889 是否在 LISTENING        │
                          │      ├─ 是 → 跳过                      │
                          │      └─ 否 → 后台拉起 smart-proxy.py   │
                          │  [3] 设置 HTTP_PROXY = 127.0.0.1:8889  │
                          │  [4] 显示启动模式菜单                   │
                          │  [5] 启动 Claude Code                   │
                          └────────────────────────────────────────┘


                          ┌─── 运行时 ─────────────────────────────┐
                          │                                        │
     Claude Code ────────► 127.0.0.1:8889 (sidecar)               │
                          │    │                                   │
                          │    ├── 读注册表                         │
                          │    │   HKLM\...\Internet Settings       │
                          │    │   ┌─ ProxyEnable=0 → 直连 ────┐  │
                          │    │   └─ ProxyEnable=1 → 代理 ──┐  │  │
                          │    │                              │  │  │
                          │    ├── 直连 ─────────────────► api.deepseek.com
                          │    │                                     │
                          │    └── 代理 ──► 127.0.0.1:10090 ──► 出站│
                          │               (v2rayA / Clash / ...)     │
                          │                                          │
                          │    缓存 3 秒，下次请求重新检测           │
                          │    你中途开关代理 → 最多 3 秒自动切换    │
                          └──────────────────────────────────────────┘


                          ┌─── 数据路径 ───────────────────────────┐
                          │                                        │
     HTTPS 请求            sidecar 处理             最终         │
     (TLS 加密)            (不解密，纯转发)                       │
                          │                                        │
     ┌─────────┐          ┌──────────┐          ┌──────────┐     │
     │ ClientHello ──────►│ CONNECT  │─────────►│ ServerHello│     │
     │   密文1   ──────►  │ 隧道建立  │─────────►│   密文1   │     │
     │   密文2   ◄──────  │ 双向字节 │ ◄────────│   密文2   │     │
     └─────────┘          │   管道    │          └──────────┘     │
                          └──────────┘                             │
                                                                    │
     附加延迟: < 1ms（本地回环 + 一次注册表读取）                  │
                          └──────────────────────────────────────────┘
```

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

## 兼容性

- 不依赖特定代理软件。只要代理软件正确设置了 Windows 系统代理（注册表 `ProxyEnable` + `ProxyServer`），就能自动识别
- 已验证：v2rayA、Clash、SSR
- 仅 Windows（依赖注册表读取系统代理状态）

## 文件

| 文件 | 说明 |
|------|------|
| `smart-proxy.py` | 核心 sidecar，监听 127.0.0.1:8889 |
| `claude-with-proxy.ps1` | Claude Code 启动脚本模板（需替换路径） |
| `start-proxy.vbs` | Windows 无窗口后台启动脚本 |
