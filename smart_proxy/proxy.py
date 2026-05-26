"""Smart proxy sidecar — auto-detect Windows system proxy per request."""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import socket
import sys
import time
import uuid

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
from smart_proxy.whitelist import Whitelist, WhitelistProvider
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


proxy_cache = Cache(CACHE_SEC)

# ── whitelist ────────────────────────────────────────────────────────

WHITELIST_FILE = DEFAULT_CONFIG.whitelist_file
WHITELIST_RELOAD_SEC = DEFAULT_CONFIG.whitelist_reload_sec
STATS_DB_FILE = DEFAULT_CONFIG.stats_db_file
stats_store = None


whitelist = Whitelist(WHITELIST_FILE, WHITELIST_RELOAD_SEC)


def _check_socket(host, port, timeout=0.35):
    started = time.monotonic()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True, elapsed_ms(started), ""
    except OSError as exc:
        return False, elapsed_ms(started), str(exc)


def _doctor_item(key, label, ok, detail, fix=""):
    return {
        "key": key,
        "label": label,
        "status": "ok" if ok else "warning",
        "detail": detail,
        "fix": fix,
    }


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


def build_doctor_report():
    upstream = proxy_cache.get()
    whitelist.refresh_if_needed()
    reader = ClaudeUsageReader()
    projects_dir = reader.projects_dir
    transcript_files = []
    if projects_dir.exists():
        transcript_files = list(projects_dir.rglob("*.jsonl"))[:200]

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
            "upstream",
            "系统代理 / 上游代理",
            upstream_ok,
            upstream_detail,
            "检查 Windows 系统代理设置或上游代理进程。",
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



async def connect_to(host, port, timeout=5):
    return await asyncio.wait_for(asyncio.open_connection(host, port), timeout)


def elapsed_ms(start_monotonic):
    return int((time.monotonic() - start_monotonic) * 1000)


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
    
    try:
        done, pending = await asyncio.wait(
            [task_c2r, task_r2c],
            return_when=asyncio.FIRST_COMPLETED
        )
    except Exception as exc:
        profiler_logger.error(f"[{tracker.request_id}] Direct tunnel wait error: {exc}")
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
    return ForwardResult(success=True, connect_latency_ms=connect_latency_ms)


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
    
    try:
        done, pending = await asyncio.wait(
            [task_c2r, task_r2c],
            return_when=asyncio.FIRST_COMPLETED
        )
    except Exception as exc:
        profiler_logger.error(f"[{tracker.request_id}] CONNECT wait error: {exc}")
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
    return ForwardResult(success=True, connect_latency_ms=connect_latency_ms)


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
                
        # 判定 A: 直连发生极其严重的超时 (时延 >= 3000ms)，或者连续 2 次直连失败
        if (last_latencies and last_latencies[-1] >= 3000) or consecutive_failures >= 2:
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
                score.status = "PROMOTED"
                score.status_expire_at = now + 300.0  # 升级享受 5 分钟
                msg = f"[Adaptive-Route] 检测到 Host {host} 代理耗时严重拥堵 (T3: {latency_ms:.1f}ms)。智能升格直连 5 分钟！"
                log(msg)
                profiler_logger.info(msg)


# ── main handler ──────────────────────────────────────────────────────

async def handle(client_r, client_w):
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
            stage="tunnel_closed" if success else "forward_failed",
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
    stats_store = StatsStore(STATS_DB_FILE)
    
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
        asyncio.create_task(run_usage_ingestion_loop(stats_store, log=log))
        async with server, dashboard:
            await asyncio.gather(server.serve_forever(), dashboard.serve_forever())
    finally:
        # 3. 优雅退出：冲刷剩余未写入磁盘的日志并关闭线程池
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
