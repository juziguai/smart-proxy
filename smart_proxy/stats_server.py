import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import DEFAULT_CONFIG


DASHBOARD_HOST = DEFAULT_CONFIG.dashboard_host
DASHBOARD_PORT = DEFAULT_CONFIG.dashboard_port


def build_stats_response(status, payload):
    return (
        status,
        {"Content-Type": "application/json; charset=utf-8"},
        json.dumps(payload).encode("utf-8"),
    )


def build_html_response(status, html):
    return (
        status,
        {"Content-Type": "text/html; charset=utf-8"},
        html.encode("utf-8"),
    )


def build_text_response(status, text, content_type):
    return (
        status,
        {"Content-Type": content_type},
        text.encode("utf-8"),
    )


def read_text_asset(path):
    return path.read_text(encoding="utf-8")


def handle_stats_request(
    method,
    parsed_url,
    stats_store,
    status_provider=None,
    whitelist_provider=None,
    doctor_provider=None,
    provider_health_provider=None,
    request_body=b"",
):
    if method == "GET" and parsed_url.path in ("", "/"):
        return build_html_response(200, read_text_asset(DASHBOARD_HTML_FILE))

    if method == "GET" and parsed_url.path in ASSET_CONTENT_TYPES:
        asset_path, content_type = ASSET_CONTENT_TYPES[parsed_url.path]
        return build_text_response(200, read_text_asset(asset_path), content_type)

    if method == "GET" and parsed_url.path == "/api/summary":
        params = parse_qs(parsed_url.query)
        range_name = (params.get("range") or ["day"])[0]
        return build_stats_response(200, stats_store.get_summary(range_name))

    if method == "GET" and parsed_url.path == "/api/trends":
        params = parse_qs(parsed_url.query)
        range_name = (params.get("range") or ["day"])[0]
        models = params.get("model") or []
        return build_stats_response(
            200,
            stats_store.get_trends(range_name, models=models),
        )

    if method == "GET" and parsed_url.path == "/api/recent-requests":
        params = parse_qs(parsed_url.query)
        raw_limit = (params.get("limit") or ["50"])[0]
        try:
            limit = int(raw_limit)
        except ValueError:
            limit = 50
        return build_stats_response(
            200,
            {"requests": stats_store.get_recent_proxy_requests(limit=limit)},
        )

    if method == "GET" and parsed_url.path == "/api/runtime-status":
        if status_provider is None:
            status = {
                "proxy_enabled": None,
                "upstream_proxy": "",
                "whitelist_count": 0,
                "whitelist_path": "",
                "whitelist_loaded_at": "",
            }
        else:
            status = status_provider()
        return build_stats_response(200, status)

    if method == "GET" and parsed_url.path == "/api/whitelist":
        if whitelist_provider is None:
            return build_stats_response(
                200,
                {
                    "entries": [],
                    "path": "",
                    "count": 0,
                    "loaded_at": "",
                    "candidates": [],
                },
            )
        return build_stats_response(200, whitelist_provider.get())

    if method == "POST" and parsed_url.path == "/api/whitelist":
        if whitelist_provider is None:
            return build_stats_response(503, {"error": "whitelist unavailable"})
        try:
            payload = json.loads(request_body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return build_stats_response(400, {"error": "invalid json"})
        try:
            return build_stats_response(200, whitelist_provider.save(payload))
        except ValueError as exc:
            return build_stats_response(400, {"error": str(exc)})

    if method == "GET" and parsed_url.path == "/api/doctor":
        if doctor_provider is None:
            return build_stats_response(200, {"checks": []})
        return build_stats_response(200, doctor_provider())

    if method == "GET" and parsed_url.path == "/api/provider-health":
        if provider_health_provider is None:
            return build_stats_response(200, {"check": None})
        return build_stats_response(200, provider_health_provider())

    if method == "POST" and parsed_url.path == "/api/clear-proxy-stats":
        stats_store.clear_proxy_stats()
        return build_stats_response(200, {"ok": True})

    return build_stats_response(404, {"error": "not found"})


WEB_DIR = Path(__file__).resolve().parents[1] / "web"
DASHBOARD_HTML_FILE = WEB_DIR / "dashboard.html"
ASSET_CONTENT_TYPES = {
    "/assets/dashboard.css": (WEB_DIR / "dashboard.css", "text/css; charset=utf-8"),
    "/assets/dashboard.js": (
        WEB_DIR / "dashboard.js",
        "application/javascript; charset=utf-8",
    ),
}


async def start_stats_server(stats_store, host=DASHBOARD_HOST, port=DASHBOARD_PORT):
    return await start_stats_server_with_status(stats_store, host, port)


async def start_stats_server_with_status(
    stats_store,
    host=DASHBOARD_HOST,
    port=DASHBOARD_PORT,
    status_provider=None,
    whitelist_provider=None,
    doctor_provider=None,
    provider_health_provider=None,
):
    server = await asyncio.start_server(
        lambda reader, writer: _handle_client(
            reader,
            writer,
            stats_store,
            status_provider,
            whitelist_provider,
            doctor_provider,
            provider_health_provider,
        ),
        host,
        port,
    )
    return server


async def _handle_client(
    reader,
    writer,
    stats_store,
    status_provider=None,
    whitelist_provider=None,
    doctor_provider=None,
    provider_health_provider=None,
):
    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=5)
        if not request_line:
            writer.close()
            return

        parts = request_line.decode("latin-1", errors="replace").strip().split()
        method = parts[0] if len(parts) > 0 else "GET"
        target = parts[1] if len(parts) > 1 else "/"

        content_length = 0
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            header = line.decode("latin-1", errors="replace").strip()
            name, separator, value = header.partition(":")
            if separator and name.lower() == "content-length":
                try:
                    content_length = int(value.strip())
                except ValueError:
                    content_length = 0

        request_body = b""
        if content_length > 0:
            request_body = await reader.readexactly(content_length)

        status, headers, body = handle_stats_request(
            method,
            urlparse(target),
            stats_store,
            status_provider=status_provider,
            whitelist_provider=whitelist_provider,
            doctor_provider=doctor_provider,
            provider_health_provider=provider_health_provider,
            request_body=request_body,
        )
        reason = {
            200: "OK",
            201: "Created",
            404: "Not Found",
        }.get(status, "OK")
        header_lines = [
            f"HTTP/1.1 {status} {reason}",
            f"Content-Length: {len(body)}",
            "Connection: close",
        ]
        for key, value in headers.items():
            header_lines.append(f"{key}: {value}")
        writer.write(("\r\n".join(header_lines) + "\r\n\r\n").encode() + body)
        await writer.drain()
    finally:
        writer.close()
