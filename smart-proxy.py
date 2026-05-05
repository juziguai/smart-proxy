"""Smart proxy sidecar — auto-detect Windows system proxy per request."""
import asyncio
import fnmatch
import os
import sys
import time
import winreg

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8889
CACHE_SEC = 3
READ_SIZE = 65536


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

WHITELIST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whitelist.txt")
WHITELIST_RELOAD_SEC = 60


class Whitelist:
    def __init__(self, path, reload_interval):
        self._path = path
        self._interval = reload_interval
        self._expires = 0
        self._patterns = set()

    def _load(self):
        try:
            with open(self._path, encoding="utf-8") as f:
                self._patterns = {
                    line.strip() for line in f
                    if line.strip() and not line.startswith("#")
                }
        except FileNotFoundError:
            self._patterns = set()

    def match(self, host):
        now = time.monotonic()
        if now >= self._expires:
            self._load()
            self._expires = now + self._interval
        if not self._patterns:
            return False
        return any(fnmatch.fnmatch(host, p) for p in self._patterns)


whitelist = Whitelist(WHITELIST_FILE, WHITELIST_RELOAD_SEC)


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


# ── CONNECT (TLS tunnel) ──────────────────────────────────────────────

async def connect_direct_tunnel(client_r, client_w, target):
    host, _, port = target.rpartition(":")
    try:
        rmt_r, rmt_w = await connect_to(host, int(port))
    except Exception:
        safe_write(client_w, b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        client_w.close()
        return
    safe_write(client_w, b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await client_w.drain()
    await asyncio.gather(relay(client_r, rmt_w), relay(rmt_r, client_w))
    rmt_w.close()


async def connect_via_proxy(client_r, client_w, target, upstream):
    phost, pport = upstream
    try:
        pr, pw = await connect_to(phost, pport)
    except Exception:
        safe_write(client_w, b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        client_w.close()
        return

    pw.write(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    await pw.drain()
    resp_line = await asyncio.wait_for(pr.readline(), timeout=5)
    if not resp_line or b"200" not in resp_line:
        safe_write(client_w, b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        client_w.close()
        pw.close()
        return
    # drain proxy response headers
    while True:
        line = await asyncio.wait_for(pr.readline(), timeout=5)
        if line in (b"\r\n", b"\n", b""):
            break

    safe_write(client_w, b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await client_w.drain()
    await asyncio.gather(relay(client_r, pw), relay(pr, client_w))
    pw.close()


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
        return

    # 读取 body（如果有 Content-Length）
    body = await _read_body(client_r, headers_data)

    try:
        rmt_r, rmt_w = await connect_to(host, port)
    except Exception:
        safe_write(client_w, b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        client_w.close()
        return

    rmt_w.write(headers_data + body)
    await rmt_w.drain()
    await relay(rmt_r, client_w)
    rmt_w.close()


async def http_via_proxy(client_r, client_w, first_line, upstream):
    """Forward plain HTTP through upstream proxy."""
    phost, pport = upstream
    try:
        pr, pw = await connect_to(phost, pport)
    except Exception:
        safe_write(client_w, b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        client_w.close()
        return

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


# ── helpers ───────────────────────────────────────────────────────────

def safe_write(writer, data):
    try:
        writer.write(data)
    except Exception:
        pass


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
    upstream = proxy_cache.get()
    try:
        first_line = await asyncio.wait_for(client_r.readline(), timeout=10)
    except asyncio.TimeoutError:
        client_w.close()
        return

    if not first_line:
        client_w.close()
        return

    parts = first_line.decode("latin-1", errors="replace").rstrip("\r\n").split(" ")
    method = parts[0] if parts else "?"
    target = parts[1] if len(parts) > 1 else "?"

    # extract host and check whitelist
    host = extract_host(target, first_line)
    force_direct = whitelist.match(host)

    if force_direct:
        via = "direct (whitelist)"
    elif upstream:
        via = f"proxy {upstream[0]}:{upstream[1]}"
    else:
        via = "direct"
    log(f"{method} {host or target[:80]} -> {via}")

    if method == "CONNECT":
        while True:
            line = await client_r.readline()
            if line in (b"\r\n", b"\n", b""):
                break
        if force_direct or not upstream:
            await connect_direct_tunnel(client_r, client_w, target)
        else:
            await connect_via_proxy(client_r, client_w, target, upstream)
    else:
        if force_direct or not upstream:
            await http_direct(client_r, client_w, first_line)
        else:
            await http_via_proxy(client_r, client_w, first_line, upstream)
    try:
        client_w.close()
    except Exception:
        pass


async def main():
    server = await asyncio.start_server(handle, LISTEN_HOST, LISTEN_PORT)
    log(f"listening {LISTEN_HOST}:{LISTEN_PORT}  |  mode: auto-detect Windows system proxy  |  cache: {CACHE_SEC}s")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    if sys.platform != "win32":
        print("Windows only.", file=sys.stderr)
        sys.exit(1)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("")
