"""Windows proxy and client process inspection helpers."""

import ctypes
from ctypes import wintypes
from pathlib import Path
import socket
import subprocess
import sys
import time
import winreg

from .config import DEFAULT_CONFIG


LISTEN_HOST = DEFAULT_CONFIG.listen_host
LISTEN_PORT = DEFAULT_CONFIG.listen_port
PROCESS_INFO_CACHE_SEC = 60
PROCESS_INFO_CACHE = {}


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
