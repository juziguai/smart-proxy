from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime, time as datetime_time, timedelta
from pathlib import Path
import sqlite3


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

        total_requests = int(row["total_requests"])
        successful_requests = int(row["successful_requests"])
        failed_requests = int(row["failed_requests"])
        average_latency_ms = int(row["average_latency_ms"])
        success_rate = (
            successful_requests / total_requests if total_requests else 0
        )

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
        }

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
            models[model_row["model"]] = {
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


def parse_datetime(value):
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    raise TypeError("now must be a datetime or ISO timestamp string")
