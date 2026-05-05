# smart-proxy

Claude Code 智能代理 sidecar —— 自动检测 Windows 系统代理状态，动态切换直连/代理模式。

## 原理

```
Claude Code → 127.0.0.1:8889 (sidecar) → 系统代理开着？→ 走代理
                                        → 系统代理关了？→ 直连
```

每 3 秒重新检测一次，中途开关代理即时生效，无需重启 Claude Code。

## 使用

### 1. 启动 sidecar

```powershell
start "" "D:\Tools\AI\Claude-code\smart-proxy\start-proxy.vbs"
```

或直接：

```powershell
python smart-proxy.py
```

### 2. 配置 Claude Code

将 `HTTP_PROXY` 和 `HTTPS_PROXY` 指向 `http://127.0.0.1:8889` 后启动 Claude Code。

## 文件

| 文件 | 说明 |
|------|------|
| `smart-proxy.py` | 核心 sidecar，监听 127.0.0.1:8889 |
| `start-proxy.vbs` | Windows 无窗口后台启动脚本 |
