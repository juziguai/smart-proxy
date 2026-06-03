"""Smart proxy sidecar — auto-detect Windows system proxy per request."""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import socket
import sys
import time
import uuid

START_TIME = time.time()

from smart_proxy.logger import profiler_logger, start_async_logging_listener, shutdown_async_logging

class LatencyTracker:
    """全链路高精度分层耗时与流量统计追踪器"""
    def __init__(self, request_id: str, host: str = ""):
        self.request_id = request_id
        self.host = host
        
        # 物理计时节点 (Epoch 秒级)
        self.t_start = time.time()
        self.t_t1_end = None
        self.t_t2_end = None
        self.t_t3_end = None
        
        # 数据流特征追踪
        self.last_client_write = None
        self.first_remote_read = None
        
        # 状态锁：用于防止粘包/分包引起的误重置
        self.is_waiting_for_first_token = True
        self.t4_logged = False
        
        # 传输字节量统计
        self.total_client_bytes = 0
        self.total_remote_bytes = 0
        
    def reset_for_next_keepalive(self):
        """当长连接复用并检测到流向反转时，自适应重置状态机，开启全新交互轮次"""
        self.first_remote_read = None
        self.is_waiting_for_first_token = True
        self.t4_logged = False


from smart_proxy.claude_usage_reader import ClaudeUsageReader
from smart_proxy.config import DEFAULT_CONFIG
from smart_proxy.whitelist import Whitelist, WhitelistProvider, Blocklist, BlocklistProvider
from smart_proxy.stats_store import ProxyRequestEvent, StatsStore
from smart_proxy.stats_server import (
    DASHBOARD_HOST,
    DASHBOARD_PORT,
    start_stats_server_with_status,
)
from smart_proxy.usage_ingestion import run_usage_ingestion_loop
from smart_proxy.windows_network import Cache, get_client_peer, resolve_client_process

LISTEN_HOST = DEFAULT_CONFIG.listen_host
LISTEN_PORT = DEFAULT_CONFIG.listen_port
CACHE_SEC = DEFAULT_CONFIG.cache_sec
READ_SIZE = DEFAULT_CONFIG.read_size
PROVIDER_HEALTH_PATH = DEFAULT_CONFIG.provider_health_path
UNKNOWN_HOST = "(unknown)"
UNPARSED_ROUTE = "unparsed"


@dataclass(frozen=True)
class ForwardResult:
    success: bool
    connect_latency_ms: int | None = None
    error: str | None = None
    stage: str | None = None


proxy_cache = Cache(CACHE_SEC)
DOCTOR_DB_INTEGRITY_CACHE = {
    "checked_at": 0.0,
    "db_mtime": None,
    "db_size": None,
    "integrity": None,
    "integrity_ms": None,
}
DOCTOR_DB_INTEGRITY_CACHE_SECONDS = 30 * 60

# ── whitelist ────────────────────────────────────────────────────────

WHITELIST_FILE = DEFAULT_CONFIG.whitelist_file
WHITELIST_RELOAD_SEC = DEFAULT_CONFIG.whitelist_reload_sec
BLOCKLIST_FILE = DEFAULT_CONFIG.blocklist_file
BLOCKLIST_RELOAD_SEC = DEFAULT_CONFIG.blocklist_reload_sec
STATS_DB_FILE = DEFAULT_CONFIG.stats_db_file
stats_store = None


whitelist = Whitelist(WHITELIST_FILE, WHITELIST_RELOAD_SEC)
blocklist = Blocklist(BLOCKLIST_FILE, BLOCKLIST_RELOAD_SEC)


def _get_self_memory_win():
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        
        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ('cb', wintypes.DWORD),
                ('PageFaultCount', wintypes.DWORD),
                ('PeakWorkingSetSize', ctypes.c_size_t),
                ('WorkingSetSize', ctypes.c_size_t),
                ('QuotaPeakWorkingSetSize', ctypes.c_size_t),
                ('QuotaWorkingSetSize', ctypes.c_size_t),
                ('QuotaPeakPagedPoolSize', ctypes.c_size_t),
                ('QuotaPagedPoolSize', ctypes.c_size_t),
                ('PeakPagefileUsage', ctypes.c_size_t),
                ('PagefileUsage', ctypes.c_size_t),
            ]
            
        GetProcessMemoryInfo = ctypes.windll.psapi.GetProcessMemoryInfo
        GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
            wintypes.DWORD,
        ]
        GetProcessMemoryInfo.restype = wintypes.BOOL
        GetCurrentProcess = ctypes.windll.kernel32.GetCurrentProcess
        GetCurrentProcess.restype = wintypes.HANDLE
        
        process_handle = GetCurrentProcess()
        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        
        if GetProcessMemoryInfo(process_handle, ctypes.byref(counters), counters.cb):
            return counters.WorkingSetSize
    except Exception:
        pass
    return None


async def _probe_link_async(host, port, use_proxy=False):
    """
    高保真链路异步探测探针：
    - use_proxy=True 时，若有上游代理开启，通过发送 CONNECT 代理协议实现高保真握手与全链路延迟测速。
    - use_proxy=False 时执行国内直连 TCP 握手。
    - 返回 (status_ok, elapsed_ms, error_msg)
    """
    upstream = proxy_cache.get()
    started = time.monotonic()
    
    try:
        if use_proxy and upstream:
            # 1. 异步建连到本地上游代理 (mihomo)
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(upstream[0], upstream[1]),
                timeout=1.5
            )
            # 2. 发送 CONNECT 报文打通隧道
            connect_req = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n"
            writer.write(connect_req.encode("latin-1"))
            await writer.drain()
            
            # 3. 读取响应首行
            resp_line = await reader.readline()
            if b"200" not in resp_line:
                writer.close()
                await writer.wait_closed()
                return False, elapsed_ms(started), f"代理连接失败: {resp_line.decode().strip()}"
                
            # 4. 冲刷完 HTTP 响应头部
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                    
            writer.close()
            await writer.wait_closed()
            return True, elapsed_ms(started), ""
        else:
            # 5. 国内直连探测
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=1.5
            )
            writer.close()
            await writer.wait_closed()
            return True, elapsed_ms(started), ""
            
    except asyncio.TimeoutError:
        return False, elapsed_ms(started), "连接超时 (1.5秒)"
    except Exception as e:
        return False, elapsed_ms(started), str(e)


_probe_cache = {}
PROBE_CACHE_TTL = 30.0


async def _get_probe_result(key, host, port, use_proxy=False):
    """带 30s 内存缓存的网络链路测速包装器，降低高频探测开销"""
    now = time.time()
    cached = _probe_cache.get(key)
    if cached and (now - cached["ts"]) < PROBE_CACHE_TTL:
        return cached["ok"], cached["ms"], cached["err"], True
        
    ok, ms, err = await _probe_link_async(host, port, use_proxy=use_proxy)
    _probe_cache[key] = {
        "ok": ok,
        "ms": ms,
        "err": err,
        "ts": now
    }
    return ok, ms, err, False


def _check_socket(host, port, timeout=0.35):
    started = time.monotonic()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True, elapsed_ms(started), ""
    except OSError as exc:
        return False, elapsed_ms(started), str(exc)


def _doctor_item(key, label, ok, detail, fix="", status=None, data=None, actions=None):
    if status is None:
        status = "ok" if ok else "warning"
    item = {
        "key": key,
        "label": label,
        "status": status,
        "detail": detail,
        "fix": fix,
    }
    if data is not None:
        item["data"] = data
    if actions is not None:
        item["actions"] = actions
    return item


def build_provider_health_doctor_item(path=PROVIDER_HEALTH_PATH):
    path = Path(path)
    if not path.exists():
        return _doctor_item(
            "provider_health",
            "Provider quota / health",
            True,
            "No provider health check has been recorded yet.",
            "Run claude.ps1 once so the launcher can probe the selected provider.",
        )

    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return _doctor_item(
            "provider_health",
            "Provider quota / health",
            False,
            f"Could not read provider health file: {exc}",
            "Delete logs/provider-health.json and run claude.ps1 again.",
        )

    label = payload.get("label") or "selected provider"
    status = payload.get("status") or "unknown"
    detail = payload.get("detail") or status
    checked_at = payload.get("checked_at")
    suffix = f" ({checked_at})" if checked_at else ""
    ok = bool(payload.get("ok"))
    fix = (
        ""
        if ok
        else "If this says quota exhausted or HTTP 429, renew the provider plan or switch models in claude.ps1."
    )
    return _doctor_item(
        "provider_health",
        "Provider quota / health",
        ok,
        f"{label}: {detail}{suffix}",
        fix,
    )


def build_provider_health_report(path=PROVIDER_HEALTH_PATH):
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "check": build_provider_health_doctor_item(path),
    }


async def build_doctor_report():
    upstream = proxy_cache.get()
    whitelist.refresh_if_needed()
    reader = ClaudeUsageReader()
    projects_dir = reader.projects_dir
    transcript_files = []
    if projects_dir.exists():
        transcript_files = list(projects_dir.rglob("*.jsonl"))[:200]

    # 1. 基础端口诊断
    proxy_ok, proxy_ms, proxy_error = _check_socket(LISTEN_HOST, LISTEN_PORT)
    dashboard_ok, dashboard_ms, dashboard_error = _check_socket(
        DASHBOARD_HOST,
        DASHBOARD_PORT,
    )
    upstream_ok = True
    upstream_detail = "当前系统代理未启用，Smart Proxy 将直连上游。"
    if upstream:
        upstream_ok, upstream_ms, upstream_error = _check_socket(
            upstream[0],
            upstream[1],
        )
        upstream_detail = (
            f"{upstream[0]}:{upstream[1]} 可连接，耗时 {upstream_ms}ms"
            if upstream_ok
            else f"{upstream[0]}:{upstream[1]} 不可连接：{upstream_error}"
        )

    # 2. 屏蔽名单诊断
    blocklist_ok = True
    blocklist_msg = ""
    blocklist_advice = "如果文件不存在，可在 Blocklist 页保存一次自动创建。"
    try:
        blocklist.refresh_if_needed()
        if not os.path.exists(blocklist.path):
            blocklist_ok = False
            blocklist_msg = "屏蔽名单配置文件 blocklist.txt 不存在"
            blocklist_advice = "请在 Dashboard 的 Whitelist/Blocklist 页面中保存一次以自动创建 blocklist.txt。"
        else:
            with open(blocklist.path, "r", encoding="utf-8") as f:
                content = f.read()
            if not content.strip():
                blocklist_msg = f"{blocklist.path} 存在但内容为空 (已解析加载 0 条规则)"
            else:
                blocklist_msg = f"{blocklist.path}，当前已解析加载 {blocklist.pattern_count} 条屏蔽规则"
    except Exception as e:
        blocklist_ok = False
        blocklist_msg = f"屏蔽名单读取失败: {str(e)}"
        blocklist_advice = "请检查 blocklist.txt 的读取权限或编码格式。"

    # 3. 异步网络探测 (带 30s 内存缓存的异步探针)
    baidu_task = _get_probe_result("baidu", "baidu.com", 80, use_proxy=False)
    anthropic_task = _get_probe_result("anthropic", "api.anthropic.com", 443, use_proxy=True)
    openai_task = _get_probe_result("openai", "api.openai.com", 443, use_proxy=True)
    
    baidu_res, anthropic_res, openai_res = await asyncio.gather(
        baidu_task, anthropic_task, openai_task
    )
    
    baidu_ok, baidu_ms, baidu_err, baidu_cached = baidu_res
    anthropic_ok, anthropic_ms, anthropic_err, anthropic_cached = anthropic_res
    openai_ok, openai_ms, openai_err, openai_cached = openai_res

    # 4. SQLite 遥测数据库物理健康度与 I/O 压测
    db_ok = True
    db_status = "ok"
    db_msg = ""
    db_advice = "如果读写缓慢，建议清理历史统计或优化磁盘剩余空间。"
    db_data = None
    db_actions = None
    try:
        import sqlite3
        db_path = Path(STATS_DB_FILE)
        if not db_path.exists():
            db_ok = False
            db_status = "error"
            db_msg = "数据库文件 smart-proxy-stats.db 不存在"
            db_advice = "请重新启动代理服务，它会自动在初始化时创建该数据库。"
        else:
            db_size_mb = db_path.stat().st_size / (1024 * 1024)
            
            total_check_start = time.time()
            conn = sqlite3.connect(db_path, timeout=2.0)
            cursor = conn.cursor()
            
            cache_age = total_check_start - float(
                DOCTOR_DB_INTEGRITY_CACHE.get("checked_at") or 0.0
            )
            cache_valid = (
                DOCTOR_DB_INTEGRITY_CACHE.get("integrity") is not None
                and cache_age < DOCTOR_DB_INTEGRITY_CACHE_SECONDS
            )
            if cache_valid:
                integrity = DOCTOR_DB_INTEGRITY_CACHE["integrity"]
                integrity_ms = int(DOCTOR_DB_INTEGRITY_CACHE["integrity_ms"] or 0)
                integrity_cached = True
            else:
                integrity_start = time.time()
                cursor.execute("PRAGMA integrity_check;")
                integrity = cursor.fetchone()[0]
                integrity_ms = int((time.time() - integrity_start) * 1000)
                DOCTOR_DB_INTEGRITY_CACHE.update(
                    {
                        "checked_at": time.time(),
                        "db_mtime": db_path.stat().st_mtime,
                        "db_size": db_path.stat().st_size,
                        "integrity": integrity,
                        "integrity_ms": integrity_ms,
                    }
                )
                integrity_cached = False
            if integrity != "ok":
                raise ValueError(f"SQLite 完整性校验失败: {integrity}")
                
            query_start = time.time()
            cursor.execute("SELECT COUNT(*) FROM proxy_requests;")
            total_requests = cursor.fetchone()[0]
            cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM proxy_requests
                WHERE started_at >= ?
                """,
                (cutoff_iso,),
            )
            retained_requests = cursor.fetchone()[0]
            query_ms = int((time.time() - query_start) * 1000)
            
            write_start = time.time()
            try:
                cursor.execute("BEGIN IMMEDIATE;")
                cursor.execute("CREATE TABLE IF NOT EXISTS __doctor_io_test (id INTEGER PRIMARY KEY, val TEXT);")
                cursor.execute("INSERT INTO __doctor_io_test (val) VALUES ('diagnose');")
                cursor.execute("SELECT val FROM __doctor_io_test WHERE val = 'diagnose';")
                cursor.execute("DROP TABLE __doctor_io_test;")
                conn.rollback()
            except Exception:
                conn.rollback()
                raise
            write_ms = int((time.time() - write_start) * 1000)
            conn.close()
            
            check_ms = int((time.time() - total_check_start) * 1000)
            old_requests = max(0, total_requests - retained_requests)
            integrity_text = (
                f"{integrity_ms}ms"
                if not integrity_cached
                else f"{integrity_ms}ms(缓存)"
            )
            db_msg = (
                f"连接正常 · 完整性 {integrity} · 累计请求 {total_requests} 条 "
                f"· 大小 {db_size_mb:.2f} MB · 健康检查 {check_ms}ms "
                f"(完整性 {integrity_text} / 统计 {query_ms}ms / 写入 {write_ms}ms)"
            )
            db_data = {
                "integrity": integrity,
                "total_requests": total_requests,
                "retained_requests_7d": retained_requests,
                "prunable_requests_7d": old_requests,
                "size_mb": round(db_size_mb, 2),
                "io_ms": check_ms,
                "check_ms": check_ms,
                "integrity_ms": integrity_ms,
                "integrity_cached": integrity_cached,
                "query_ms": query_ms,
                "write_ms": write_ms,
                "retention_days": 7,
            }
            db_actions = [
                {
                    "id": "prune_proxy_stats",
                    "label": "保留最近7天并压缩",
                    "method": "POST",
                    "url": "/api/prune-proxy-stats",
                }
            ]
            
            if write_ms > 100:
                db_status = "warning"
                db_msg += " (写入测试较慢，可能存在磁盘写入瓶颈)"
            elif query_ms > 500:
                db_status = "warning"
                db_msg += " (统计查询较慢，建议检查索引或清理历史统计)"
            elif check_ms > 1000:
                db_status = "warning"
                db_msg += " (健康检查耗时较高，完整性校验可能正在全库扫描)"
    except Exception as e:
        db_ok = False
        db_status = "error"
        db_msg = f"数据库异常: {str(e)}"

    # 5. 注册表与环境变量冲突检测
    sys_ok = True
    sys_msg = ""
    sys_advice = "若无需系统全局代理，建议在系统设置中关闭代理或清理相关环境变量。"
    sys_status = "ok"
    
    reg_enabled, reg_server, reg_override = False, "", ""
    is_win = sys.platform == "win32"
    if is_win:
        try:
            import winreg
            reg_key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
            )
            proxy_enable, _ = winreg.QueryValueEx(reg_key, "ProxyEnable")
            proxy_server, _ = winreg.QueryValueEx(reg_key, "ProxyServer")
            proxy_override, _ = winreg.QueryValueEx(reg_key, "ProxyOverride")
            winreg.CloseKey(reg_key)
            reg_enabled = bool(proxy_enable)
            reg_server = str(proxy_server or "")
            reg_override = str(proxy_override or "")
        except Exception as e:
            reg_override = f"读取失败: {str(e)}"
            
    http_proxy = os.environ.get("HTTP_PROXY", "")
    https_proxy = os.environ.get("HTTPS_PROXY", "")
    
    details = []
    issues = []
    
    if reg_enabled:
        details.append(f"注册表全局代理: 已启用 (地址: {reg_server})")
        if f":{LISTEN_PORT}" in reg_server:
            issues.append(f"检测到环路风险：系统全局代理指向了 Smart Proxy 本身端口 {LISTEN_PORT}，这可能会导致请求死循环。")
            sys_advice = "建议将系统代理服务器地址修改为上游内核端口 (如 10090)，或在 Smart Proxy 运行时不要将自身设为系统全局代理。"
            sys_status = "warning"
    else:
        details.append("注册表全局代理: 已关闭")
        
    if http_proxy:
        details.append(f"HTTP_PROXY: {http_proxy}")
        if not http_proxy.startswith("http://") and not http_proxy.startswith("https://"):
            issues.append("环境变量 HTTP_PROXY 格式不规范 (缺失 http:// 协议头)")
            sys_status = "warning"
    if https_proxy:
        details.append(f"HTTPS_PROXY: {https_proxy}")
        if not https_proxy.startswith("http://") and not https_proxy.startswith("https://"):
            issues.append("环境变量 HTTPS_PROXY 格式不规范 (缺失 https:// 协议头)")
            sys_status = "warning"
            
    if issues:
        sys_msg = " | ".join(details) + " ⚠️ 潜在冲突: " + " & ".join(issues)
    else:
        sys_msg = " | ".join(details) + " (无环境变量及注册表冲突)"

    # 6. 守护资源监控 (Uptime, Memory RSS, asyncio tasks)
    uptime_sec = time.time() - START_TIME
    days = int(uptime_sec // 86400)
    hours = int((uptime_sec % 86400) // 3600)
    minutes = int((uptime_sec % 3600) // 60)
    
    if days > 0:
        uptime_str = f"{days}天 {hours}小时 {minutes}分钟"
    elif hours > 0:
        uptime_str = f"{hours}小时 {minutes}分钟"
    else:
        uptime_str = f"{int(uptime_sec)}秒"
        
    tasks_count = len(asyncio.all_tasks())
    mem_bytes = _get_self_memory_win()
    mem_mb = (mem_bytes / (1024 * 1024)) if mem_bytes else None
    mem_text = f"{mem_mb:.1f} MB" if mem_mb is not None else "未知"
    
    resource_status = "ok"
    resource_msg = f"在线时长 {uptime_str} · 内存占用 {mem_text} · 活跃协程 {tasks_count} 个"
    resource_advice = "正常运行中。若协程数或内存超标，建议择机重启代理服务。"
    
    if (mem_mb is not None and mem_mb > 200.0) or tasks_count > 500:
        resource_status = "warning"
        resource_msg += " (资源占用过高，建议择机重启代理以释放资源)"

    checks = [
        _doctor_item(
            "proxy_port",
            "Proxy 端口",
            proxy_ok,
            (
                f"{LISTEN_HOST}:{LISTEN_PORT} 正在监听，连接 {proxy_ms}ms"
                if proxy_ok
                else f"{LISTEN_HOST}:{LISTEN_PORT} 未连通：{proxy_error}"
            ),
            "确认 smart-proxy.py 正在运行，且端口未被旧进程占用。",
        ),
        _doctor_item(
            "dashboard_port",
            "Dashboard 端口",
            dashboard_ok,
            (
                f"{DASHBOARD_HOST}:{DASHBOARD_PORT} 正在监听，连接 {dashboard_ms}ms"
                if dashboard_ok
                else f"{DASHBOARD_HOST}:{DASHBOARD_PORT} 未连通：{dashboard_error}"
            ),
            "重新运行 claude.ps1 或检查 8890 端口占用。",
        ),
        _doctor_item(
            "python",
            "Python 路径",
            bool(sys.executable and os.path.exists(sys.executable)),
            sys.executable or "未检测到 Python executable",
            "确认脚本使用的 Python 可执行文件存在。",
        ),
        _doctor_item(
            "transcripts",
            "Claude transcript",
            projects_dir.exists() and len(transcript_files) > 0,
            f"{projects_dir}，已发现 {len(transcript_files)} 个 transcript 文件",
            "如果这里为 0，确认 CLAUDE_CONFIG_DIR 或 ~/.claude/projects 路径。",
        ),
        _doctor_item(
            "whitelist",
            "白名单",
            os.path.exists(whitelist.path),
            f"{whitelist.path}，当前 {whitelist.pattern_count} 条",
            "如果文件不存在，可在 Whitelist 页保存一次自动创建。",
        ),
        _doctor_item(
            "blocklist",
            "屏蔽名单",
            blocklist_ok,
            blocklist_msg,
            blocklist_advice,
            status="ok" if blocklist_ok else "error",
        ),
        _doctor_item(
            "upstream",
            "系统代理 / 上游代理",
            upstream_ok,
            upstream_detail,
            "检查 Windows 系统代理设置或上游代理进程。",
        ),
        _doctor_item(
            "net_baidu",
            "公网连通性 (国内直连)",
            baidu_ok,
            (
                f"Baidu 可达，耗时 {baidu_ms}ms" + (" (已缓存)" if baidu_cached else "")
                if baidu_ok
                else f"Baidu 连接失败: {baidu_err}"
            ),
            "请检查本地物理宽带或网线连接是否正常。",
            status="ok" if baidu_ok else "error",
        ),
        _doctor_item(
            "net_anthropic",
            "Anthropic 可达性 (Claude 链路)",
            anthropic_ok,
            (
                f"Claude 链路畅通，已通过代理握手连通，耗时 {anthropic_ms}ms" + (" (已缓存)" if anthropic_cached else "")
                if anthropic_ok
                else f"连接超时: {anthropic_err}"
            ),
            "确认本地代理软件(mihomo)是否正常工作、机场节点是否可用。",
            status="ok" if anthropic_ok else "error",
        ),
        _doctor_item(
            "net_openai",
            "OpenAI 可达性 (ChatGPT 链路)",
            openai_ok,
            (
                f"ChatGPT 链路畅通，已通过代理握手连通，耗时 {openai_ms}ms" + (" (已缓存)" if openai_cached else "")
                if openai_ok
                else f"连接超时: {openai_err}"
            ),
            "确认机场节点是否处于高可用状态或全局代理设置。",
            status="ok" if openai_ok else "error",
        ),
        _doctor_item(
            "database",
            "遥测数据库健康度",
            db_ok,
            db_msg,
            db_advice,
            status=db_status,
            data=db_data,
            actions=db_actions,
        ),
        _doctor_item(
            "env_proxy",
            "系统代理与冲突检测",
            sys_ok,
            sys_msg,
            sys_advice,
            status=sys_status,
        ),
        _doctor_item(
            "resources",
            "守护进程资源诊断",
            True,
            resource_msg,
            resource_advice,
            status=resource_status,
        ),
    ]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }


def build_runtime_status():
    upstream = proxy_cache.get()
    whitelist.refresh_if_needed()
    return {
        "proxy_enabled": upstream is not None,
        "upstream_proxy": (
            f"{upstream[0]}:{upstream[1]}" if upstream else ""
        ),
        "whitelist_count": whitelist.pattern_count,
        "whitelist_path": whitelist.path,
        "whitelist_loaded_at": whitelist.loaded_at,
    }


def extract_host(target, headers_data):
    """Extract hostname from CONNECT target or Host header."""
    # CONNECT: target is "host:port"
    if ":" in target:
        host = target.rsplit(":", 1)[0]
    else:
        host = target
    # plain HTTP: fallback to Host header
    if not host:
        for line in headers_data.split(b"\r\n"):
            if line.lower().startswith(b"host:"):
                host = line.split(b":", 1)[1].strip().decode()
                host, _, _ = host.partition(":")
                break
    return host


def extract_target_port(method, target):
    if method == "CONNECT":
        _host, _sep, port = target.rpartition(":")
        try:
            return int(port)
        except (TypeError, ValueError):
            return None
    return 80


def log(msg):
    if sys.stdout.isatty():
        ts = time.strftime("%H:%M:%S")
        print(f"[sp {ts}] {msg}", flush=True)



async def relay(src_reader, dst_writer):
    """单向转发 src -> dst，连接断开时返回."""
    try:
        while True:
            data = await src_reader.read(READ_SIZE)
            if not data:
                break
            dst_writer.write(data)
            await dst_writer.drain()
    except Exception:
        pass


def print_profiler_report(tracker):
    t_end = time.time()
    t_total = t_end - tracker.t_start
    t1_ms = int((tracker.t_t1_end - tracker.t_start) * 1000) if tracker.t_t1_end else 0
    t2_ms = int((tracker.t_t2_end - tracker.t_t1_end) * 1000) if (tracker.t_t2_end and tracker.t_t1_end) else 0
    t3_ms = int((tracker.t_t3_end - tracker.t_t2_end) * 1000) if (tracker.t_t3_end and tracker.t_t2_end) else 0
    
    t4 = 0.0
    if tracker.first_remote_read and tracker.last_client_write:
        t4 = tracker.first_remote_read - tracker.last_client_write
    elif tracker.first_remote_read:
        t4 = tracker.first_remote_read - tracker.t_start

    t5 = 0.0
    if tracker.first_remote_read:
        t5 = t_end - tracker.first_remote_read
        
    def format_bytes(b):
        if b < 1024:
            return f"{b} B"
        elif b < 1024 * 1024:
            return f"{b / 1024:.2f} KB"
        else:
            return f"{b / 1024 / 1024:.2f} MB"
            
    c_bytes_str = format_bytes(tracker.total_client_bytes)
    r_bytes_str = format_bytes(tracker.total_remote_bytes)
    
    profiler_logger.info(
        f"\n"
        f"┌─── [ProxyProfiler] [Req #{tracker.request_id}] 全链路分层耗时报告 (Host: {tracker.host}) ───\n"
        f"│  - T1 (本地中继耗时): {t1_ms} ms\n"
        f"│  - T2 (本地代理耗时): {t2_ms} ms\n"
        f"│  - T3 (公网建连耗时): {t3_ms} ms\n"
        f"│  - T4 (云端首包思考): {t4:.3f} s (TTFT)\n"
        f"│  - T5 (流式数据传输): {t5:.3f} s\n"
        f"│  - 链条全程总耗时  : {t_total:.3f} s\n"
        f"│  - 物理流量交互统计: 客户端发送 {c_bytes_str} | 远程返回 {r_bytes_str}\n"
        f"└──────────────────────────────────────────────────────────────────────────"
    )


async def relay_client_to_remote(src_reader, dst_writer, tracker):
    """从客户端读取并转发到远程（C ➔ R），记录最后写入并检测 Keep-Alive 交互流反转"""
    try:
        while True:
            data = await src_reader.read(READ_SIZE)
            if not data:
                break
            
            now = time.time()
            # 老大建议一：状态锁。如果上一轮响应已经完成了，这时再次检测到 client 写入，说明是长连接 Keep-Alive 下的一轮新交互
            if not tracker.is_waiting_for_first_token:
                tracker.reset_for_next_keepalive()
                
            tracker.last_client_write = now
            tracker.total_client_bytes += len(data)
            
            dst_writer.write(data)
            await dst_writer.drain()
    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, asyncio.CancelledError):
        # 预期内的 TCP 主动断开异常，静默处理，防止垃圾红字刷屏
        pass
    except Exception as exc:
        profiler_logger.debug(f"[{tracker.request_id}] Client-to-remote error: {exc}")


async def relay_remote_to_client(src_reader, dst_writer, tracker):
    """从远程读取并转发到客户端（R ➔ C），精确测定 T4 首包延迟和 T5 流式耗时"""
    try:
        while True:
            data = await src_reader.read(READ_SIZE)
            if not data:
                break
            
            now = time.time()
            tracker.total_remote_bytes += len(data)
            
            if tracker.is_waiting_for_first_token:
                tracker.first_remote_read = now
                tracker.is_waiting_for_first_token = False
                
                # 计算 TTFT (T4)
                if tracker.last_client_write is not None:
                    t4 = now - tracker.last_client_write
                else:
                    t4 = now - tracker.t_start
                    
                if not tracker.t4_logged:
                    profiler_logger.info(
                        f"[{tracker.request_id}] T4 (TTFT - 云端首包思考耗时): {t4:.3f}s (Host: {tracker.host})"
                    )
                    tracker.t4_logged = True
            
            dst_writer.write(data)
            await dst_writer.drain()
    except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, asyncio.CancelledError):
        pass
    except Exception as exc:
        profiler_logger.debug(f"[{tracker.request_id}] Remote-to-client error: {exc}")



def _enable_nodelay(writer):
    """Enable TCP_NODELAY when the transport exposes a socket."""
    try:
        sock = writer.get_extra_info("socket")
        if sock:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except Exception as exc:
        profiler_logger.debug(f"TCP_NODELAY setup skipped: {exc}")


async def connect_to(host, port, timeout=5):
    r, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    _enable_nodelay(w)
    return r, w



def elapsed_ms(start_monotonic):
    return int((time.monotonic() - start_monotonic) * 1000)


def classify_tunnel_close(tracker, task_c2r=None, task_r2c=None):
    if tracker.total_remote_bytes > 0:
        return "tunnel_closed", None
    if task_r2c is not None and task_r2c.done():
        return "remote_closed_empty", "remote closed without response bytes"
    if task_c2r is not None and task_c2r.done():
        return "client_closed_after_request", "client closed before remote response bytes"
    return "remote_closed_empty", "tunnel closed without response bytes"


# ── CONNECT (TLS tunnel) ──────────────────────────────────────────────

async def connect_direct_tunnel(client_r, client_w, target, tracker=None):
    if tracker is None:
        tracker = LatencyTracker(str(uuid.uuid4())[:8], target)
    host, _, port = target.rpartition(":")
    tracker.t_t1_end = time.time()
    
    connect_started = time.time()
    try:
        rmt_r, rmt_w = await connect_to(host, int(port))
    except Exception as exc:
        connect_latency_ms = int((time.time() - connect_started) * 1000)
        safe_write(client_w, b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        try:
            client_w.close()
            await client_w.wait_closed()
        except Exception:
            pass
        return ForwardResult(
            success=False,
            connect_latency_ms=connect_latency_ms,
            error=str(exc),
        )
        
    tracker.t_t2_end = time.time()
    tracker.t_t3_end = time.time()
    connect_latency_ms = int((time.time() - connect_started) * 1000)
    
    safe_write(client_w, b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await client_w.drain()
    
    task_c2r = asyncio.create_task(relay_client_to_remote(client_r, rmt_w, tracker))
    task_r2c = asyncio.create_task(relay_remote_to_client(rmt_r, client_w, tracker))
    close_stage = "tunnel_closed"
    close_error = None
    
    try:
        done, pending = await asyncio.wait(
            [task_c2r, task_r2c],
            return_when=asyncio.FIRST_COMPLETED
        )
        close_stage, close_error = classify_tunnel_close(
            tracker,
            task_c2r=task_c2r,
            task_r2c=task_r2c,
        )
    except Exception as exc:
        profiler_logger.error(f"[{tracker.request_id}] Direct tunnel wait error: {exc}")
        close_stage = "forward_failed"
        close_error = str(exc)
    finally:
        # 强切 Pending 任务
        for t in [task_c2r, task_r2c]:
            if not t.done():
                t.cancel()
        
        # 物理多米诺骨牌彻底关闭，释放端口
        try:
            rmt_w.close()
            await rmt_w.wait_closed()
        except Exception:
            pass
        try:
            client_w.close()
            await client_w.wait_closed()
        except Exception:
            pass
            
    print_profiler_report(tracker)
    return ForwardResult(
        success=True,
        connect_latency_ms=connect_latency_ms,
        error=close_error,
        stage=close_stage,
    )


async def connect_via_proxy(client_r, client_w, target, upstream, tracker=None):
    if tracker is None:
        tracker = LatencyTracker(str(uuid.uuid4())[:8], target)
    phost, pport = upstream
    tracker.t_t1_end = time.time()
    
    connect_started = time.time()
    try:
        pr, pw = await connect_to(phost, pport)
    except Exception as exc:
        connect_latency_ms = int((time.time() - connect_started) * 1000)
        safe_write(client_w, b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        try:
            client_w.close()
            await client_w.wait_closed()
        except Exception:
            pass
        return ForwardResult(
            success=False,
            connect_latency_ms=connect_latency_ms,
            error=str(exc),
        )
        
    tracker.t_t2_end = time.time()

    pw.write(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    await pw.drain()
    
    try:
        resp_line = await asyncio.wait_for(pr.readline(), timeout=10)
    except Exception as exc:
        connect_latency_ms = int((time.time() - connect_started) * 1000)
        safe_write(client_w, b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        try:
            client_w.close()
            await client_w.wait_closed()
        except Exception:
            pass
        try:
            pw.close()
            await pw.wait_closed()
        except Exception:
            pass
        return ForwardResult(
            success=False,
            connect_latency_ms=connect_latency_ms,
            error=str(exc),
        )
        
    if not resp_line or b"200" not in resp_line:
        connect_latency_ms = int((time.time() - connect_started) * 1000)
        safe_write(client_w, b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        try:
            client_w.close()
            await client_w.wait_closed()
        except Exception:
            pass
        try:
            pw.close()
            await pw.wait_closed()
        except Exception:
            pass
        return ForwardResult(
            success=False,
            connect_latency_ms=connect_latency_ms,
            error=resp_line.decode("latin-1", errors="replace").strip()
            or "upstream CONNECT failed",
        )
        
    # 读取代理返回的响应头部，直至空行
    try:
        while True:
            line = await asyncio.wait_for(pr.readline(), timeout=5)
            if line in (b"\r\n", b"\n", b""):
                break
    except Exception as exc:
        connect_latency_ms = int((time.time() - connect_started) * 1000)
        safe_write(client_w, b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        try:
            client_w.close()
            await client_w.wait_closed()
        except Exception:
            pass
        try:
            pw.close()
            await pw.wait_closed()
        except Exception:
            pass
        return ForwardResult(
            success=False,
            connect_latency_ms=connect_latency_ms,
            error=str(exc),
        )
        
    tracker.t_t3_end = time.time()
    connect_latency_ms = int((time.time() - connect_started) * 1000)

    safe_write(client_w, b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await client_w.drain()
    
    # ── 双向协程启动与强切逻辑 ──
    task_c2r = asyncio.create_task(relay_client_to_remote(client_r, pw, tracker))
    task_r2c = asyncio.create_task(relay_remote_to_client(pr, client_w, tracker))
    close_stage = "tunnel_closed"
    close_error = None
    
    try:
        done, pending = await asyncio.wait(
            [task_c2r, task_r2c],
            return_when=asyncio.FIRST_COMPLETED
        )
        close_stage, close_error = classify_tunnel_close(
            tracker,
            task_c2r=task_c2r,
            task_r2c=task_r2c,
        )
    except Exception as exc:
        profiler_logger.error(f"[{tracker.request_id}] CONNECT wait error: {exc}")
        close_stage = "forward_failed"
        close_error = str(exc)
    finally:
        for t in [task_c2r, task_r2c]:
            if not t.done():
                t.cancel()
                
        try:
            pw.close()
            await pw.wait_closed()
        except Exception:
            pass
        try:
            client_w.close()
            await client_w.wait_closed()
        except Exception:
            pass
            
    print_profiler_report(tracker)
    return ForwardResult(
        success=True,
        connect_latency_ms=connect_latency_ms,
        error=close_error,
        stage=close_stage,
    )


# ── plain HTTP (non-CONNECT) ──────────────────────────────────────────

async def http_direct(client_r, client_w, first_line):
    """Forward plain HTTP directly. 读取 Host header 决定目标."""
    headers_data = first_line
    host, port = None, 80
    while True:
        line = await client_r.readline()
        headers_data += line
        if line in (b"\r\n", b"\n", b""):
            break
        if line.lower().startswith(b"host:") and host is None:
            h = line.split(b":", 1)[1].strip().decode()
            host, _, p = h.partition(":")
            port = int(p) if p else 80

    if not host:
        safe_write(client_w, b"HTTP/1.1 400 Bad Request\r\n\r\n")
        client_w.close()
        return False

    # 读取 body（如果有 Content-Length）
    body = await _read_body(client_r, headers_data)

    connect_started = time.monotonic()
    try:
        rmt_r, rmt_w = await connect_to(host, port)
    except Exception as exc:
        connect_latency_ms = elapsed_ms(connect_started)
        safe_write(client_w, b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        client_w.close()
        return ForwardResult(
            success=False,
            connect_latency_ms=connect_latency_ms,
            error=str(exc),
        )
    connect_latency_ms = elapsed_ms(connect_started)

    rmt_w.write(headers_data + body)
    await rmt_w.drain()
    await relay(rmt_r, client_w)
    rmt_w.close()
    return ForwardResult(success=True, connect_latency_ms=connect_latency_ms)


async def http_via_proxy(client_r, client_w, first_line, upstream):
    """Forward plain HTTP through upstream proxy."""
    phost, pport = upstream
    connect_started = time.monotonic()
    try:
        pr, pw = await connect_to(phost, pport)
    except Exception as exc:
        connect_latency_ms = elapsed_ms(connect_started)
        safe_write(client_w, b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        client_w.close()
        return ForwardResult(
            success=False,
            connect_latency_ms=connect_latency_ms,
            error=str(exc),
        )
    connect_latency_ms = elapsed_ms(connect_started)

    pw.write(first_line)
    while True:
        line = await client_r.readline()
        pw.write(line)
        if line in (b"\r\n", b"\n", b""):
            break

    body = await _read_body(client_r, first_line)
    if body:
        pw.write(body)
    await pw.drain()

    await relay(pr, client_w)
    pw.close()
    return ForwardResult(success=True, connect_latency_ms=connect_latency_ms)


# ── helpers ───────────────────────────────────────────────────────────

def safe_write(writer, data):
    try:
        writer.write(data)
    except Exception:
        pass


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def record_proxy_stats(
    started_at,
    method,
    host,
    route,
    success,
    latency_ms,
    error,
    connect_latency_ms=None,
    duration_ms=None,
    stage="completed",
    client_addr="",
    client_port=None,
    target_port=None,
    upstream_host="",
    upstream_port=None,
    client_pid=None,
    client_process="",
    client_exe="",
    client_label="",
):
    if stats_store is None:
        return
    try:
        stats_store.record_proxy_request(
            ProxyRequestEvent(
                started_at=started_at,
                completed_at=utc_now_iso(),
                method=method,
                host=host,
                route=route,
                success=success,
                latency_ms=latency_ms,
                error=error,
                connect_latency_ms=connect_latency_ms,
                duration_ms=duration_ms,
                stage=stage,
                client_addr=client_addr,
                client_port=client_port,
                target_port=target_port,
                upstream_host=upstream_host,
                upstream_port=upstream_port,
                client_pid=client_pid,
                client_process=client_process,
                client_exe=client_exe,
                client_label=client_label,
            )
        )
    except Exception as exc:
        log(f"stats record failed: {exc}")


async def _read_body(reader, request_bytes):
    """如果请求带 Content-Length，读取 body。否则返回空."""
    # 简易 Content-Length 提取
    for line in request_bytes.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            cl = int(line.split(b":", 1)[1].strip())
            body = b""
            remaining = cl
            while remaining > 0:
                chunk = await reader.read(min(remaining, READ_SIZE))
                if not chunk:
                    break
                body += chunk
                remaining -= len(chunk)
            return body
    return b""


import collections
import time

# 强保护敏感域名：绝对禁止升级为直连，保障翻墙稳定性与密钥隐私
SENSITIVE_HOSTS = {
    "api.openai.com", "chatgpt.com", "ab.chatgpt.com",
    "api.anthropic.com", "anthropic.com", "claude.ai",
    "generativelanguage.googleapis.com"
}

class HostRouteScore:
    """单个 Host 的实时网络时延采样与路由惩罚状态"""
    def __init__(self):
        # deque 滑动窗口，记录最近 5 次的 (时延_ms, success)
        self.direct_history = collections.deque(maxlen=5)
        self.proxy_history = collections.deque(maxlen=5)
        
        # 惩罚降级或升级状态，及过期时间戳
        self.status = "NORMAL"  # "NORMAL", "DEMOTED" (强制代理), "PROMOTED" (强制直连)
        self.status_expire_at = 0.0

# 内存自适应路由状态库
_route_tracker = collections.defaultdict(HostRouteScore)

def decide_adaptive_route(host: str, in_whitelist: bool) -> str:
    """
    自适应智能路由核心决策逻辑。
    返回: "direct" (直连) 或 "proxy" (代理)
    """
    global _route_tracker
    now = time.time()
    score = _route_tracker[host]
    
    # 1. 检查自适应惩罚是否过期，过期则自动恢复探索
    if score.status != "NORMAL" and now > score.status_expire_at:
        old_status = score.status
        score.status = "NORMAL"
        log(f"[Adaptive-Route] Host {host} 状态 {old_status} 惩罚期满，恢复常规自适应探索...")
        
    # 2. 如果处于强保护敏感名单中，强制走原版规则，严禁动态升级为直连
    is_sensitive = any(sh in host for sh in SENSITIVE_HOSTS)
    
    # 3. 状态路由判定
    if score.status == "DEMOTED":
        # 正在降级走代理处罚中
        return "proxy"
    elif score.status == "PROMOTED" and not is_sensitive:
        # 正在升级直连享受中
        return "direct"
        
    # 4. 常规白名单判断
    return "direct" if in_whitelist else "proxy"


def record_route_metrics(host: str, route: str, latency_ms: float, success: bool):
    """记录每一次连接的时延特征，自适应更新惩罚/升格状态"""
    global _route_tracker
    score = _route_tracker[host]
    now = time.time()
    
    history = score.direct_history if route == "direct" else score.proxy_history
    history.append((latency_ms, success))
    
    # 检查状态机更新
    if route == "direct":
        # 直连状态监控
        consecutive_failures = 0
        last_latencies = []
        for lat, succ in history:
            last_latencies.append(lat)
            if not succ:
                consecutive_failures += 1
            else:
                consecutive_failures = 0
                
        # 如果域名在白名单中且本次请求是成功的，哪怕全生命周期时间超过 3000ms 也绝对不算“直连质量崩溃”
        is_whitelisted = whitelist.match(host) if host else False
        if is_whitelisted and success:
            return

        # 判定 A: 直连发生极其严重的超时 (时延 >= 3000ms)，或者连续 2 次直连失败
        if (last_latencies and last_latencies[-1] >= 3000) or consecutive_failures >= 2:
            if score.status != "DEMOTED":
                score.status = "DEMOTED"
                score.status_expire_at = now + 300.0  # 惩罚 5 分钟
                msg = f"[Adaptive-Route] 检测到 Host {host} 直连质量崩溃 (时延: {latency_ms:.1f}ms, 成功: {success})。智能降级走代理 5 分钟！"
                log(msg)
                profiler_logger.info(msg)
            
    elif route == "proxy":
        # 代理状态监控
        # 判定 B: 默认代理 Host 建连时延 T3 变得极其拥堵 (时延 >= 2000ms)
        # 且该域名不在敏感强保护名单中，且以往直连有成功的历史 (没有严重超时)
        is_sensitive = any(sh in host for sh in SENSITIVE_HOSTS)
        if not is_sensitive and latency_ms >= 2000:
            # 查一下最近直连历史，如果没有严重报错，可以尝试升级
            has_good_direct = len(score.direct_history) > 0 and all(lat < 2000 and succ for lat, succ in score.direct_history)
            if has_good_direct or len(score.direct_history) == 0:
                if score.status != "PROMOTED":
                    score.status = "PROMOTED"
                    score.status_expire_at = now + 300.0  # 升级享受 5 分钟
                    msg = f"[Adaptive-Route] 检测到 Host {host} 代理耗时严重拥堵 (T3: {latency_ms:.1f}ms)。智能升格直连 5 分钟！"
                    log(msg)
                    profiler_logger.info(msg)


# ── main handler ──────────────────────────────────────────────────────

async def handle(client_r, client_w):
    _enable_nodelay(client_w)
    started_at = utc_now_iso()
    start_monotonic = time.monotonic()
    request_id = str(uuid.uuid4())[:8]
    tracker = LatencyTracker(request_id)
    
    client_addr, client_port = get_client_peer(client_w)
    client_info = resolve_client_process(client_addr, client_port)
    upstream = proxy_cache.get()
    upstream_host = upstream[0] if upstream else ""
    upstream_port = upstream[1] if upstream else None
    method = "?"
    target = "?"
    host = ""
    route = "direct"
    success = False
    error = None
    connect_latency_ms = None
    target_port = None
    stage = "completed"
    try:
        first_line = await asyncio.wait_for(client_r.readline(), timeout=10)
    except asyncio.TimeoutError:
        duration_ms = elapsed_ms(start_monotonic)
        record_proxy_stats(
            started_at,
            "UNKNOWN",
            UNKNOWN_HOST,
            UNPARSED_ROUTE,
            False,
            duration_ms,
            "timed out waiting for request line",
            duration_ms=duration_ms,
            stage="read_timeout",
            client_addr=client_addr,
            client_port=client_port,
            upstream_host=upstream_host,
            upstream_port=upstream_port,
            client_pid=client_info["pid"],
            client_process=client_info["process"],
            client_exe=client_info["exe"],
            client_label=client_info["label"],
        )
        client_w.close()
        return

    if not first_line:
        duration_ms = elapsed_ms(start_monotonic)
        record_proxy_stats(
            started_at,
            "UNKNOWN",
            UNKNOWN_HOST,
            UNPARSED_ROUTE,
            False,
            duration_ms,
            "client closed before request line",
            duration_ms=duration_ms,
            stage="client_closed",
            client_addr=client_addr,
            client_port=client_port,
            upstream_host=upstream_host,
            upstream_port=upstream_port,
            client_pid=client_info["pid"],
            client_process=client_info["process"],
            client_exe=client_info["exe"],
            client_label=client_info["label"],
        )
        client_w.close()
        return

    parts = first_line.decode("latin-1", errors="replace").rstrip("\r\n").split(" ")
    method = parts[0] if parts else "?"
    target = parts[1] if len(parts) > 1 else "?"
    if len(parts) < 2 or not method or not target or target == "?":
        duration_ms = elapsed_ms(start_monotonic)
        record_proxy_stats(
            started_at,
            method or "UNKNOWN",
            UNKNOWN_HOST,
            UNPARSED_ROUTE,
            False,
            duration_ms,
            "malformed request line",
            duration_ms=duration_ms,
            stage="parse_failed",
            client_addr=client_addr,
            client_port=client_port,
            upstream_host=upstream_host,
            upstream_port=upstream_port,
            client_pid=client_info["pid"],
            client_process=client_info["process"],
            client_exe=client_info["exe"],
            client_label=client_info["label"],
        )
        safe_write(client_w, b"HTTP/1.1 400 Bad Request\r\n\r\n")
        client_w.close()
        return
    target_port = extract_target_port(method, target)

    # extract host and check whitelist
    host = extract_host(target, first_line)
    tracker.host = host or target

    # ── 最优先：屏蔽名单检查（快速拒绝，不转发）────────────────────
    if host and blocklist.match(host):
        duration_ms = elapsed_ms(start_monotonic)
        log(f"{method} {host} -> BLOCKED")
        safe_write(client_w, b"HTTP/1.1 502 Blocked\r\nContent-Length: 0\r\n\r\n")
        client_w.close()
        record_proxy_stats(
            started_at,
            method,
            host,
            "blocked",
            False,
            duration_ms,
            "blocked by blocklist",
            duration_ms=duration_ms,
            stage="blocked",
            client_addr=client_addr,
            client_port=client_port,
            target_port=target_port,
            upstream_host=upstream_host,
            upstream_port=upstream_port,
            client_pid=client_info["pid"],
            client_process=client_info["process"],
            client_exe=client_info["exe"],
            client_label=client_info["label"],
        )
        return

    # 结合静态白名单进行自适应动态决策
    in_whitelist = whitelist.match(host) if host else False
    adaptive_route = decide_adaptive_route(host, in_whitelist) if host else "proxy"
    force_direct = (adaptive_route == "direct")

    if force_direct:
        via = "direct (adaptive)" if in_whitelist else "direct (adaptive_promoted)"
        route = "direct_whitelist" if in_whitelist else "direct"
    elif upstream:
        via = f"proxy {upstream[0]}:{upstream[1]}"
        route = "proxy"
    else:
        via = "direct"
        route = "direct"
    log(f"{method} {host or target[:80]} -> {via}")

    try:
        if method == "CONNECT":
            while True:
                line = await client_r.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
            if force_direct or not upstream:
                try:
                    result = await connect_direct_tunnel(client_r, client_w, target, tracker)
                except TypeError:
                    result = await connect_direct_tunnel(client_r, client_w, target)
            else:
                try:
                    result = await connect_via_proxy(client_r, client_w, target, upstream, tracker)
                except TypeError:
                    result = await connect_via_proxy(client_r, client_w, target, upstream)
        else:
            if force_direct or not upstream:
                result = await http_direct(client_r, client_w, first_line)
            else:
                result = await http_via_proxy(client_r, client_w, first_line, upstream)
        if isinstance(result, ForwardResult):
            success = result.success
            connect_latency_ms = result.connect_latency_ms
            if result.stage:
                stage = result.stage
            if result.error:
                error = result.error
        else:
            success = result is not False
    except Exception as exc:
        error = str(exc)
        success = False
    finally:
        duration_ms = elapsed_ms(start_monotonic)
        record_proxy_stats(
            started_at,
            method,
            host or target[:80] or UNKNOWN_HOST,
            route,
            success,
            duration_ms,
            error,
            connect_latency_ms=connect_latency_ms,
            duration_ms=duration_ms,
            stage=stage if success else "forward_failed",
            client_addr=client_addr,
            client_port=client_port,
            target_port=target_port,
            upstream_host=upstream_host,
            upstream_port=upstream_port,
            client_pid=client_info["pid"],
            client_process=client_info["process"],
            client_exe=client_info["exe"],
            client_label=client_info["label"],
        )
        if host:
            sampled_lat = duration_ms if route in ("direct", "direct_whitelist") else (connect_latency_ms or 0.0)
            record_route_metrics(host, "direct" if route in ("direct", "direct_whitelist") else "proxy", sampled_lat, success)
        try:
            client_w.close()
        except Exception:
            pass


def kill_process_by_port(port: int) -> bool:
    """
    通过 Windows 原生 netstat -ano 查找监听指定端口的进程 PID，
    并使用 taskkill /F /PID 强杀之（跳过当前进程自身）。
    返回是否成功执行了强杀。
    """
    import subprocess
    import os

    msg = f"[Port-Heal] 检测到端口 {port} 被占用，正在尝试定位占用进程..."
    log(msg)
    profiler_logger.info(msg)
    my_pid = os.getpid()
    killed = False
    try:
        # 执行 netstat -ano
        output = subprocess.check_output("netstat -ano", shell=True, text=True, errors="ignore")
        pids_to_kill = set()
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            # 我们需要确保是监听状态，并且端口确实匹配
            # 格式: 协议 本地地址 外部地址 状态 PID
            parts = line.split()
            if len(parts) >= 5 and parts[3].upper() == "LISTENING":
                local_addr = parts[1]
                # 提取端口，可能形如 127.0.0.1:8889 或 [::]:8889 或 0.0.0.0:8889
                if local_addr.endswith(f":{port}"):
                    try:
                        pid = int(parts[-1])
                        if pid != my_pid and pid > 0:
                            pids_to_kill.add(pid)
                    except ValueError:
                        pass
        
        for pid in pids_to_kill:
            msg = f"[Port-Heal] 发现目标进程 PID {pid} 正在占用端口 {port}，执行强杀..."
            log(msg)
            profiler_logger.info(msg)
            # 运行 taskkill /F /PID <pid>
            subprocess.run(f"taskkill /F /PID {pid}", shell=True, capture_output=True)
            killed = True
            
        if not pids_to_kill:
            msg = f"[Port-Heal] 未发现其他进程占用端口 {port} (可能是协议栈处于 TIME_WAIT 状态)"
            log(msg)
            profiler_logger.info(msg)
    except Exception as exc:
        msg = f"[Port-Heal] 端口清理执行异常: {exc}"
        log(msg)
        profiler_logger.info(msg)
    return killed


async def main():
    global stats_store
    profiler_logger.info(
        "smart-proxy process starting "
        f"pid={os.getpid()} python={sys.version.split()[0]} executable={sys.executable} "
        f"cwd={os.getcwd()}"
    )
    stats_store = StatsStore(STATS_DB_FILE)
    profiler_logger.info(
        "smart-proxy config "
        f"proxy={LISTEN_HOST}:{LISTEN_PORT} dashboard={DASHBOARD_HOST}:{DASHBOARD_PORT} "
        f"cache_sec={CACHE_SEC} read_size={READ_SIZE} stats_db={Path(STATS_DB_FILE).resolve()} "
        f"whitelist={Path(WHITELIST_FILE).resolve()} blocklist={Path(BLOCKLIST_FILE).resolve()}"
    )
    
    # 启动异步日志缓冲消费中台
    start_async_logging_listener()
    
    server = None
    dashboard = None
    try:
        # 1. 尝试拉起 8889 代理端口，带自愈重试
        try:
            server = await asyncio.start_server(handle, LISTEN_HOST, LISTEN_PORT)
        except OSError as exc:
            if exc.errno == 10048 or "already in use" in str(exc).lower():
                msg = f"[Port-Heal] 代理服务端口 {LISTEN_PORT} 被占用。触发自愈清理..."
                log(msg)
                profiler_logger.info(msg)
                kill_process_by_port(LISTEN_PORT)
                await asyncio.sleep(0.5)  # 缓冲给操作系统释放句柄
                # 第二次重试
                server = await asyncio.start_server(handle, LISTEN_HOST, LISTEN_PORT)
                msg = f"[Port-Heal] 代理服务端口 {LISTEN_PORT} 自愈绑定成功！"
                log(msg)
                profiler_logger.info(msg)
            else:
                raise

        # 2. 尝试拉起 8890 状态/Dashboard 端口，带自愈重试
        try:
            dashboard = await start_stats_server_with_status(
                stats_store,
                DASHBOARD_HOST,
                DASHBOARD_PORT,
                status_provider=build_runtime_status,
                whitelist_provider=WhitelistProvider(whitelist, lambda: stats_store),
                blocklist_provider=BlocklistProvider(blocklist),
                doctor_provider=build_doctor_report,
                provider_health_provider=build_provider_health_report,
            )
        except OSError as exc:
            if exc.errno == 10048 or "already in use" in str(exc).lower():
                msg = f"[Port-Heal] Dashboard服务端口 {DASHBOARD_PORT} 被占用。触发自愈清理..."
                log(msg)
                profiler_logger.info(msg)
                kill_process_by_port(DASHBOARD_PORT)
                await asyncio.sleep(0.5)
                # 第二次重试
                dashboard = await start_stats_server_with_status(
                    stats_store,
                    DASHBOARD_HOST,
                    DASHBOARD_PORT,
                    status_provider=build_runtime_status,
                    whitelist_provider=WhitelistProvider(whitelist, lambda: stats_store),
                    blocklist_provider=BlocklistProvider(blocklist),
                    doctor_provider=build_doctor_report,
                    provider_health_provider=build_provider_health_report,
                )
                msg = f"[Port-Heal] Dashboard服务端口 {DASHBOARD_PORT} 自愈绑定成功！"
                log(msg)
                profiler_logger.info(msg)
            else:
                if server:
                    server.close()
                raise
        except Exception:
            if server:
                server.close()
            raise

        log(f"listening {LISTEN_HOST}:{LISTEN_PORT}  |  mode: auto-detect Windows system proxy  |  cache: {CACHE_SEC}s")
        log(f"dashboard http://{DASHBOARD_HOST}:{DASHBOARD_PORT}")
        runtime_status = build_runtime_status()
        profiler_logger.info(
            f"smart-proxy serving pid={os.getpid()} proxy={LISTEN_HOST}:{LISTEN_PORT} "
            f"dashboard={DASHBOARD_HOST}:{DASHBOARD_PORT} upstream={runtime_status['upstream_proxy']} "
            f"whitelist_count={runtime_status['whitelist_count']}"
        )
        asyncio.create_task(run_usage_ingestion_loop(stats_store, log=log))
        async with server, dashboard:
            await asyncio.gather(server.serve_forever(), dashboard.serve_forever())
    except Exception:
        profiler_logger.exception(f"smart-proxy main crashed pid={os.getpid()}")
        raise
    finally:
        # 3. 优雅退出：冲刷剩余未写入磁盘的日志并关闭线程池
        profiler_logger.info(
            f"smart-proxy main exiting pid={os.getpid()} uptime_sec={time.time() - START_TIME:.1f}"
        )
        await shutdown_async_logging()


def cli():
    if sys.platform != "win32":
        print("Windows only.", file=sys.stderr)
        sys.exit(1)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("")


if __name__ == "__main__":
    cli()
