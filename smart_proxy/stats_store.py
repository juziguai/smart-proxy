from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
import sqlite3

from .config import DEFAULT_CONFIG
from .pricing import aggregate_cost, estimate_usage_cost
from .provider_classifier import classify_provider


SLOW_REQUEST_THRESHOLD_MS = 3000
UPSTREAM_UNAVAILABLE_BATCH_WINDOW_SECONDS = 120
UPSTREAM_UNAVAILABLE_BATCH_MIN_COUNT = 3
HOST_FAILURE_RATE_THRESHOLD = 0.10
HOST_CRITICAL_FAILURE_RATE = 0.50
MODEL_API_SLOW_ALERT_MIN_COUNT = 2
DEVELOPER_SERVICE_SLOW_ALERT_MIN_COUNT = 10
GENERIC_SLOW_ALERT_MIN_COUNT = 5
UNKNOWN_HOST = "(unknown)"
UNPARSED_ROUTE = "unparsed"

MODEL_API_HOST_MARKERS = (
    "api.deepseek.com",
    "api.minimaxi.com",
    "api.anthropic.com",
    "platform.xiaomimimo.com",
)
DEVELOPER_SERVICE_HOST_MARKERS = (
    "github.com",
    "api.github.com",
)
CONTENT_SITE_HOST_MARKERS = (
    "douyin.com",
)

LOCAL_UPSTREAM_HOSTS = {
    "127.0.0.1",
    "localhost",
    "::1",
}


@dataclass(frozen=True)
class ProxyRequestEvent:
    started_at: str
    completed_at: str
    method: str
    host: str
    route: str
    success: bool
    latency_ms: int
    error: str | None
    connect_latency_ms: int | None = None
    duration_ms: int | None = None
    stage: str = "completed"
    client_addr: str = ""
    client_port: int | None = None
    target_port: int | None = None
    upstream_host: str = ""
    upstream_port: int | None = None
    client_pid: int | None = None
    client_process: str = ""
    client_exe: str = ""
    client_label: str = ""
    client_evidence: str = ""
    client_chain: str = ""
    user_agent: str = ""


def _normalize_disabled_hosts(hosts):
    normalized = []
    for host in hosts or []:
        text = str(host).strip().lower()
        if text:
            normalized.append(text)
    return tuple(dict.fromkeys(normalized))


class StatsStore:
    def __init__(self, db_path, disabled_service_hosts=None):
        self._db_path = Path(db_path)
        self._disabled_service_hosts = _normalize_disabled_hosts(
            DEFAULT_CONFIG.disabled_service_hosts
            if disabled_service_hosts is None
            else disabled_service_hosts
        )
        self._init_schema()

    def record_proxy_request(self, event: ProxyRequestEvent):
        connect_latency_ms = (
            event.connect_latency_ms
            if event.connect_latency_ms is not None
            else event.latency_ms
        )
        duration_ms = (
            event.duration_ms
            if event.duration_ms is not None
            else event.latency_ms
        )
        with self._connection() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO proxy_requests (
                        started_at,
                        completed_at,
                        method,
                        host,
                        route,
                        success,
                        latency_ms,
                        connect_latency_ms,
                        duration_ms,
                        stage,
                        client_addr,
                        client_port,
                        target_port,
                        upstream_host,
                        upstream_port,
                        client_pid,
                        client_process,
                        client_exe,
                        client_label,
                        client_evidence,
                        client_chain,
                        user_agent,
                        error
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.started_at,
                        event.completed_at,
                        event.method,
                        event.host,
                        event.route,
                        1 if event.success else 0,
                        event.latency_ms,
                        connect_latency_ms,
                        duration_ms,
                        event.stage,
                        event.client_addr,
                        event.client_port,
                        event.target_port,
                        event.upstream_host,
                        event.upstream_port,
                        event.client_pid,
                        event.client_process,
                        event.client_exe,
                        event.client_label,
                        event.client_evidence,
                        event.client_chain,
                        event.user_agent,
                        event.error,
                    ),
                )

    def get_summary(self, range_name, now=None, since=None):
        since = since or self._since_for_range(range_name, now)
        proxy = self._get_proxy_summary(since)
        usage = self._get_usage_summary(since)
        comparison = self._get_summary_comparison(range_name, now, since)
        return {"proxy": proxy, "usage": usage, "comparison": comparison}

    def get_recent_proxy_requests(self, limit=50, since=None, source=None):
        limit = max(1, min(int(limit), 200))
        alertable_request_expr = self._alertable_request_expr()
        where_parts = []
        params = []
        if since:
            where_parts.append("started_at >= ?")
            params.append(self._indexed_time_value(since))
        source = (source or "").strip().lower()
        if source:
            source_expr = """
                LOWER(
                    COALESCE(NULLIF(client_label, ''), 'Unknown')
                    || ' / '
                    || COALESCE(NULLIF(client_process, ''), 'unknown')
                )
            """
            where_parts.append(
                f"""(
                    LOWER(COALESCE(NULLIF(client_label, ''), 'Unknown')) = ?
                    OR LOWER(COALESCE(NULLIF(client_process, ''), 'unknown')) = ?
                    OR {source_expr} = ?
                )"""
            )
            params.extend([source, source, source])
        where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        params.append(limit)

        with self._connection() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    started_at,
                    completed_at,
                    method,
                    host,
                    route,
                    success,
                    latency_ms,
                    connect_latency_ms,
                    duration_ms,
                    stage,
                    client_addr,
                    client_port,
                    target_port,
                    upstream_host,
                    upstream_port,
                    client_pid,
                    client_process,
                    client_exe,
                    client_label,
                    client_evidence,
                    client_chain,
                    user_agent,
                    error,
                    ({alertable_request_expr}) AS alertable
                FROM proxy_requests
                {where}
                ORDER BY started_at DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        requests = []
        for row in rows:
            provider = classify_provider(row["host"])
            requests.append({
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "method": row["method"],
                "host": row["host"],
                **provider,
                "route": row["route"],
                "success": bool(row["success"]),
                "latency_ms": int(row["latency_ms"]),
                "connect_latency_ms": (
                    int(row["connect_latency_ms"])
                    if row["connect_latency_ms"] is not None
                    else None
                ),
                "duration_ms": int(row["duration_ms"]),
                "stage": row["stage"] or "completed",
                "client_addr": row["client_addr"] or "",
                "client_port": (
                    int(row["client_port"])
                    if row["client_port"] is not None
                    else None
                ),
                "target_port": (
                    int(row["target_port"])
                    if row["target_port"] is not None
                    else None
                ),
                "upstream_host": row["upstream_host"] or "",
                "upstream_port": (
                    int(row["upstream_port"])
                    if row["upstream_port"] is not None
                    else None
                ),
                "client_pid": (
                    int(row["client_pid"])
                    if row["client_pid"] is not None
                    else None
                ),
                "client_process": row["client_process"] or "",
                "client_exe": row["client_exe"] or "",
                "client_label": row["client_label"] or "Unknown",
                "client_evidence": row["client_evidence"] or "",
                "client_chain": row["client_chain"] or "",
                "user_agent": row["user_agent"] or "",
                "slow": (
                    bool(row["alertable"])
                    and
                    row["connect_latency_ms"] is not None
                    and int(row["connect_latency_ms"]) >= SLOW_REQUEST_THRESHOLD_MS
                ),
                "alertable": bool(row["alertable"]),
                "error": row["error"],
                "diagnosis": "",
                "diagnosis_label": "",
                "diagnosis_detail": "",
                "diagnosis_batch_count": 0,
            })
        self._annotate_proxy_request_batches(requests)
        return requests

    def get_whitelist_candidates(self, limit=10, since=None):
        limit = max(1, min(int(limit), 50))
        where, params = self._time_window_clause("started_at", since)
        connect_latency_expr = self._connect_latency_expr()

        with self._connection() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    host,
                    COUNT(*) AS total_requests,
                    COALESCE(SUM(CASE WHEN route = 'proxy' THEN 1 ELSE 0 END), 0)
                        AS proxy_requests,
                    COALESCE(SUM(CASE WHEN route = 'direct_whitelist' THEN 1 ELSE 0 END), 0)
                        AS whitelist_requests,
                    COALESCE(SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END), 0)
                        AS failed_requests,
                    COALESCE(SUM(CASE WHEN {connect_latency_expr} >= ? THEN 1 ELSE 0 END), 0)
                        AS slow_requests,
                    COALESCE(AVG({connect_latency_expr}), 0)
                        AS average_connect_latency_ms
                FROM proxy_requests
                {where}
                GROUP BY host
                HAVING proxy_requests > 0
                ORDER BY proxy_requests DESC, slow_requests DESC, average_connect_latency_ms DESC
                LIMIT ?
                """,
                [SLOW_REQUEST_THRESHOLD_MS] + params + [limit],
            ).fetchall()

        return [
            {
                "host": row["host"],
                "total_requests": int(row["total_requests"]),
                "proxy_requests": int(row["proxy_requests"]),
                "whitelist_requests": int(row["whitelist_requests"]),
                "failed_requests": int(row["failed_requests"]),
                "slow_requests": int(row["slow_requests"]),
                "average_connect_latency_ms": int(
                    row["average_connect_latency_ms"]
                ),
            }
            for row in rows
        ]

    def get_trends(self, range_name, now=None, since=None, models=None):
        since = since or self._since_for_range(range_name, now)
        interval = "hour" if range_name == "day" else "day"
        buckets = {}
        models = [model for model in (models or []) if model]
        alertable_request_expr = self._alertable_request_expr()

        with self._connection() as conn:
            proxy_rows = conn.execute(
                self._range_query(
                    f"""
                    SELECT
                        started_at,
                        success,
                        ({alertable_request_expr}) AS alertable,
                        {self._connect_latency_expr()} AS connect_latency_ms,
                        COALESCE(duration_ms, latency_ms) AS duration_ms
                    FROM proxy_requests
                    """,
                    "started_at",
                    since,
                ),
                [self._indexed_time_value(since)] if since else [],
            ).fetchall()
            usage_sql, usage_params = self._usage_trends_query(since, models)
            usage_rows = conn.execute(usage_sql, usage_params).fetchall()

        for row in proxy_rows:
            is_meaningful = bool(row["success"]) or bool(row["alertable"])
            if not is_meaningful:
                continue
            bucket = self._bucket_key(row["started_at"], interval)
            item = self._trend_bucket(buckets, bucket)
            item["proxy_requests"] += 1
            item["failed_requests"] += 0 if row["success"] else 1
            if row["connect_latency_ms"] is not None:
                item["_latency_sum"] += int(row["connect_latency_ms"])
                item["_latency_count"] += 1
            if row["duration_ms"] is not None:
                item["_duration_sum"] += int(row["duration_ms"])
                item["_duration_count"] += 1

        for row in usage_rows:
            bucket = self._bucket_key(row["timestamp"], interval)
            item = self._trend_bucket(buckets, bucket)
            usage = {
                "input_tokens": int(row["input_tokens"]),
                "output_tokens": int(row["output_tokens"]),
                "cache_read_input_tokens": int(
                    row["cache_read_input_tokens"]
                ),
                "cache_creation_input_tokens": int(
                    row["cache_creation_input_tokens"]
                ),
            }
            cost = estimate_usage_cost(row["model"], usage)
            item["input_tokens"] += usage["input_tokens"]
            item["output_tokens"] += usage["output_tokens"]
            item["total_tokens"] += (
                usage["input_tokens"] + usage["output_tokens"]
            )
            item["cache_read_input_tokens"] += usage[
                "cache_read_input_tokens"
            ]
            item["cache_creation_input_tokens"] += usage[
                "cache_creation_input_tokens"
            ]
            item["estimated_cost"] += cost["total"]

        result = []
        for bucket in sorted(buckets):
            item = buckets[bucket]
            if item["_latency_count"]:
                item["average_latency_ms"] = int(
                    item["_latency_sum"] / item["_latency_count"]
                )
                item["average_connect_latency_ms"] = item["average_latency_ms"]
            if item["_duration_count"]:
                item["average_duration_ms"] = int(
                    item["_duration_sum"] / item["_duration_count"]
                )
            del item["_latency_sum"]
            del item["_latency_count"]
            del item["_duration_sum"]
            del item["_duration_count"]
            result.append(item)

        return {
            "range": range_name,
            "interval": interval,
            "currency": "CNY",
            "models": models,
            "points": result,
        }

    def upsert_usage_event(self, event):
        self.upsert_usage_events([event])

    def upsert_usage_events(self, events):
        if not events:
            return
        with self._connection() as conn:
            with conn:
                conn.executemany(
                    """
                    INSERT INTO usage_events (
                        source_file,
                        source_line,
                        timestamp,
                        session_id,
                        model,
                        input_tokens,
                        output_tokens,
                        cache_read_input_tokens,
                        cache_creation_input_tokens,
                        web_search_requests,
                        web_fetch_requests,
                        service_tier,
                        speed
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_file, source_line) DO UPDATE SET
                        timestamp = excluded.timestamp,
                        session_id = excluded.session_id,
                        model = excluded.model,
                        input_tokens = excluded.input_tokens,
                        output_tokens = excluded.output_tokens,
                        cache_read_input_tokens =
                            excluded.cache_read_input_tokens,
                        cache_creation_input_tokens =
                            excluded.cache_creation_input_tokens,
                        web_search_requests = excluded.web_search_requests,
                        web_fetch_requests = excluded.web_fetch_requests,
                        service_tier = excluded.service_tier,
                        speed = excluded.speed
                    """,
                    [
                        (
                        event.source_file,
                        event.source_line,
                        event.timestamp,
                        event.session_id,
                        event.model,
                        event.input_tokens,
                        event.output_tokens,
                        event.cache_read_input_tokens,
                        event.cache_creation_input_tokens,
                        event.web_search_requests,
                        event.web_fetch_requests,
                        event.service_tier,
                        event.speed,
                        )
                        for event in events
                    ],
                )

    def _get_proxy_summary(self, since, until=None):
        where, params = self._time_window_clause("started_at", since, until)
        connect_latency_expr = self._connect_latency_expr()
        duration_expr = "COALESCE(duration_ms, latency_ms)"
        alertable_request_expr = self._alertable_request_expr()
        meaningful_request_expr = f"(success = 1 OR ({alertable_request_expr}))"

        with self._connection() as conn:
            row = conn.execute(
                f"""
                SELECT
                    COALESCE(SUM(CASE WHEN {meaningful_request_expr} THEN 1 ELSE 0 END), 0)
                        AS total_requests,
                    COALESCE(SUM(CASE WHEN success = 1 AND {meaningful_request_expr} THEN 1 ELSE 0 END), 0)
                        AS successful_requests,
                    COALESCE(SUM(CASE WHEN success = 0 AND ({alertable_request_expr}) THEN 1 ELSE 0 END), 0)
                        AS failed_requests,
                    COALESCE(AVG(CASE WHEN {meaningful_request_expr} THEN {connect_latency_expr} END), 0)
                        AS average_connect_latency_ms,
                    COALESCE(AVG(CASE WHEN {meaningful_request_expr} THEN {duration_expr} END), 0)
                        AS average_duration_ms
                FROM proxy_requests
                {where}
                """,
                params,
            ).fetchone()
            blocked_row = conn.execute(
                f"""
                SELECT COUNT(*) AS blocked_requests
                FROM proxy_requests
                {where} {'AND' if where else 'WHERE'} route = 'blocked'
                """,
                params,
            ).fetchone()
            route_rows = conn.execute(
                f"""
                SELECT route, COUNT(*) AS count
                FROM proxy_requests
                {where}
                GROUP BY route
                ORDER BY route
                """,
                params,
            ).fetchall()
            host_rows = conn.execute(
                f"""
                SELECT
                    host,
                    route,
                    COUNT(*) AS total_requests,
                    COALESCE(SUM(success), 0) AS successful_requests,
                    COALESCE(SUM(CASE WHEN success = 0 AND ({alertable_request_expr}) THEN 1 ELSE 0 END), 0)
                        AS failed_requests,
                    COALESCE(SUM(CASE WHEN success = 0 AND error IS NOT NULL AND error <> '' AND ({alertable_request_expr}) THEN 1 ELSE 0 END), 0)
                        AS alert_failed_requests,
                    COALESCE(SUM(CASE WHEN ({alertable_request_expr}) AND {connect_latency_expr} >= ? THEN 1 ELSE 0 END), 0)
                        AS slow_requests,
                    COALESCE(AVG({connect_latency_expr}), 0)
                        AS average_connect_latency_ms,
                    COALESCE(AVG({duration_expr}), 0) AS average_duration_ms,
                    COALESCE(SUM(CASE WHEN {connect_latency_expr} IS NOT NULL THEN 1 ELSE 0 END), 0)
                        AS connect_latency_samples
                FROM proxy_requests
                {where}
                GROUP BY host, route
                ORDER BY COUNT(*) DESC, AVG({connect_latency_expr}) DESC
                """,
                [SLOW_REQUEST_THRESHOLD_MS] + params,
            ).fetchall()
            client_rows = conn.execute(
                f"""
                SELECT
                    COALESCE(NULLIF(client_label, ''), 'Unknown') AS client_label,
                    COALESCE(NULLIF(client_process, ''), 'unknown') AS client_process,
                    COUNT(*) AS total_requests,
                    COALESCE(SUM(success), 0) AS successful_requests,
                    COALESCE(SUM(CASE WHEN success = 0 AND ({alertable_request_expr}) THEN 1 ELSE 0 END), 0)
                        AS failed_requests,
                    COALESCE(SUM(CASE WHEN ({alertable_request_expr}) AND {connect_latency_expr} >= ? THEN 1 ELSE 0 END), 0)
                        AS slow_requests,
                    COALESCE(AVG({connect_latency_expr}), 0)
                        AS average_connect_latency_ms,
                    COALESCE(AVG({duration_expr}), 0) AS average_duration_ms
                FROM proxy_requests
                {where}
                GROUP BY client_label, client_process
                ORDER BY COUNT(*) DESC, failed_requests DESC, average_connect_latency_ms DESC
                LIMIT 20
                """,
                [SLOW_REQUEST_THRESHOLD_MS] + params,
            ).fetchall()
            incident_batches = self._get_proxy_incident_batches(conn, since, until)

        total_requests = int(row["total_requests"])
        successful_requests = int(row["successful_requests"])
        failed_requests = int(row["failed_requests"])
        blocked_requests = int(blocked_row["blocked_requests"])
        average_connect_latency_ms = int(row["average_connect_latency_ms"])
        average_duration_ms = int(row["average_duration_ms"])
        average_latency_ms = average_connect_latency_ms
        success_rate = (
            successful_requests / total_requests if total_requests else 0
        )

        hosts = {}
        for host_row in host_rows:
            host = host_row["host"]
            if host not in hosts:
                hosts[host] = {
                    "host": host,
                    "total_requests": 0,
                    "successful_requests": 0,
                    "failed_requests": 0,
                    "alert_failed_requests": 0,
                    "slow_requests": 0,
                    "_latency_weighted_sum": 0,
                    "_latency_samples": 0,
                    "_duration_weighted_sum": 0,
                    "average_latency_ms": 0,
                    "average_connect_latency_ms": 0,
                    "average_duration_ms": 0,
                    "failure_rate": 0,
                    "alert_failure_rate": 0,
                    "slow_rate": 0,
                    "health": "ok",
                    "routes": {},
                }
            item = hosts[host]
            route_count = int(host_row["total_requests"])
            item["total_requests"] += route_count
            item["successful_requests"] += int(host_row["successful_requests"])
            item["failed_requests"] += int(host_row["failed_requests"])
            item["alert_failed_requests"] += int(
                host_row["alert_failed_requests"]
            )
            item["slow_requests"] += int(host_row["slow_requests"])
            connect_samples = int(host_row["connect_latency_samples"])
            item["_latency_weighted_sum"] += (
                int(host_row["average_connect_latency_ms"]) * connect_samples
            )
            item["_latency_samples"] += connect_samples
            item["_duration_weighted_sum"] += (
                int(host_row["average_duration_ms"]) * route_count
            )
            item["routes"][host_row["route"]] = route_count

        host_breakdown = []
        for item in hosts.values():
            if item["total_requests"]:
                if item["_latency_samples"]:
                    item["average_connect_latency_ms"] = int(
                        item["_latency_weighted_sum"] / item["_latency_samples"]
                    )
                    item["average_latency_ms"] = item["average_connect_latency_ms"]
                item["average_duration_ms"] = int(
                    item["_duration_weighted_sum"] / item["total_requests"]
                )
                item["failure_rate"] = (
                    item["failed_requests"] / item["total_requests"]
                )
                item["alert_failure_rate"] = (
                    item["alert_failed_requests"] / item["total_requests"]
                )
                item["slow_rate"] = (
                    item["slow_requests"] / item["total_requests"]
                )
            if item["alert_failure_rate"] >= HOST_CRITICAL_FAILURE_RATE:
                item["health"] = "critical"
            elif (
                item["alert_failure_rate"] >= HOST_FAILURE_RATE_THRESHOLD
                or item["slow_requests"] > 0
            ):
                item["health"] = "warning"
            del item["_latency_weighted_sum"]
            del item["_latency_samples"]
            del item["_duration_weighted_sum"]
            host_breakdown.append(item)
        host_breakdown.sort(
            key=lambda item: (
                item["failed_requests"],
                item["average_latency_ms"],
                item["total_requests"],
            ),
            reverse=True,
        )
        alerts = self._build_proxy_alerts(host_breakdown)
        slow_requests = sum(item["slow_requests"] for item in host_breakdown)
        clients = [
            {
                "client_label": row["client_label"],
                "client_process": row["client_process"],
                "total_requests": int(row["total_requests"]),
                "successful_requests": int(row["successful_requests"]),
                "failed_requests": int(row["failed_requests"]),
                "slow_requests": int(row["slow_requests"]),
                "average_connect_latency_ms": int(row["average_connect_latency_ms"]),
                "average_duration_ms": int(row["average_duration_ms"]),
            }
            for row in client_rows
        ]

        return {
            "total_requests": total_requests,
            "successful_requests": successful_requests,
            "failed_requests": failed_requests,
            "blocked_requests": blocked_requests,
            "slow_requests": slow_requests,
            "success_rate": success_rate,
            "average_latency_ms": average_latency_ms,
            "average_connect_latency_ms": average_connect_latency_ms,
            "average_duration_ms": average_duration_ms,
            "routes": {
                route_row["route"]: int(route_row["count"])
                for route_row in route_rows
            },
            "hosts": host_breakdown[:20],
            "clients": clients,
            "alerts": alerts,
            "incident_batches": incident_batches,
            "alert_counts": {
                "critical": sum(
                    1 for alert in alerts
                    if alert["severity"] == "critical"
                ),
                "warning": sum(
                    1 for alert in alerts
                    if alert["severity"] == "warning"
                ),
            },
        }

    def _get_proxy_incident_batches(self, conn, since=None, until=None):
        where, params = self._time_window_clause("started_at", since, until)
        conjunction = "AND" if where else "WHERE"
        rows = conn.execute(
            f"""
            SELECT
                started_at,
                host,
                route,
                success,
                stage,
                upstream_host,
                upstream_port,
                client_label,
                client_process,
                error
            FROM proxy_requests
            {where}
            {conjunction} success = 0
                AND route = 'proxy'
                AND stage = 'forward_failed'
                AND LOWER(COALESCE(upstream_host, '')) IN ('127.0.0.1', 'localhost', '::1')
                AND (
                    LOWER(COALESCE(error, '')) LIKE '%winerror 1225%'
                    OR LOWER(COALESCE(error, '')) LIKE '%connection refused%'
                    OR LOWER(COALESCE(error, '')) LIKE '%actively refused%'
                    OR COALESCE(error, '') LIKE '%远程计算机拒绝网络连接%'
                )
            ORDER BY started_at ASC
            """,
            params,
        ).fetchall()
        requests = [
            {
                "started_at": row["started_at"],
                "host": row["host"],
                "route": row["route"],
                "success": bool(row["success"]),
                "stage": row["stage"] or "",
                "upstream_host": row["upstream_host"] or "",
                "upstream_port": (
                    int(row["upstream_port"])
                    if row["upstream_port"] is not None
                    else None
                ),
                "client_label": row["client_label"] or "Unknown",
                "client_process": row["client_process"] or "unknown",
                "error": row["error"] or "",
            }
            for row in rows
        ]
        self._annotate_proxy_request_batches(requests)

        batches = {}
        for request in requests:
            if request.get("diagnosis") != "local_upstream_unavailable_batch":
                continue
            window = request.get("diagnosis_window") or {}
            key = (
                window.get("first_at") or "",
                window.get("last_at") or "",
                request.get("upstream_host") or "",
                request.get("upstream_port") or 0,
            )
            item = batches.setdefault(
                key,
                {
                    "kind": "local_upstream_unavailable_batch",
                    "label": "上游出口不可达批次",
                    "severity": "warning",
                    "count": 0,
                    "first_at": window.get("first_at") or "",
                    "last_at": window.get("last_at") or "",
                    "upstream": (
                        f"{request.get('upstream_host') or '-'}:"
                        f"{request.get('upstream_port') or '-'}"
                    ),
                    "hosts": {},
                    "clients": {},
                    "detail": request.get("diagnosis_detail") or "",
                },
            )
            item["count"] += 1
            host = request.get("host") or "unknown"
            client = (
                f"{request.get('client_label') or 'Unknown'} / "
                f"{request.get('client_process') or 'unknown'}"
            )
            item["hosts"][host] = item["hosts"].get(host, 0) + 1
            item["clients"][client] = item["clients"].get(client, 0) + 1

        incidents = []
        for item in batches.values():
            item["hosts"] = [
                {"host": host, "count": count}
                for host, count in sorted(
                    item["hosts"].items(),
                    key=lambda entry: entry[1],
                    reverse=True,
                )[:5]
            ]
            item["clients"] = [
                {"client": client, "count": count}
                for client, count in sorted(
                    item["clients"].items(),
                    key=lambda entry: entry[1],
                    reverse=True,
                )[:5]
            ]
            incidents.append(item)
        incidents.sort(key=lambda item: item["last_at"], reverse=True)
        return incidents[:5]

    def _build_proxy_alerts(self, hosts):
        alerts = []
        for host in hosts:
            if self._disabled_service_host_match(host["host"]):
                alerts.append(
                    {
                        "severity": "warning",
                        "kind": "disabled_service_host",
                        "host": host["host"],
                        "message": (
                            f"disabled service host still has "
                            f"{host['total_requests']} request(s)"
                        ),
                        "value": host["total_requests"],
                    }
                )
        for host in hosts[:10]:
            if (
                host["alert_failed_requests"] > 0
                and host["alert_failure_rate"] >= HOST_FAILURE_RATE_THRESHOLD
            ):
                severity = (
                    "critical"
                    if host["alert_failure_rate"] >= HOST_CRITICAL_FAILURE_RATE
                    else "warning"
                )
                alerts.append(
                    {
                        "severity": severity,
                        "kind": "host_failures",
                        "host": host["host"],
                        "message": (
                            f"{host['host']} failure rate "
                            f"{round(host['alert_failure_rate'] * 100)}%"
                        ),
                        "value": host["alert_failure_rate"],
                    }
                )
            if self._should_alert_slow_host(host):
                alerts.append(
                    {
                        "severity": "warning",
                        "kind": "slow_requests",
                        "host": host["host"],
                        "message": (
                            f"{host['host']} has {host['slow_requests']} "
                            f"connect(s) >= {SLOW_REQUEST_THRESHOLD_MS}ms"
                        ),
                        "value": host["slow_requests"],
                    }
                )
        return alerts[:8]

    def _annotate_proxy_request_batches(self, requests):
        candidates = [
            request
            for request in requests
            if self._looks_like_local_upstream_refusal(request)
        ]
        if len(candidates) < UPSTREAM_UNAVAILABLE_BATCH_MIN_COUNT:
            return

        batches_by_upstream = {}
        for request in candidates:
            key = (
                request.get("upstream_host") or "",
                request.get("upstream_port") or 0,
            )
            batches_by_upstream.setdefault(key, []).append(request)

        for batch in batches_by_upstream.values():
            ordered = sorted(
                batch,
                key=lambda item: self._request_timestamp(item.get("started_at")),
            )
            current = []
            for request in ordered:
                if not current:
                    current = [request]
                    continue
                first_at = self._request_timestamp(current[0].get("started_at"))
                request_at = self._request_timestamp(request.get("started_at"))
                if (
                    request_at - first_at
                    <= UPSTREAM_UNAVAILABLE_BATCH_WINDOW_SECONDS
                ):
                    current.append(request)
                else:
                    self._mark_upstream_unavailable_batch(current)
                    current = [request]
            self._mark_upstream_unavailable_batch(current)

    def _mark_upstream_unavailable_batch(self, batch):
        if len(batch) < UPSTREAM_UNAVAILABLE_BATCH_MIN_COUNT:
            return
        first_at = batch[0].get("started_at") or ""
        last_at = batch[-1].get("started_at") or ""
        upstream = (
            f"{batch[0].get('upstream_host') or '-'}:"
            f"{batch[0].get('upstream_port') or '-'}"
        )
        detail = (
            f"{len(batch)} 条请求在短时间内经本地上游 {upstream} "
            "forward_failed 并被拒绝，疑似上游出口代理当时不可达"
        )
        for request in batch:
            request["diagnosis"] = "local_upstream_unavailable_batch"
            request["diagnosis_label"] = "上游出口不可达批次"
            request["diagnosis_detail"] = detail
            request["diagnosis_batch_count"] = len(batch)
            request["diagnosis_window"] = {
                "first_at": first_at,
                "last_at": last_at,
            }

    def _looks_like_local_upstream_refusal(self, request):
        if request.get("success"):
            return False
        if request.get("route") != "proxy":
            return False
        if request.get("stage") != "forward_failed":
            return False
        upstream_host = (request.get("upstream_host") or "").lower()
        if upstream_host not in LOCAL_UPSTREAM_HOSTS:
            return False
        error = (request.get("error") or "").lower()
        refused_markers = (
            "winerror 1225",
            "connection refused",
            "actively refused",
            "远程计算机拒绝网络连接",
        )
        return any(marker in error for marker in refused_markers)

    def _request_timestamp(self, value):
        if not value:
            return 0.0
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            return 0.0

    def _disabled_service_host_match(self, host):
        host = (host or "").lower()
        if not host or not self._disabled_service_hosts:
            return False
        for pattern in self._disabled_service_hosts:
            if host == pattern or host.endswith(f".{pattern}"):
                return True
        return False

    def _should_alert_slow_host(self, host):
        slow_requests = host["slow_requests"]
        if slow_requests <= 0:
            return False

        category = self._alert_host_category(host["host"])
        if category == "model_api":
            return slow_requests >= MODEL_API_SLOW_ALERT_MIN_COUNT
        if category == "developer_service":
            return slow_requests >= DEVELOPER_SERVICE_SLOW_ALERT_MIN_COUNT
        if category == "content_site":
            return False
        return slow_requests >= GENERIC_SLOW_ALERT_MIN_COUNT

    def _alert_host_category(self, host):
        value = (host or "").lower()
        if any(marker in value for marker in MODEL_API_HOST_MARKERS):
            return "model_api"
        if any(marker in value for marker in DEVELOPER_SERVICE_HOST_MARKERS):
            return "developer_service"
        if any(marker in value for marker in CONTENT_SITE_HOST_MARKERS):
            return "content_site"
        return "generic"

    def _get_usage_summary(self, since, until=None):
        where, params = self._usage_time_window_clause(since, until)

        with self._connection() as conn:
            row = conn.execute(
                f"""
                SELECT
                    COALESCE(SUM(input_tokens), 0) AS input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS output_tokens,
                    COALESCE(SUM(cache_read_input_tokens), 0)
                        AS cache_read_input_tokens,
                    COALESCE(SUM(cache_creation_input_tokens), 0)
                        AS cache_creation_input_tokens,
                    COALESCE(SUM(web_search_requests), 0)
                        AS web_search_requests,
                    COALESCE(SUM(web_fetch_requests), 0)
                        AS web_fetch_requests
                FROM usage_events
                {where}
                """,
                params,
            ).fetchone()
            model_rows = conn.execute(
                f"""
                SELECT
                    model,
                    COALESCE(SUM(input_tokens), 0) AS input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS output_tokens,
                    COALESCE(SUM(cache_read_input_tokens), 0)
                        AS cache_read_input_tokens,
                    COALESCE(SUM(cache_creation_input_tokens), 0)
                        AS cache_creation_input_tokens,
                    COALESCE(SUM(web_search_requests), 0)
                        AS web_search_requests,
                    COALESCE(SUM(web_fetch_requests), 0)
                        AS web_fetch_requests
                FROM usage_events
                {where}
                GROUP BY model
                ORDER BY model
                """,
                params,
            ).fetchall()

        input_tokens = int(row["input_tokens"])
        output_tokens = int(row["output_tokens"])
        models = {}
        for model_row in model_rows:
            model_input = int(model_row["input_tokens"])
            model_output = int(model_row["output_tokens"])
            model_usage = {
                "input_tokens": model_input,
                "output_tokens": model_output,
                "total_tokens": model_input + model_output,
                "cache_read_input_tokens": int(
                    model_row["cache_read_input_tokens"]
                ),
                "cache_creation_input_tokens": int(
                    model_row["cache_creation_input_tokens"]
                ),
                "web_search_requests": int(model_row["web_search_requests"]),
                "web_fetch_requests": int(model_row["web_fetch_requests"]),
            }
            model_usage["cost"] = estimate_usage_cost(
                model_row["model"],
                model_usage,
            )
            models[model_row["model"]] = model_usage

        cost = aggregate_cost({
            model: usage["cost"] for model, usage in models.items()
        })

        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cache_read_input_tokens": int(row["cache_read_input_tokens"]),
            "cache_creation_input_tokens": int(
                row["cache_creation_input_tokens"]
            ),
            "web_search_requests": int(row["web_search_requests"]),
            "web_fetch_requests": int(row["web_fetch_requests"]),
            "cost": cost,
            "models": models,
        }

    def _get_summary_comparison(self, range_name, now, since):
        previous_window = self._previous_window_for_range(range_name, now, since)
        if previous_window is None:
            return {
                "label": "全量统计",
                "available": False,
                "previous": None,
            }

        previous_since, previous_until, label = previous_window
        previous_proxy = self._get_proxy_summary(previous_since, previous_until)
        previous_usage = self._get_usage_summary(previous_since, previous_until)
        return {
            "label": label,
            "available": True,
            "previous": {
                "proxy": self._comparison_proxy(previous_proxy),
                "usage": self._comparison_usage(previous_usage),
            },
        }

    def _comparison_proxy(self, proxy):
        return {
            "total_requests": proxy["total_requests"],
            "successful_requests": proxy["successful_requests"],
            "failed_requests": proxy["failed_requests"],
            "slow_requests": proxy["slow_requests"],
            "success_rate": proxy["success_rate"],
            "average_latency_ms": proxy["average_latency_ms"],
            "average_connect_latency_ms": proxy["average_connect_latency_ms"],
            "average_duration_ms": proxy["average_duration_ms"],
        }

    def _comparison_usage(self, usage):
        return {
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "total_tokens": usage["total_tokens"],
            "cache_read_input_tokens": usage["cache_read_input_tokens"],
            "cache_creation_input_tokens": usage["cache_creation_input_tokens"],
            "cost": {
                "currency": usage["cost"]["currency"],
                "total": usage["cost"]["total"],
            },
        }

    def _previous_window_for_range(self, range_name, now, since):
        if range_name == "all" or not since:
            return None

        current_start = parse_datetime(since)
        current = parse_datetime(now) if now else datetime.now().astimezone()
        if current.tzinfo is None and current_start.tzinfo is not None:
            current = current.replace(tzinfo=current_start.tzinfo)

        if range_name == "day":
            return (
                (current_start - timedelta(days=1)).isoformat(),
                current_start.isoformat(),
                "较昨日",
            )
        if range_name == "week":
            return (
                (current_start - timedelta(days=7)).isoformat(),
                current_start.isoformat(),
                "较前7天",
            )
        if range_name == "month":
            return (
                (current_start - timedelta(days=30)).isoformat(),
                current_start.isoformat(),
                "较前30天",
            )
        return None

    def _time_window_clause(self, column, since, until=None):
        clauses = []
        params = []
        if since:
            clauses.append(f"{column} >= ?")
            params.append(self._indexed_time_value(since))
        if until:
            clauses.append(f"{column} < ?")
            params.append(self._indexed_time_value(until))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, params

    def clear_proxy_stats(self):
        with self._connection() as conn:
            with conn:
                conn.execute("DELETE FROM proxy_requests")

    def prune_proxy_stats(self, keep_days=7, now=None):
        keep_days = max(1, int(keep_days))
        current = parse_datetime(now) if now else datetime.now().astimezone()
        cutoff = current - timedelta(days=keep_days)
        cutoff_iso = self._indexed_time_value(cutoff.isoformat())
        size_before = self._db_path.stat().st_size if self._db_path.exists() else 0

        with self._connection() as conn:
            before_count = self._count_proxy_requests(conn)
            with conn:
                cursor = conn.execute(
                    """
                    DELETE FROM proxy_requests
                    WHERE started_at < ?
                    """,
                    (cutoff_iso,),
                )
                deleted_count = cursor.rowcount if cursor.rowcount >= 0 else 0
            remaining_count = self._count_proxy_requests(conn)

        vacuum_ok = True
        vacuum_error = ""
        try:
            with self._connection() as conn:
                conn.isolation_level = None
                conn.execute("VACUUM")
        except sqlite3.Error as exc:
            vacuum_ok = False
            vacuum_error = str(exc)

        size_after = self._db_path.stat().st_size if self._db_path.exists() else 0
        return {
            "ok": vacuum_ok,
            "keep_days": keep_days,
            "cutoff": cutoff_iso,
            "before_count": before_count,
            "deleted_count": deleted_count,
            "remaining_count": remaining_count,
            "size_before_bytes": size_before,
            "size_after_bytes": size_after,
            "vacuum_ok": vacuum_ok,
            "vacuum_error": vacuum_error,
        }

    def get_traffic_ranking(self, since_iso=None):
        """
        获取从 since_iso 起的所有流量排行（按进程和 Host 聚类），高并发 SQLite 秒回。
        """
        sql_process = "SELECT client_process, COUNT(*) AS count FROM proxy_requests"
        sql_host = "SELECT host, COUNT(*) AS count FROM proxy_requests"
        sql_claude_host = (
            "SELECT host, COUNT(*) AS count FROM proxy_requests "
            "WHERE COALESCE(NULLIF(client_label, ''), 'Unknown') = 'Claude Code'"
        )

        params = []
        if since_iso:
            sql_process += " WHERE started_at >= ?"
            sql_host += " WHERE started_at >= ?"
            sql_claude_host += " AND started_at >= ?"
            params.append(self._indexed_time_value(since_iso))

        sql_process += " GROUP BY client_process ORDER BY count DESC"
        sql_host += " GROUP BY host ORDER BY count DESC"
        sql_claude_host += " GROUP BY host ORDER BY count DESC"

        with self._connection() as conn:
            process_rows = conn.execute(sql_process, params).fetchall()
            host_rows = conn.execute(sql_host, params).fetchall()
            claude_host_rows = conn.execute(sql_claude_host, params).fetchall()

        software_ranking = []
        for row in process_rows:
            proc = row["client_process"] if isinstance(row, sqlite3.Row) else row[0]
            cnt = row["count"] if isinstance(row, sqlite3.Row) else row[1]
            software_ranking.append({
                "process": proc or "unknown",
                "count": cnt
            })

        host_ranking = []
        for row in host_rows:
            h = row["host"] if isinstance(row, sqlite3.Row) else row[0]
            cnt = row["count"] if isinstance(row, sqlite3.Row) else row[1]
            host_ranking.append({
                "host": h or "unknown",
                "count": cnt
            })

        claude_code_host_ranking = []
        for row in claude_host_rows:
            h = row["host"] if isinstance(row, sqlite3.Row) else row[0]
            cnt = row["count"] if isinstance(row, sqlite3.Row) else row[1]
            claude_code_host_ranking.append({
                "host": h or "unknown",
                "count": cnt
            })

        return {
            "software": software_ranking,
            "host": host_ranking,
            "claude_code_host": claude_code_host_ranking,
            "claude_code_activity": self.get_claude_code_activity(since_iso),
        }

    def get_claude_code_activity(self, since_iso=None, limit=1000):
        limit = max(1, min(int(limit), 5000))
        where = "WHERE COALESCE(NULLIF(client_label, ''), 'Unknown') = 'Claude Code'"
        params = []
        if since_iso:
            where += " AND started_at >= ?"
            params.append(self._indexed_time_value(since_iso))
        params.append(limit)

        with self._connection() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    started_at,
                    host,
                    success,
                    connect_latency_ms,
                    duration_ms,
                    stage,
                    error,
                    client_pid,
                    client_process,
                    client_evidence,
                    client_chain
                FROM proxy_requests
                {where}
                ORDER BY started_at DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        activity = []
        for row in rows:
            provider = classify_provider(row["host"])
            activity.append(
                {
                    "started_at": row["started_at"],
                    "host": row["host"] or UNKNOWN_HOST,
                    "success": bool(row["success"]),
                    "connect_latency_ms": (
                        int(row["connect_latency_ms"])
                        if row["connect_latency_ms"] is not None
                        else None
                    ),
                    "duration_ms": int(row["duration_ms"] or 0),
                    "stage": row["stage"] or "completed",
                    "error": row["error"] or "",
                    "client_pid": (
                        int(row["client_pid"])
                        if row["client_pid"] is not None
                        else None
                    ),
                    "client_process": row["client_process"] or "",
                    "client_evidence": row["client_evidence"] or "",
                    "client_chain": row["client_chain"] or "",
                    **provider,
                }
            )
        return activity

    def _count_proxy_requests(self, conn):
        row = conn.execute("SELECT COUNT(*) AS count FROM proxy_requests").fetchone()
        return int(row["count"] if isinstance(row, sqlite3.Row) else row[0])

    def _connect(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def _init_schema(self):
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            with conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS proxy_requests (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        started_at TEXT NOT NULL,
                        completed_at TEXT NOT NULL,
                        method TEXT NOT NULL,
                        host TEXT NOT NULL,
                        route TEXT NOT NULL,
                        success INTEGER NOT NULL,
                        latency_ms INTEGER NOT NULL,
                        connect_latency_ms INTEGER,
                        duration_ms INTEGER,
                        stage TEXT NOT NULL DEFAULT 'completed',
                        client_addr TEXT,
                        client_port INTEGER,
                        target_port INTEGER,
                        upstream_host TEXT,
                        upstream_port INTEGER,
                        client_pid INTEGER,
                        client_process TEXT,
                        client_exe TEXT,
                        client_label TEXT,
                        client_evidence TEXT,
                        client_chain TEXT,
                        user_agent TEXT,
                        error TEXT
                    )
                    """
                )
                self._migrate_proxy_request_columns(conn)
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS usage_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source_file TEXT NOT NULL,
                        source_line INTEGER NOT NULL,
                        timestamp TEXT NOT NULL,
                        session_id TEXT,
                        model TEXT NOT NULL,
                        input_tokens INTEGER NOT NULL,
                        output_tokens INTEGER NOT NULL,
                        cache_read_input_tokens INTEGER NOT NULL,
                        cache_creation_input_tokens INTEGER NOT NULL,
                        web_search_requests INTEGER NOT NULL,
                        web_fetch_requests INTEGER NOT NULL,
                        service_tier TEXT,
                        speed TEXT,
                        UNIQUE(source_file, source_line)
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_proxy_requests_started_at
                    ON proxy_requests(started_at)
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_proxy_requests_host_started_at
                    ON proxy_requests(host, started_at)
                    """
                )

    def _migrate_proxy_request_columns(self, conn):
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(proxy_requests)")
        }
        if "connect_latency_ms" not in columns:
            conn.execute(
                "ALTER TABLE proxy_requests ADD COLUMN connect_latency_ms INTEGER"
            )
            conn.execute(
                """
                UPDATE proxy_requests
                SET connect_latency_ms = latency_ms
                WHERE method <> 'CONNECT'
                """
            )
        if "duration_ms" not in columns:
            conn.execute(
                "ALTER TABLE proxy_requests ADD COLUMN duration_ms INTEGER"
            )
            conn.execute(
                """
                UPDATE proxy_requests
                SET duration_ms = latency_ms
                WHERE duration_ms IS NULL
                """
            )
        if "stage" not in columns:
            conn.execute(
                "ALTER TABLE proxy_requests ADD COLUMN stage TEXT NOT NULL DEFAULT 'completed'"
            )
        if "client_addr" not in columns:
            conn.execute("ALTER TABLE proxy_requests ADD COLUMN client_addr TEXT")
        if "client_port" not in columns:
            conn.execute("ALTER TABLE proxy_requests ADD COLUMN client_port INTEGER")
        if "target_port" not in columns:
            conn.execute("ALTER TABLE proxy_requests ADD COLUMN target_port INTEGER")
        if "upstream_host" not in columns:
            conn.execute("ALTER TABLE proxy_requests ADD COLUMN upstream_host TEXT")
        if "upstream_port" not in columns:
            conn.execute("ALTER TABLE proxy_requests ADD COLUMN upstream_port INTEGER")
        if "client_pid" not in columns:
            conn.execute("ALTER TABLE proxy_requests ADD COLUMN client_pid INTEGER")
        if "client_process" not in columns:
            conn.execute("ALTER TABLE proxy_requests ADD COLUMN client_process TEXT")
        if "client_exe" not in columns:
            conn.execute("ALTER TABLE proxy_requests ADD COLUMN client_exe TEXT")
        if "client_label" not in columns:
            conn.execute("ALTER TABLE proxy_requests ADD COLUMN client_label TEXT")
        if "client_evidence" not in columns:
            conn.execute("ALTER TABLE proxy_requests ADD COLUMN client_evidence TEXT")
        if "client_chain" not in columns:
            conn.execute("ALTER TABLE proxy_requests ADD COLUMN client_chain TEXT")
        if "user_agent" not in columns:
            conn.execute("ALTER TABLE proxy_requests ADD COLUMN user_agent TEXT")

    def _connect_latency_expr(self):
        return (
            "CASE "
            "WHEN connect_latency_ms IS NOT NULL THEN connect_latency_ms "
            "WHEN method <> 'CONNECT' THEN latency_ms "
            "ELSE NULL "
            "END"
        )

    def _alertable_request_expr(self):
        return (
            f"host <> '{UNKNOWN_HOST}' "
            f"AND route NOT IN ('{UNPARSED_ROUTE}', 'blocked') "
            "AND COALESCE(stage, 'completed') NOT IN "
            "('client_closed', 'read_timeout', 'parse_failed')"
        )

    def _since_for_range(self, range_name, now):
        if range_name == "all":
            return None

        current = parse_datetime(now) if now else datetime.now().astimezone()
        if range_name == "day":
            start = datetime.combine(
                current.date(),
                datetime_time.min,
                tzinfo=current.tzinfo,
            )
        elif range_name == "week":
            start_date = current.date() - timedelta(days=6)
            start = datetime.combine(
                start_date,
                datetime_time.min,
                tzinfo=current.tzinfo,
            )
        elif range_name == "month":
            start_date = current.date() - timedelta(days=29)
            start = datetime.combine(
                start_date,
                datetime_time.min,
                tzinfo=current.tzinfo,
            )
        else:
            return None
        return start.isoformat()

    def _range_query(self, select_sql, time_column, since):
        if since:
            return f"{select_sql} WHERE {time_column} >= ?"
        return select_sql

    def _indexed_time_value(self, value):
        return parse_datetime(value).astimezone(timezone.utc).isoformat()

    def _usage_trends_query(self, since, models):
        sql = """
            SELECT
                timestamp,
                model,
                input_tokens,
                output_tokens,
                cache_read_input_tokens,
                cache_creation_input_tokens
            FROM usage_events
        """
        clauses = ["source_file LIKE ?"]
        params = ["%token-capture-%"]
        if since:
            clauses.append("datetime(timestamp) >= datetime(?)")
            params.append(since)
        if models:
            placeholders = ", ".join("?" for _ in models)
            clauses.append(f"model IN ({placeholders})")
            params.extend(models)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return sql, params

    def _usage_time_window_clause(self, since, until=None):
        clauses = ["source_file LIKE ?"]
        params = ["%token-capture-%"]
        if since:
            clauses.append("datetime(timestamp) >= datetime(?)")
            params.append(since)
        if until:
            clauses.append("datetime(timestamp) < datetime(?)")
            params.append(until)
        return f"WHERE {' AND '.join(clauses)}", params

    def _bucket_key(self, value, interval):
        dt = parse_datetime(value)
        if interval == "hour":
            dt = dt.replace(minute=0, second=0, microsecond=0)
        else:
            dt = datetime.combine(
                dt.date(),
                datetime_time.min,
                tzinfo=dt.tzinfo,
            )
        return dt.isoformat()

    def _trend_bucket(self, buckets, bucket):
        if bucket not in buckets:
            buckets[bucket] = {
                "bucket": bucket,
                "proxy_requests": 0,
                "failed_requests": 0,
                "average_latency_ms": 0,
                "average_connect_latency_ms": 0,
                "average_duration_ms": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "estimated_cost": 0.0,
                "_latency_sum": 0,
                "_latency_count": 0,
                "_duration_sum": 0,
                "_duration_count": 0,
            }
        return buckets[bucket]


def parse_datetime(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    raise TypeError("now must be a datetime or ISO timestamp string")
