from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime, time as datetime_time, timedelta
from pathlib import Path
import sqlite3

from pricing import aggregate_cost, estimate_usage_cost


SLOW_REQUEST_THRESHOLD_MS = 3000
HOST_FAILURE_RATE_THRESHOLD = 0.10
HOST_CRITICAL_FAILURE_RATE = 0.50


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


class StatsStore:
    def __init__(self, db_path):
        self._db_path = Path(db_path)
        self._init_schema()

    def record_proxy_request(self, event: ProxyRequestEvent):
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
                        error
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.started_at,
                        event.completed_at,
                        event.method,
                        event.host,
                        event.route,
                        1 if event.success else 0,
                        event.latency_ms,
                        event.error,
                    ),
                )

    def get_summary(self, range_name, now=None, since=None):
        since = since or self._since_for_range(range_name, now)
        proxy = self._get_proxy_summary(since)
        usage = self._get_usage_summary(since)
        return {"proxy": proxy, "usage": usage}

    def get_recent_proxy_requests(self, limit=50, since=None):
        limit = max(1, min(int(limit), 200))
        where = ""
        params = []
        if since:
            where = "WHERE started_at >= ?"
            params.append(since)
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
                    error
                FROM proxy_requests
                {where}
                ORDER BY started_at DESC, id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()

        return [
            {
                "started_at": row["started_at"],
                "completed_at": row["completed_at"],
                "method": row["method"],
                "host": row["host"],
                "route": row["route"],
                "success": bool(row["success"]),
                "latency_ms": int(row["latency_ms"]),
                "slow": int(row["latency_ms"]) >= SLOW_REQUEST_THRESHOLD_MS,
                "error": row["error"],
            }
            for row in rows
        ]

    def get_trends(self, range_name, now=None, since=None, models=None):
        since = since or self._since_for_range(range_name, now)
        interval = "hour" if range_name == "day" else "day"
        buckets = {}
        models = [model for model in (models or []) if model]

        with self._connection() as conn:
            proxy_rows = conn.execute(
                self._range_query(
                    """
                    SELECT started_at, success, latency_ms
                    FROM proxy_requests
                    """,
                    "started_at",
                    since,
                ),
                [since] if since else [],
            ).fetchall()
            usage_sql, usage_params = self._usage_trends_query(since, models)
            usage_rows = conn.execute(usage_sql, usage_params).fetchall()

        for row in proxy_rows:
            bucket = self._bucket_key(row["started_at"], interval)
            item = self._trend_bucket(buckets, bucket)
            item["proxy_requests"] += 1
            item["failed_requests"] += 0 if row["success"] else 1
            item["_latency_sum"] += int(row["latency_ms"])

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
            if item["proxy_requests"]:
                item["average_latency_ms"] = int(
                    item["_latency_sum"] / item["proxy_requests"]
                )
            del item["_latency_sum"]
            result.append(item)

        return {
            "range": range_name,
            "interval": interval,
            "currency": "CNY",
            "models": models,
            "points": result,
        }

    def upsert_usage_event(self, event):
        with self._connection() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO usage_events (
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
                    """,
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
                    ),
                )

    def _get_proxy_summary(self, since):
        where = ""
        params = []
        if since:
            where = "WHERE started_at >= ?"
            params.append(since)

        with self._connection() as conn:
            row = conn.execute(
                f"""
                SELECT
                    COUNT(*) AS total_requests,
                    COALESCE(SUM(success), 0) AS successful_requests,
                    COALESCE(SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END), 0)
                        AS failed_requests,
                    COALESCE(AVG(latency_ms), 0) AS average_latency_ms
                FROM proxy_requests
                {where}
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
                    COALESCE(SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END), 0)
                        AS failed_requests,
                    COALESCE(SUM(CASE WHEN latency_ms >= ? THEN 1 ELSE 0 END), 0)
                        AS slow_requests,
                    COALESCE(AVG(latency_ms), 0) AS average_latency_ms
                FROM proxy_requests
                {where}
                GROUP BY host, route
                ORDER BY COUNT(*) DESC, AVG(latency_ms) DESC
                """,
                [SLOW_REQUEST_THRESHOLD_MS] + params,
            ).fetchall()

        total_requests = int(row["total_requests"])
        successful_requests = int(row["successful_requests"])
        failed_requests = int(row["failed_requests"])
        average_latency_ms = int(row["average_latency_ms"])
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
                    "slow_requests": 0,
                    "_latency_weighted_sum": 0,
                    "average_latency_ms": 0,
                    "failure_rate": 0,
                    "slow_rate": 0,
                    "health": "ok",
                    "routes": {},
                }
            item = hosts[host]
            route_count = int(host_row["total_requests"])
            item["total_requests"] += route_count
            item["successful_requests"] += int(host_row["successful_requests"])
            item["failed_requests"] += int(host_row["failed_requests"])
            item["slow_requests"] += int(host_row["slow_requests"])
            item["_latency_weighted_sum"] += (
                int(host_row["average_latency_ms"]) * route_count
            )
            item["routes"][host_row["route"]] = route_count

        host_breakdown = []
        for item in hosts.values():
            if item["total_requests"]:
                item["average_latency_ms"] = int(
                    item["_latency_weighted_sum"] / item["total_requests"]
                )
                item["failure_rate"] = (
                    item["failed_requests"] / item["total_requests"]
                )
                item["slow_rate"] = (
                    item["slow_requests"] / item["total_requests"]
                )
            if item["failure_rate"] >= HOST_CRITICAL_FAILURE_RATE:
                item["health"] = "critical"
            elif (
                item["failure_rate"] >= HOST_FAILURE_RATE_THRESHOLD
                or item["slow_requests"] > 0
            ):
                item["health"] = "warning"
            del item["_latency_weighted_sum"]
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

        return {
            "total_requests": total_requests,
            "successful_requests": successful_requests,
            "failed_requests": failed_requests,
            "success_rate": success_rate,
            "average_latency_ms": average_latency_ms,
            "routes": {
                route_row["route"]: int(route_row["count"])
                for route_row in route_rows
            },
            "hosts": host_breakdown[:20],
            "alerts": alerts,
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

    def _build_proxy_alerts(self, hosts):
        alerts = []
        for host in hosts[:10]:
            if (
                host["failed_requests"] > 0
                and host["failure_rate"] >= HOST_FAILURE_RATE_THRESHOLD
            ):
                severity = (
                    "critical"
                    if host["failure_rate"] >= HOST_CRITICAL_FAILURE_RATE
                    else "warning"
                )
                alerts.append(
                    {
                        "severity": severity,
                        "kind": "host_failures",
                        "host": host["host"],
                        "message": (
                            f"{host['host']} failure rate "
                            f"{round(host['failure_rate'] * 100)}%"
                        ),
                        "value": host["failure_rate"],
                    }
                )
            if host["slow_requests"] > 0:
                alerts.append(
                    {
                        "severity": "warning",
                        "kind": "slow_requests",
                        "host": host["host"],
                        "message": (
                            f"{host['host']} has {host['slow_requests']} "
                            f"request(s) >= {SLOW_REQUEST_THRESHOLD_MS}ms"
                        ),
                        "value": host["slow_requests"],
                    }
                )
        return alerts[:8]

    def _get_usage_summary(self, since):
        where = ""
        params = []
        if since:
            where = "WHERE timestamp >= ?"
            params.append(since)

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

    def clear_proxy_stats(self):
        with self._connection() as conn:
            with conn:
                conn.execute("DELETE FROM proxy_requests")

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
                        error TEXT
                    )
                    """
                )
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
        clauses = []
        params = []
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        if models:
            placeholders = ", ".join("?" for _ in models)
            clauses.append(f"model IN ({placeholders})")
            params.extend(models)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return sql, params

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
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
                "estimated_cost": 0.0,
                "_latency_sum": 0,
            }
        return buckets[bucket]


def parse_datetime(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    raise TypeError("now must be a datetime or ISO timestamp string")
