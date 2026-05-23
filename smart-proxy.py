"""Smart proxy sidecar — auto-detect Windows system proxy per request."""
import asyncio
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timezone
import fnmatch
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import winreg

from claude_usage_reader import ClaudeUsageReader
from smart_proxy_config import DEFAULT_CONFIG
from stats_store import ProxyRequestEvent, StatsStore
from stats_server import (
    DASHBOARD_HOST,
    DASHBOARD_PORT,
    start_stats_server_with_status,
)
from usage_ingestion import run_usage_ingestion_loop

LISTEN_HOST = DEFAULT_CONFIG.listen_host
LISTEN_PORT = DEFAULT_CONFIG.listen_port
CACHE_SEC = DEFAULT_CONFIG.cache_sec
READ_SIZE = DEFAULT_CONFIG.read_size
PROVIDER_HEALTH_PATH = DEFAULT_CONFIG.provider_health_path
UNKNOWN_HOST = "(unknown)"
UNPARSED_ROUTE = "unparsed"
PROCESS_INFO_CACHE_SEC = 60
PROCESS_INFO_CACHE = {}


@dataclass(frozen=True)
class ForwardResult:
    success: bool
    connect_latency_ms: int | None = None
    error: str | None = None


def get_system_proxy():
    """Return (host, port) if Windows system proxy is enabled, else None."""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        )
        enabled_val, _ = winreg.QueryValueEx(key, "ProxyEnable")
        server_val, _ = winreg.QueryValueEx(key, "ProxyServer")
        winreg.CloseKey(key)
    except OSError:
        return None

    if not enabled_val or not server_val or not server_val.strip():
        return None

    server = server_val.strip()
    # 格式可能是 "host:port" 或 "http=host:port;https=host:port"
    if "=" in server:
        for part in server.split(";"):
            part = part.strip()
            if part.lower().startswith("http=") or part.lower().startswith("https="):
                server = part.split("=", 1)[1].strip()
                break
    if ":" in server:
        host, port = server.rsplit(":", 1)
        if not host:
            host = "127.0.0.1"
        return host, int(port)
    return None


class Cache:
    def __init__(self, ttl):
        self._ttl = ttl
        self._expires = 0
        self._value = None

    def get(self):
        now = time.monotonic()
        if now >= self._expires:
            self._value = get_system_proxy()
            self._expires = now + self._ttl
        return self._value


proxy_cache = Cache(CACHE_SEC)

# ── whitelist ────────────────────────────────────────────────────────

WHITELIST_FILE = DEFAULT_CONFIG.whitelist_file
WHITELIST_RELOAD_SEC = DEFAULT_CONFIG.whitelist_reload_sec
STATS_DB_FILE = DEFAULT_CONFIG.stats_db_file
stats_store = None


class Whitelist:
    def __init__(self, path, reload_interval):
        self._path = os.fspath(path)
        self._interval = reload_interval
        self._expires = 0
        self._patterns = set()
        self._loaded_at = ""

    def _load(self):
        try:
            with open(self._path, encoding="utf-8") as f:
                self._patterns = {
                    line.strip() for line in f
                    if line.strip() and not line.startswith("#")
                }
        except FileNotFoundError:
            self._patterns = set()
        self._loaded_at = datetime.now(timezone.utc).isoformat()
        self._expires = time.monotonic() + self._interval

    def refresh_if_needed(self):
        now = time.monotonic()
        if now >= self._expires:
            self._load()

    def reload(self):
        self._load()

    def match(self, host):
        self.refresh_if_needed()
        if not self._patterns:
            return False
        return any(fnmatch.fnmatch(host, p) for p in self._patterns)

    def entries(self):
        self.refresh_if_needed()
        return sorted(self._patterns)

    def save_entries(self, entries):
        cleaned = []
        seen = set()
        for entry in entries:
            value = str(entry).strip()
            if not value or value.startswith("#"):
                continue
            if any(char.isspace() for char in value):
                raise ValueError(f"invalid whitelist entry: {value}")
            if value not in seen:
                seen.add(value)
                cleaned.append(value)
        directory = os.path.dirname(self._path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self._path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("# Managed by Smart Proxy Console\n")
            for entry in cleaned:
                handle.write(f"{entry}\n")
        self._load()
        return self.entries()

    @property
    def path(self):
        return self._path

    @property
    def pattern_count(self):
        return len(self._patterns)

    @property
    def loaded_at(self):
        return self._loaded_at


whitelist = Whitelist(WHITELIST_FILE, WHITELIST_RELOAD_SEC)


class WhitelistProvider:
    def __init__(self, whitelist_obj, store_getter):
        self._whitelist = whitelist_obj
        self._store_getter = store_getter

    def get(self):
        self._whitelist.refresh_if_needed()
        store = self._store_getter()
        candidates = store.get_whitelist_candidates(limit=12) if store else []
        entries = self._whitelist.entries()
        return {
            "entries": entries,
            "path": self._whitelist.path,
            "count": len(entries),
            "loaded_at": self._whitelist.loaded_at,
            "candidates": candidates,
        }

    def save(self, payload):
        entries = payload.get("entries")
        if not isinstance(entries, list):
            raise ValueError("entries must be a list")
        saved = self._whitelist.save_entries(entries)
        return {
            "ok": True,
            "entries": saved,
            "count": len(saved),
            "path": self._whitelist.path,
            "loaded_at": self._whitelist.loaded_at,
        }


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
    checks.append(build_provider_health_doctor_item())
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


def get_client_peer(writer):
    try:
        peer = writer.get_extra_info("peername")
    except Exception:
        return "", None
    if isinstance(peer, tuple) and len(peer) >= 2:
        return str(peer[0]), int(peer[1])
    return "", None


class MIB_TCPROW_OWNER_PID(ctypes.Structure):
    _fields_ = [
        ("dwState", wintypes.DWORD),
        ("dwLocalAddr", wintypes.DWORD),
        ("dwLocalPort", wintypes.DWORD),
        ("dwRemoteAddr", wintypes.DWORD),
        ("dwRemotePort", wintypes.DWORD),
        ("dwOwningPid", wintypes.DWORD),
    ]


AF_INET = 2
TCP_TABLE_OWNER_PID_ALL = 5
NO_ERROR = 0
ERROR_INSUFFICIENT_BUFFER = 122
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def ipv4_from_dword(value):
    return socket.inet_ntoa(int(value).to_bytes(4, "little"))


def port_from_dword(value):
    return socket.ntohs(int(value) & 0xFFFF)


def iter_tcp_owner_rows():
    if sys.platform != "win32":
        return []

    iphlpapi = ctypes.WinDLL("iphlpapi")
    size = wintypes.DWORD(0)
    result = iphlpapi.GetExtendedTcpTable(
        None,
        ctypes.byref(size),
        False,
        AF_INET,
        TCP_TABLE_OWNER_PID_ALL,
        0,
    )
    if result not in (NO_ERROR, ERROR_INSUFFICIENT_BUFFER):
        return []

    buffer = ctypes.create_string_buffer(size.value)
    result = iphlpapi.GetExtendedTcpTable(
        buffer,
        ctypes.byref(size),
        False,
        AF_INET,
        TCP_TABLE_OWNER_PID_ALL,
        0,
    )
    if result != NO_ERROR:
        return []

    count = ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD)).contents.value
    row_array_type = MIB_TCPROW_OWNER_PID * count
    return list(
        row_array_type.from_buffer_copy(buffer, ctypes.sizeof(wintypes.DWORD))
    )


def find_client_pid(client_addr, client_port):
    if not client_addr or client_port is None:
        return None

    for row in iter_tcp_owner_rows():
        try:
            local_addr = ipv4_from_dword(row.dwLocalAddr)
            local_port = port_from_dword(row.dwLocalPort)
            remote_addr = ipv4_from_dword(row.dwRemoteAddr)
            remote_port = port_from_dword(row.dwRemotePort)
        except Exception:
            continue
        if (
            local_addr == client_addr
            and local_port == int(client_port)
            and remote_addr == LISTEN_HOST
            and remote_port == LISTEN_PORT
        ):
            return int(row.dwOwningPid)
    return None


def query_process_image_path(pid):
    if not pid or sys.platform != "win32":
        return ""

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def query_process_command_line(pid):
    if not pid or sys.platform != "win32":
        return ""

    command = (
        "$p = Get-CimInstance Win32_Process -Filter "
        f"'ProcessId = {int(pid)}'; "
        "if ($p) { [Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
        "$p.CommandLine }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=1.5,
            check=False,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def classify_client_process(process_name, exe_path, command_line):
    text = f"{process_name}\n{exe_path}\n{command_line}".lower()
    if "antigravity" in text or "language_server" in text:
        return "Antigravity"
    if "cockpit" in text:
        return "Cockpit Tools"
    if "claude" in text or "claude-code" in text or "claude_code" in text:
        return "Claude Code"
    if "codex" in text:
        return "Codex"
    if "chrome" in text:
        return "Chrome"
    return "Unknown"


def unknown_client_process():
    return {
        "pid": None,
        "process": "",
        "exe": "",
        "label": "Unknown",
    }


def resolve_client_process(client_addr, client_port):
    try:
        pid = find_client_pid(client_addr, client_port)
    except Exception:
        return unknown_client_process()
    if not pid:
        return unknown_client_process()

    now = time.monotonic()
    cached = PROCESS_INFO_CACHE.get(pid)
    if cached and now - cached["cached_at"] < PROCESS_INFO_CACHE_SEC:
        return cached["info"]

    exe_path = query_process_image_path(pid)
    command_line = query_process_command_line(pid)
    process_name = Path(exe_path).name if exe_path else ""
    if not process_name and command_line:
        process_name = Path(command_line.split(" ", 1)[0].strip('"')).name

    info = {
        "pid": pid,
        "process": process_name,
        "exe": exe_path,
        "label": classify_client_process(process_name, exe_path, command_line),
    }
    PROCESS_INFO_CACHE[pid] = {"cached_at": now, "info": info}
    return info


def log(msg):
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


async def connect_to(host, port, timeout=5):
    return await asyncio.wait_for(asyncio.open_connection(host, port), timeout)


def elapsed_ms(start_monotonic):
    return int((time.monotonic() - start_monotonic) * 1000)


# ── CONNECT (TLS tunnel) ──────────────────────────────────────────────

async def connect_direct_tunnel(client_r, client_w, target):
    host, _, port = target.rpartition(":")
    connect_started = time.monotonic()
    try:
        rmt_r, rmt_w = await connect_to(host, int(port))
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
    safe_write(client_w, b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await client_w.drain()
    await asyncio.gather(relay(client_r, rmt_w), relay(rmt_r, client_w))
    rmt_w.close()
    return ForwardResult(success=True, connect_latency_ms=connect_latency_ms)


async def connect_via_proxy(client_r, client_w, target, upstream):
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

    pw.write(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    await pw.drain()
    try:
        resp_line = await asyncio.wait_for(pr.readline(), timeout=5)
    except Exception as exc:
        connect_latency_ms = elapsed_ms(connect_started)
        safe_write(client_w, b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        client_w.close()
        pw.close()
        return ForwardResult(
            success=False,
            connect_latency_ms=connect_latency_ms,
            error=str(exc),
        )
    if not resp_line or b"200" not in resp_line:
        connect_latency_ms = elapsed_ms(connect_started)
        safe_write(client_w, b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        client_w.close()
        pw.close()
        return ForwardResult(
            success=False,
            connect_latency_ms=connect_latency_ms,
            error=resp_line.decode("latin-1", errors="replace").strip()
            or "upstream CONNECT failed",
        )
    # drain proxy response headers
    try:
        while True:
            line = await asyncio.wait_for(pr.readline(), timeout=5)
            if line in (b"\r\n", b"\n", b""):
                break
    except Exception as exc:
        connect_latency_ms = elapsed_ms(connect_started)
        safe_write(client_w, b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        client_w.close()
        pw.close()
        return ForwardResult(
            success=False,
            connect_latency_ms=connect_latency_ms,
            error=str(exc),
        )
    connect_latency_ms = elapsed_ms(connect_started)

    safe_write(client_w, b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await client_w.drain()
    await asyncio.gather(relay(client_r, pw), relay(pr, client_w))
    pw.close()
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


# ── main handler ──────────────────────────────────────────────────────

async def handle(client_r, client_w):
    started_at = utc_now_iso()
    start_monotonic = time.monotonic()
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
    force_direct = whitelist.match(host)

    if force_direct:
        via = "direct (whitelist)"
        route = "direct_whitelist"
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
                result = await connect_direct_tunnel(client_r, client_w, target)
            else:
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
        try:
            client_w.close()
        except Exception:
            pass


async def main():
    global stats_store
    stats_store = StatsStore(STATS_DB_FILE)
    server = await asyncio.start_server(handle, LISTEN_HOST, LISTEN_PORT)
    dashboard = await start_stats_server_with_status(
        stats_store,
        DASHBOARD_HOST,
        DASHBOARD_PORT,
        status_provider=build_runtime_status,
        whitelist_provider=WhitelistProvider(whitelist, lambda: stats_store),
        doctor_provider=build_doctor_report,
    )
    log(f"listening {LISTEN_HOST}:{LISTEN_PORT}  |  mode: auto-detect Windows system proxy  |  cache: {CACHE_SEC}s")
    log(f"dashboard http://{DASHBOARD_HOST}:{DASHBOARD_PORT}")
    asyncio.create_task(run_usage_ingestion_loop(stats_store, log=log))
    async with server, dashboard:
        await asyncio.gather(server.serve_forever(), dashboard.serve_forever())


if __name__ == "__main__":
    if sys.platform != "win32":
        print("Windows only.", file=sys.stderr)
        sys.exit(1)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("")
