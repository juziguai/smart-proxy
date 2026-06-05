import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import DEFAULT_CONFIG
from .provider_classifier import classify_provider, get_provider_rules_status
from .stats_store import SLOW_REQUEST_THRESHOLD_MS


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


def ratio_text(count, total):
    return f"{count / total * 100:.1f}%" if total else "0.0%"


def build_provider_ranking_from_activity(activity):
    provider_counts = {}
    provider_meta = {}
    for row in activity:
        provider = row["provider"]
        if provider not in provider_counts:
            provider_counts[provider] = {
                "count": 0,
                "failed_requests": 0,
                "slow_requests": 0,
                "last_seen_at": "",
            }
            provider_meta[provider] = {
                key: row.get(key)
                for key in (
                    "provider_key",
                    "provider",
                    "provider_name",
                    "provider_kind",
                    "is_model_provider",
                    "provider_source",
                    "provider_match",
                    "provider_evidence",
                    "provider_confidence",
                )
            }
        bucket = provider_counts[provider]
        bucket["count"] += 1
        if not row.get("success"):
            bucket["failed_requests"] += 1
        if (row.get("connect_latency_ms") or 0) >= SLOW_REQUEST_THRESHOLD_MS:
            bucket["slow_requests"] += 1
        if row.get("started_at") and row["started_at"] > bucket["last_seen_at"]:
            bucket["last_seen_at"] = row["started_at"]

    total = sum(item["count"] for item in provider_counts.values())
    ranking = []
    for provider, bucket in sorted(
        provider_counts.items(),
        key=lambda item: item[1]["count"],
        reverse=True,
    ):
        count = bucket["count"]
        ranking.append(
            {
                **provider_meta[provider],
                **bucket,
                "ratio": ratio_text(count, total),
                "failure_rate": ratio_text(bucket["failed_requests"], count),
            }
        )
    return ranking


def is_real_claude_host(row):
    host = (row.get("host") or "").strip().lower()
    return host not in {"", "unknown", "(unknown)", "-"}


def is_claude_noise(row):
    return (
        not is_real_claude_host(row)
        or (row.get("stage") or "") in {"client_closed", "read_timeout", "parse_failed"}
    )


def build_process_topology(pids):
    primary = None
    for item in pids:
        if item.get("chain"):
            primary = item
            break
    primary = primary or (pids[0] if pids else None)
    if not primary:
        return {"nodes": [], "evidence": "waiting for process chain evidence"}

    chain = primary.get("chain") or primary.get("process") or "unknown process"
    names = [part.strip() for part in chain.split(">") if part.strip()]
    if not names:
        names = [primary.get("process") or "unknown process"]
    nodes = []
    for index, name in enumerate(names):
        nodes.append(
            {
                "label": name,
                "pid": primary.get("pid") if index == len(names) - 1 else None,
                "role": "cli" if index == len(names) - 1 else "parent",
            }
        )
    return {
        "nodes": nodes,
        "evidence": primary.get("evidence") or "Claude Code telemetry match",
    }


def build_claude_code_panel(activity, provider_ranking, unknown_hosts):
    meaningful_activity = [row for row in activity if not is_claude_noise(row)]
    model_activity = [row for row in meaningful_activity if row.get("is_model_provider")]
    total = len(meaningful_activity)
    failed = sum(1 for row in meaningful_activity if not row.get("success"))
    slow = sum(
        1
        for row in meaningful_activity
        if (row.get("connect_latency_ms") or 0) >= SLOW_REQUEST_THRESHOLD_MS
    )
    model_provider_mix = [
        item for item in provider_ranking if item.get("is_model_provider")
    ]

    pids = []
    seen_pids = set()
    for row in activity:
        pid = row.get("client_pid")
        if not pid or pid in seen_pids:
            continue
        seen_pids.add(pid)
        pids.append(
            {
                "pid": pid,
                "process": row.get("client_process") or "unknown",
                "evidence": row.get("client_evidence") or "Claude Code telemetry match",
                "chain": row.get("client_chain") or "",
            }
        )
        if len(pids) >= 5:
            break

    recent_errors = []
    for row in meaningful_activity:
        if row.get("success") and not row.get("error"):
            continue
        recent_errors.append(
            {
                "started_at": row.get("started_at") or "",
                "host": row.get("host") or "",
                "provider": row.get("provider") or "Unknown Provider",
                "stage": row.get("stage") or "",
                "error": row.get("error") or "request failed",
            }
        )
        if len(recent_errors) >= 5:
            break

    last_switch = None
    previous = None
    for row in reversed(model_activity):
        if not row.get("is_model_provider"):
            continue
        current = {
            "key": row.get("provider_key"),
            "provider": row.get("provider"),
            "started_at": row.get("started_at"),
        }
        if previous and current["key"] != previous["key"]:
            last_switch = {
                "started_at": current["started_at"],
                "from": previous["provider"],
                "to": current["provider"],
            }
        previous = current

    return {
        "total_requests": total,
        "failed_requests": failed,
        "failure_rate": ratio_text(failed, total),
        "slow_requests": slow,
        "current_pids": pids,
        "process_topology": build_process_topology(pids),
        "provider_mix": model_provider_mix,
        "unknown_hosts": unknown_hosts,
        "recent_errors": recent_errors,
        "last_provider_switch": last_switch,
        "ignored_noise_requests": len(activity) - len(meaningful_activity),
        "capability_boundary": (
            "代理层基于 CONNECT Host 判断访问了哪个服务商；今日 Token 来自可选 MITM "
            "Token Capture 解密后的模型 API usage 字段。prompt 内容不进入 Dashboard 统计。"
        ),
    }


def handle_stats_request(
    method,
    parsed_url,
    stats_store,
    status_provider=None,
    whitelist_provider=None,
    blocklist_provider=None,
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
        source = (params.get("source") or [""])[0]
        return build_stats_response(
            200,
            {
                "requests": stats_store.get_recent_proxy_requests(
                    limit=limit,
                    source=source,
                )
            },
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

    if method == "GET" and parsed_url.path == "/api/blocklist":
        if blocklist_provider is None:
            return build_stats_response(
                200,
                {"entries": [], "path": "", "count": 0, "loaded_at": ""},
            )
        return build_stats_response(200, blocklist_provider.get())

    if method == "POST" and parsed_url.path == "/api/blocklist":
        if blocklist_provider is None:
            return build_stats_response(503, {"error": "blocklist unavailable"})
        try:
            payload = json.loads(request_body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            return build_stats_response(400, {"error": "invalid json"})
        try:
            return build_stats_response(200, blocklist_provider.save(payload))
        except ValueError as exc:
            return build_stats_response(400, {"error": str(exc)})

    if method == "GET" and parsed_url.path == "/api/traffic-analytics":
        params = parse_qs(parsed_url.query)
        range_name = (params.get("range") or ["today"])[0]

        from datetime import datetime, timedelta, time as datetime_time
        current = datetime.now().astimezone()
        if range_name == "3days":
            start_date = current.date() - timedelta(days=2)
            start = datetime.combine(start_date, datetime_time.min, tzinfo=current.tzinfo)
        else:
            # 默认或 today：提取今日 00:00 至今
            start = datetime.combine(current.date(), datetime_time.min, tzinfo=current.tzinfo)

        since_iso = start.isoformat()
        raw_data = stats_store.get_traffic_ranking(since_iso)

        # 1. 软件分类占比统计
        software_raw = raw_data.get("software", [])
        total_software_requests = sum(s["count"] for s in software_raw)
        software_ranking = []
        for s in software_raw:
            pct = f"{s['count'] / total_software_requests * 100:.1f}%" if total_software_requests > 0 else "0.0%"
            software_ranking.append({
                "process": s["process"],
                "count": s["count"],
                "ratio": pct
            })

        def provider_ranking_from_hosts(host_rows):
            provider_counts = {}
            provider_meta = {}
            for host_row in host_rows:
                meta = classify_provider(host_row["host"])
                provider = meta["provider"]
                provider_counts[provider] = provider_counts.get(provider, 0) + host_row["count"]
                provider_meta[provider] = meta

            total_requests = sum(provider_counts.values())
            ranking = []
            for provider, count in sorted(provider_counts.items(), key=lambda x: x[1], reverse=True):
                pct = f"{count / total_requests * 100:.1f}%" if total_requests > 0 else "0.0%"
                ranking.append({
                    **provider_meta[provider],
                    "provider": provider,
                    "count": count,
                    "ratio": pct,
                })
            return ranking

        provider_ranking = provider_ranking_from_hosts(raw_data.get("host", []))
        claude_activity = raw_data.get("claude_code_activity", [])
        claude_meaningful_activity = [
            row for row in claude_activity if not is_claude_noise(row)
        ]
        claude_code_provider_ranking = (
            build_provider_ranking_from_activity(claude_meaningful_activity)
            if claude_meaningful_activity
            else provider_ranking_from_hosts(raw_data.get("claude_code_host", []))
        )
        claude_code_unknown_hosts = []
        unknown_host_counts = {}
        for row in claude_meaningful_activity:
            if row.get("provider_key") == "unknown" and is_real_claude_host(row):
                host = row.get("host") or "unknown"
                unknown_host_counts[host] = unknown_host_counts.get(host, 0) + 1
        if unknown_host_counts:
            for host, count in sorted(unknown_host_counts.items(), key=lambda item: item[1], reverse=True):
                meta = classify_provider(host)
                claude_code_unknown_hosts.append(
                    {
                        "host": host,
                        "count": count,
                        **meta,
                    }
                )
        else:
            for host_row in raw_data.get("claude_code_host", []):
                meta = classify_provider(host_row["host"])
                if meta["provider_key"] == "unknown":
                    claude_code_unknown_hosts.append(
                        {
                            "host": host_row["host"],
                            "count": host_row["count"],
                            **meta,
                        }
                    )

        claude_total = len(claude_meaningful_activity)
        claude_model_total = sum(
            item["count"]
            for item in claude_code_provider_ranking
            if item.get("is_model_provider")
        )

        return build_stats_response(
            200,
            {
                "software_ranking": software_ranking[:10],
                "provider_ranking": provider_ranking,
                "claude_code_provider_ranking": claude_code_provider_ranking,
                "claude_code_unknown_hosts": claude_code_unknown_hosts[:10],
                "claude_code_panel": build_claude_code_panel(
                    claude_activity,
                    claude_code_provider_ranking,
                    claude_code_unknown_hosts[:10],
                ),
                "claude_code_summary": {
                    "total_requests": claude_total,
                    "model_provider_requests": claude_model_total,
                    "unknown_provider_requests": sum(
                        item["count"] for item in claude_code_unknown_hosts
                    ),
                },
                "range": range_name,
                "since": since_iso
            }
        )

    if method == "GET" and parsed_url.path == "/api/provider-rules":
        return build_stats_response(200, get_provider_rules_status())

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

    if method == "POST" and parsed_url.path == "/api/prune-proxy-stats":
        params = parse_qs(parsed_url.query)
        keep_days = params.get("keep_days", ["7"])[0]
        try:
            result = stats_store.prune_proxy_stats(keep_days=keep_days)
        except (TypeError, ValueError) as exc:
            return build_stats_response(400, {"error": str(exc)})
        return build_stats_response(200, result)

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
    blocklist_provider=None,
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
            blocklist_provider,
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
    blocklist_provider=None,
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

        parsed_url = urlparse(target)
        if method == "GET" and parsed_url.path == "/api/doctor":
            if doctor_provider is None:
                res = {"checks": []}
            else:
                res = doctor_provider()
                if asyncio.iscoroutine(res):
                    res = await res
            status, headers, body = build_stats_response(200, res)
        else:
            status, headers, body = handle_stats_request(
                method,
                parsed_url,
                stats_store,
                status_provider=status_provider,
                whitelist_provider=whitelist_provider,
                blocklist_provider=blocklist_provider,
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
