# smart-proxy

Claude Code 智能代理 sidecar —— 自动检测 Windows 系统代理状态，动态切换直连/代理模式。

## 原理

```
Claude Code → 127.0.0.1:8889 (sidecar) → 系统代理开着？→ 走代理
                                        → 系统代理关了？→ 直连
```

每 3 秒重新检测一次，中途开关代理即时生效，无需重启 Claude Code。

## 快速开始

### 1. 克隆并配置启动脚本

将 `claude-with-proxy.ps1` 复制到你方便的位置（如 `C:\Users\<你的用户名>\claude.ps1`），修改其中的路径：

```powershell
# 改成你的 Claude Code 项目目录
Set-Location <你的项目目录>

# 改成你本地 Python 路径
Start-Process ... -FilePath "<Python路径>\python.exe" -ArgumentList "<本项目路径>\smart-proxy.py"
```

### 2. 使用

```powershell
.\claude.ps1
```

脚本自动完成：
- 检测并拉起 sidecar（如未运行）
- 注入代理环境变量指向 127.0.0.1:8889
- 显示三种启动模式菜单：

```
=== 选择启动模式 ===

  [1] claude                               安全模式，每次手动批准
  [2] claude --dangerously-skip-permissions  全自动跳过权限 (默认)
  [3] claude --dangerously-skip-permissions --resume  恢复会话 + 全自动
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
start "" "<本项目路径>\start-proxy.vbs"
```

## 文件

| 文件 | 说明 |
|------|------|
| `smart-proxy.py` | 核心 sidecar，监听 127.0.0.1:8889 |
| `claude-with-proxy.ps1` | Claude Code 启动脚本模板（需替换路径） |
| `start-proxy.vbs` | Windows 无窗口后台启动脚本 |
