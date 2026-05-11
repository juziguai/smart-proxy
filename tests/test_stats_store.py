from datetime import datetime, timedelta, timezone
import sqlite3
import unittest

from claude_usage_reader import UsageEvent
from stats_store import ProxyRequestEvent, StatsStore


def iso_at(hour):
    return datetime(2026, 5, 10, hour, 0, 0, tzinfo=timezone.utc).isoformat()


class StatsStoreTests(unittest.TestCase):
    def test_records_and_aggregates_proxy_requests(self):
        with self.subTest("aggregate"):
            tmp_path = self.enterContext(TemporaryDirectoryPath())
            store = StatsStore(tmp_path / "stats.db")

            store.record_proxy_request(
                ProxyRequestEvent(
                    started_at=iso_at(1),
                    completed_at=iso_at(1),
                    method="CONNECT",
                    host="api.example.com",
                    route="proxy",
                    success=True,
                    latency_ms=120,
                    error=None,
                )
            )
            store.record_proxy_request(
                ProxyRequestEvent(
                    started_at=iso_at(2),
                    completed_at=iso_at(2),
                    method="CONNECT",
                    host="www.baidu.com",
                    route="direct_whitelist",
                    success=True,
                    latency_ms=40,
                    error=None,
                )
            )
            store.record_proxy_request(
                ProxyRequestEvent(
                    started_at=iso_at(3),
                    completed_at=iso_at(3),
                    method="GET",
                    host="broken.example.com",
                    route="direct",
                    success=False,
                    latency_ms=20,
                    error="connect failed",
                )
            )

            summary = store.get_summary("all")

            self.assertEqual(summary["proxy"]["total_requests"], 3)
            self.assertEqual(summary["proxy"]["successful_requests"], 2)
            self.assertEqual(summary["proxy"]["failed_requests"], 1)
            self.assertEqual(summary["proxy"]["success_rate"], 2 / 3)
            self.assertEqual(summary["proxy"]["average_latency_ms"], 60)
            self.assertEqual(
                summary["proxy"]["routes"],
                {
                    "proxy": 1,
                    "direct_whitelist": 1,
                    "direct": 1,
                },
            )

    def test_range_filter_only_counts_requests_after_start_time(self):
        tmp_path = self.enterContext(TemporaryDirectoryPath())
        store = StatsStore(tmp_path / "stats.db")

        store.record_proxy_request(
            ProxyRequestEvent(
                started_at=iso_at(1),
                completed_at=iso_at(1),
                method="CONNECT",
                host="old.example.com",
                route="proxy",
                success=True,
                latency_ms=100,
                error=None,
            )
        )
        store.record_proxy_request(
            ProxyRequestEvent(
                started_at=iso_at(5),
                completed_at=iso_at(5),
                method="CONNECT",
                host="new.example.com",
                route="proxy",
                success=True,
                latency_ms=50,
                error=None,
            )
        )

        summary = store.get_summary("all", now=iso_at(5), since=iso_at(4))

        self.assertEqual(summary["proxy"]["total_requests"], 1)
        self.assertEqual(summary["proxy"]["successful_requests"], 1)
        self.assertEqual(summary["proxy"]["failed_requests"], 0)
        self.assertEqual(summary["proxy"]["average_latency_ms"], 50)
        self.assertEqual(summary["proxy"]["routes"], {"proxy": 1})

    def test_local_day_range_includes_utc_events_after_local_midnight(self):
        tmp_path = self.enterContext(TemporaryDirectoryPath())
        store = StatsStore(tmp_path / "stats.db")
        china_tz = timezone(timedelta(hours=8))

        store.record_proxy_request(
            ProxyRequestEvent(
                started_at="2026-05-10T17:30:00+00:00",
                completed_at="2026-05-10T17:31:00+00:00",
                method="CONNECT",
                host="api.deepseek.com",
                route="proxy",
                success=True,
                latency_ms=120,
                error=None,
            )
        )
        store.upsert_usage_event(
            UsageEvent(
                source_file="session-local-day.jsonl",
                source_line=1,
                timestamp="2026-05-10T17:32:00+00:00",
                session_id="session-local-day",
                model="deepseek-v4-flash",
                input_tokens=100,
                output_tokens=20,
                cache_read_input_tokens=10,
                cache_creation_input_tokens=0,
                web_search_requests=0,
                web_fetch_requests=0,
                service_tier="standard",
                speed="fast",
            )
        )

        summary = store.get_summary(
            "day",
            now=datetime(2026, 5, 11, 1, 40, tzinfo=china_tz),
        )
        trends = store.get_trends(
            "day",
            now=datetime(2026, 5, 11, 1, 40, tzinfo=china_tz),
        )

        self.assertEqual(summary["proxy"]["total_requests"], 1)
        self.assertEqual(summary["usage"]["total_tokens"], 120)
        self.assertEqual(len(trends["points"]), 1)
        self.assertEqual(trends["points"][0]["proxy_requests"], 1)
        self.assertEqual(trends["points"][0]["total_tokens"], 120)

    def test_proxy_summary_includes_host_breakdown(self):
        tmp_path = self.enterContext(TemporaryDirectoryPath())
        store = StatsStore(tmp_path / "stats.db")

        for index, (host, success, latency) in enumerate(
            (
                ("api.deepseek.com", True, 100),
                ("api.deepseek.com", False, 300),
                ("api.minimaxi.com", True, 50),
            ),
            start=1,
        ):
            store.record_proxy_request(
                ProxyRequestEvent(
                    started_at=iso_at(index),
                    completed_at=iso_at(index),
                    method="CONNECT",
                    host=host,
                    route="proxy",
                    success=success,
                    latency_ms=latency,
                    error=None if success else "bad gateway",
                )
            )

        hosts = store.get_summary("all")["proxy"]["hosts"]

        self.assertEqual(hosts[0]["host"], "api.deepseek.com")
        self.assertEqual(hosts[0]["total_requests"], 2)
        self.assertEqual(hosts[0]["failed_requests"], 1)
        self.assertEqual(hosts[0]["average_latency_ms"], 200)
        self.assertEqual(hosts[0]["routes"], {"proxy": 2})

    def test_proxy_summary_marks_slow_and_failed_hosts(self):
        tmp_path = self.enterContext(TemporaryDirectoryPath())
        store = StatsStore(tmp_path / "stats.db")

        for index, (success, latency) in enumerate(
            (
                (False, 4200),
                (True, 1200),
                (True, 900),
                (True, 800),
                (True, 700),
            ),
            start=1,
        ):
            store.record_proxy_request(
                ProxyRequestEvent(
                    started_at=iso_at(index),
                    completed_at=iso_at(index),
                    method="CONNECT",
                    host="api.slow.example.com",
                    route="proxy",
                    success=success,
                    latency_ms=latency,
                    error=None if success else "timeout",
                )
            )

        proxy = store.get_summary("all")["proxy"]
        host = proxy["hosts"][0]

        self.assertEqual(host["host"], "api.slow.example.com")
        self.assertEqual(host["slow_requests"], 1)
        self.assertEqual(host["failure_rate"], 0.2)
        self.assertEqual(host["health"], "warning")
        self.assertEqual(proxy["alert_counts"], {"critical": 0, "warning": 1})
        self.assertEqual(proxy["alerts"][0]["kind"], "host_failures")

    def test_long_connect_tunnel_duration_does_not_mark_slow_request(self):
        tmp_path = self.enterContext(TemporaryDirectoryPath())
        store = StatsStore(tmp_path / "stats.db")

        store.record_proxy_request(
            ProxyRequestEvent(
                started_at=iso_at(1),
                completed_at=iso_at(1),
                method="CONNECT",
                host="api.deepseek.com",
                route="proxy",
                success=True,
                latency_ms=600_000,
                error=None,
                connect_latency_ms=200,
                duration_ms=600_000,
            )
        )

        summary = store.get_summary("all")
        host = summary["proxy"]["hosts"][0]
        recent = store.get_recent_proxy_requests(limit=1)[0]

        self.assertEqual(summary["proxy"]["average_latency_ms"], 200)
        self.assertEqual(summary["proxy"]["average_connect_latency_ms"], 200)
        self.assertEqual(summary["proxy"]["average_duration_ms"], 600_000)
        self.assertEqual(host["slow_requests"], 0)
        self.assertEqual(host["average_connect_latency_ms"], 200)
        self.assertEqual(host["average_duration_ms"], 600_000)
        self.assertEqual(summary["proxy"]["alerts"], [])
        self.assertFalse(recent["slow"])
        self.assertEqual(recent["connect_latency_ms"], 200)
        self.assertEqual(recent["duration_ms"], 600_000)

    def test_connect_latency_marks_slow_request(self):
        tmp_path = self.enterContext(TemporaryDirectoryPath())
        store = StatsStore(tmp_path / "stats.db")

        for index in range(5):
            store.record_proxy_request(
                ProxyRequestEvent(
                    started_at=iso_at(index + 1),
                    completed_at=iso_at(index + 1),
                    method="CONNECT",
                    host="api.slow-connect.example.com",
                    route="proxy",
                    success=True,
                    latency_ms=30_000,
                    error=None,
                    connect_latency_ms=3_000,
                    duration_ms=30_000,
                )
            )

        summary = store.get_summary("all")
        host = summary["proxy"]["hosts"][0]
        recent = store.get_recent_proxy_requests(limit=1)[0]

        self.assertEqual(host["slow_requests"], 5)
        self.assertEqual(summary["proxy"]["slow_requests"], 5)
        self.assertEqual(summary["proxy"]["alerts"][0]["kind"], "slow_requests")
        self.assertTrue(recent["slow"])

    def test_slow_alerts_prioritize_model_api_and_suppress_noise(self):
        tmp_path = self.enterContext(TemporaryDirectoryPath())
        store = StatsStore(tmp_path / "stats.db")

        events = [
            ("api.github.com", 9),
            ("www.douyin.com", 5),
            ("api.deepseek.com", 2),
            ("api.anthropic.com", 1),
        ]
        minute = 1
        for host, count in events:
            for _ in range(count):
                store.record_proxy_request(
                    ProxyRequestEvent(
                        started_at=iso_at(minute),
                        completed_at=iso_at(minute),
                        method="CONNECT",
                        host=host,
                        route="proxy",
                        success=True,
                        latency_ms=4_000,
                        error=None,
                        connect_latency_ms=4_000,
                        duration_ms=4_000,
                    )
                )
                minute += 1

        alerts = store.get_summary("all")["proxy"]["alerts"]

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["kind"], "slow_requests")
        self.assertEqual(alerts[0]["host"], "api.deepseek.com")
        self.assertEqual(alerts[0]["value"], 2)

    def test_migrated_legacy_connect_duration_does_not_mark_slow_request(self):
        tmp_path = self.enterContext(TemporaryDirectoryPath())
        db_path = tmp_path / "stats.db"
        conn = sqlite3.connect(db_path)
        try:
            with conn:
                conn.execute(
                    """
                    CREATE TABLE proxy_requests (
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
                        iso_at(1),
                        iso_at(1),
                        "CONNECT",
                        "api.deepseek.com",
                        "proxy",
                        1,
                        600_000,
                        None,
                    ),
                )
        finally:
            conn.close()

        store = StatsStore(db_path)
        summary = store.get_summary("all")
        host = summary["proxy"]["hosts"][0]
        recent = store.get_recent_proxy_requests(limit=1)[0]

        self.assertEqual(summary["proxy"]["average_connect_latency_ms"], 0)
        self.assertEqual(summary["proxy"]["average_duration_ms"], 600_000)
        self.assertEqual(host["slow_requests"], 0)
        self.assertEqual(summary["proxy"]["alerts"], [])
        self.assertIsNone(recent["connect_latency_ms"])
        self.assertFalse(recent["slow"])

    def test_failure_alerts_ignore_legacy_failures_without_error_detail(self):
        tmp_path = self.enterContext(TemporaryDirectoryPath())
        store = StatsStore(tmp_path / "stats.db")

        for index in range(5):
            store.record_proxy_request(
                ProxyRequestEvent(
                    started_at=iso_at(index + 1),
                    completed_at=iso_at(index + 1),
                    method="CONNECT",
                    host="api.deepseek.com",
                    route="proxy",
                    success=False,
                    latency_ms=600_000,
                    error=None,
                    connect_latency_ms=100,
                    duration_ms=600_000,
                )
            )

        summary = store.get_summary("all")
        host = summary["proxy"]["hosts"][0]

        self.assertEqual(host["failed_requests"], 5)
        self.assertEqual(host["health"], "ok")
        self.assertEqual(summary["proxy"]["alerts"], [])

    def test_recent_proxy_requests_returns_latest_events(self):
        tmp_path = self.enterContext(TemporaryDirectoryPath())
        store = StatsStore(tmp_path / "stats.db")

        for index in range(3):
            store.record_proxy_request(
                ProxyRequestEvent(
                    started_at=iso_at(index + 1),
                    completed_at=iso_at(index + 1),
                    method="CONNECT",
                    host=f"api-{index}.example.com",
                    route="proxy",
                    success=index != 2,
                    latency_ms=100 + index,
                    error="timeout" if index == 2 else None,
                )
            )

        recent = store.get_recent_proxy_requests(limit=2)

        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[0]["host"], "api-2.example.com")
        self.assertFalse(recent[0]["success"])
        self.assertEqual(recent[0]["error"], "timeout")
        self.assertFalse(recent[0]["slow"])
        self.assertEqual(recent[1]["host"], "api-1.example.com")

    def test_whitelist_candidates_rank_proxy_hosts(self):
        tmp_path = self.enterContext(TemporaryDirectoryPath())
        store = StatsStore(tmp_path / "stats.db")

        events = [
            ("api.deepseek.com", "proxy", 4_000),
            ("api.deepseek.com", "proxy", 120),
            ("www.minimaxi.com", "direct_whitelist", 90),
            ("api.github.com", "proxy", 800),
        ]
        for index, (host, route, latency) in enumerate(events, start=1):
            store.record_proxy_request(
                ProxyRequestEvent(
                    started_at=iso_at(index),
                    completed_at=iso_at(index),
                    method="CONNECT",
                    host=host,
                    route=route,
                    success=True,
                    latency_ms=latency,
                    error=None,
                    connect_latency_ms=latency,
                    duration_ms=latency,
                )
            )

        candidates = store.get_whitelist_candidates(limit=5)

        self.assertEqual(candidates[0]["host"], "api.deepseek.com")
        self.assertEqual(candidates[0]["proxy_requests"], 2)
        self.assertEqual(candidates[0]["slow_requests"], 1)
        self.assertNotIn(
            "www.minimaxi.com",
            [candidate["host"] for candidate in candidates],
        )

    def test_recent_proxy_requests_marks_slow_events(self):
        tmp_path = self.enterContext(TemporaryDirectoryPath())
        store = StatsStore(tmp_path / "stats.db")
        store.record_proxy_request(
            ProxyRequestEvent(
                started_at=iso_at(1),
                completed_at=iso_at(1),
                method="CONNECT",
                host="api.slow.example.com",
                route="proxy",
                success=True,
                latency_ms=3000,
                error=None,
            )
        )

        recent = store.get_recent_proxy_requests(limit=1)

        self.assertTrue(recent[0]["slow"])

    def test_clear_proxy_stats_removes_proxy_events_only(self):
        tmp_path = self.enterContext(TemporaryDirectoryPath())
        store = StatsStore(tmp_path / "stats.db")

        store.record_proxy_request(
            ProxyRequestEvent(
                started_at=iso_at(1),
                completed_at=iso_at(1),
                method="CONNECT",
                host="api.example.com",
                route="proxy",
                success=True,
                latency_ms=120,
                error=None,
            )
        )

        self.assertEqual(store.get_summary("all")["proxy"]["total_requests"], 1)

        store.clear_proxy_stats()

        summary = store.get_summary("all")
        self.assertEqual(summary["proxy"]["total_requests"], 0)
        self.assertEqual(summary["proxy"]["successful_requests"], 0)
        self.assertEqual(summary["proxy"]["failed_requests"], 0)
        self.assertEqual(summary["proxy"]["success_rate"], 0)
        self.assertEqual(summary["proxy"]["average_latency_ms"], 0)
        self.assertEqual(summary["proxy"]["routes"], {})

    def test_aggregates_usage_events_by_range_and_model(self):
        tmp_path = self.enterContext(TemporaryDirectoryPath())
        store = StatsStore(tmp_path / "stats.db")

        store.upsert_usage_event(
            UsageEvent(
                source_file="session-a.jsonl",
                source_line=1,
                timestamp="2026-05-10T01:00:00+00:00",
                session_id="session-a",
                model="model-a",
                input_tokens=100,
                output_tokens=20,
                cache_read_input_tokens=50,
                cache_creation_input_tokens=10,
                web_search_requests=1,
                web_fetch_requests=2,
                service_tier="standard",
                speed="fast",
            )
        )
        store.upsert_usage_event(
            UsageEvent(
                source_file="session-b.jsonl",
                source_line=1,
                timestamp="2026-05-09T01:00:00+00:00",
                session_id="session-b",
                model="model-b",
                input_tokens=40,
                output_tokens=8,
                cache_read_input_tokens=4,
                cache_creation_input_tokens=2,
                web_search_requests=0,
                web_fetch_requests=1,
                service_tier="standard",
                speed="standard",
            )
        )

        summary = store.get_summary(
            "day",
            now="2026-05-10T12:00:00+00:00",
        )

        self.assertEqual(summary["usage"]["input_tokens"], 100)
        self.assertEqual(summary["usage"]["output_tokens"], 20)
        self.assertEqual(summary["usage"]["total_tokens"], 120)
        self.assertEqual(summary["usage"]["cache_read_input_tokens"], 50)
        self.assertEqual(summary["usage"]["cache_creation_input_tokens"], 10)
        self.assertEqual(summary["usage"]["web_search_requests"], 1)
        self.assertEqual(summary["usage"]["web_fetch_requests"], 2)
        self.assertEqual(
            summary["usage"]["models"],
            {
                "model-a": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                    "cache_read_input_tokens": 50,
                    "cache_creation_input_tokens": 10,
                    "web_search_requests": 1,
                    "web_fetch_requests": 2,
                    "cost": {
                        "billable": False,
                        "billing_type": "unknown",
                        "currency": "CNY",
                        "total": 0.0,
                        "input": 0.0,
                        "output": 0.0,
                        "cache_read": 0.0,
                        "cache_write": 0.0,
                        "source": "",
                    },
                }
            },
        )

        all_time = store.get_summary("all")
        self.assertEqual(all_time["usage"]["input_tokens"], 140)
        self.assertEqual(all_time["usage"]["output_tokens"], 28)
        self.assertEqual(all_time["usage"]["total_tokens"], 168)

    def test_upsert_usage_event_is_idempotent(self):
        tmp_path = self.enterContext(TemporaryDirectoryPath())
        store = StatsStore(tmp_path / "stats.db")
        event = UsageEvent(
            source_file="session-a.jsonl",
            source_line=1,
            timestamp="2026-05-10T01:00:00+00:00",
            session_id="session-a",
            model="model-a",
            input_tokens=100,
            output_tokens=20,
            cache_read_input_tokens=50,
            cache_creation_input_tokens=10,
            web_search_requests=1,
            web_fetch_requests=2,
            service_tier="standard",
            speed="fast",
        )

        store.upsert_usage_event(event)
        store.upsert_usage_event(event)

        summary = store.get_summary("all")
        self.assertEqual(summary["usage"]["input_tokens"], 100)
        self.assertEqual(summary["usage"]["output_tokens"], 20)

    def test_summary_includes_previous_period_comparison(self):
        tmp_path = self.enterContext(TemporaryDirectoryPath())
        store = StatsStore(tmp_path / "stats.db")

        yesterday = datetime(
            2026, 5, 9, 8, 0, 0, tzinfo=timezone.utc
        ).isoformat()
        today_a = datetime(
            2026, 5, 10, 8, 0, 0, tzinfo=timezone.utc
        ).isoformat()
        today_b = datetime(
            2026, 5, 10, 9, 0, 0, tzinfo=timezone.utc
        ).isoformat()
        for timestamp in (yesterday, today_a, today_b):
            store.record_proxy_request(
                ProxyRequestEvent(
                    started_at=timestamp,
                    completed_at=timestamp,
                    method="CONNECT",
                    host="api.deepseek.com",
                    route="proxy",
                    success=True,
                    latency_ms=100,
                    error=None,
                    connect_latency_ms=100,
                    duration_ms=100,
                )
            )
        store.upsert_usage_event(
            UsageEvent(
                source_file="yesterday.jsonl",
                source_line=1,
                timestamp=yesterday,
                session_id="session-y",
                model="deepseek-v4-flash",
                input_tokens=50,
                output_tokens=25,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
                web_search_requests=0,
                web_fetch_requests=0,
                service_tier="standard",
                speed="fast",
            )
        )
        store.upsert_usage_event(
            UsageEvent(
                source_file="today.jsonl",
                source_line=1,
                timestamp=today_a,
                session_id="session-t",
                model="deepseek-v4-flash",
                input_tokens=100,
                output_tokens=50,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
                web_search_requests=0,
                web_fetch_requests=0,
                service_tier="standard",
                speed="fast",
            )
        )

        summary = store.get_summary(
            "day",
            now="2026-05-10T12:00:00+00:00",
        )
        previous = summary["comparison"]["previous"]

        self.assertTrue(summary["comparison"]["available"])
        self.assertEqual(summary["comparison"]["label"], "较昨日")
        self.assertEqual(summary["proxy"]["total_requests"], 2)
        self.assertEqual(previous["proxy"]["total_requests"], 1)
        self.assertNotIn("hosts", previous["proxy"])
        self.assertEqual(summary["usage"]["total_tokens"], 150)
        self.assertEqual(previous["usage"]["total_tokens"], 75)
        self.assertNotIn("models", previous["usage"])

        all_time = store.get_summary("all")
        self.assertFalse(all_time["comparison"]["available"])

    def test_summary_estimates_api_cost_and_marks_token_plan_models(self):
        tmp_path = self.enterContext(TemporaryDirectoryPath())
        store = StatsStore(tmp_path / "stats.db")

        store.upsert_usage_event(
            UsageEvent(
                source_file="deepseek.jsonl",
                source_line=1,
                timestamp="2026-05-10T01:00:00+00:00",
                session_id="session-a",
                model="deepseek-v4-flash",
                input_tokens=1_000_000,
                output_tokens=2_000_000,
                cache_read_input_tokens=3_000_000,
                cache_creation_input_tokens=4_000_000,
                web_search_requests=0,
                web_fetch_requests=0,
                service_tier="standard",
                speed="fast",
            )
        )
        store.upsert_usage_event(
            UsageEvent(
                source_file="minimax.jsonl",
                source_line=1,
                timestamp="2026-05-10T01:00:00+00:00",
                session_id="session-b",
                model="MiniMax-M2.7-highspeed",
                input_tokens=1_000_000,
                output_tokens=1_000_000,
                cache_read_input_tokens=1_000_000,
                cache_creation_input_tokens=1_000_000,
                web_search_requests=0,
                web_fetch_requests=0,
                service_tier="standard",
                speed="fast",
            )
        )

        summary = store.get_summary("all")

        self.assertAlmostEqual(summary["usage"]["cost"]["total"], 9.06)
        self.assertEqual(summary["usage"]["cost"]["billable_models"], 1)
        self.assertEqual(summary["usage"]["cost"]["token_plan_models"], 1)
        self.assertEqual(
            summary["usage"]["models"]["MiniMax-M2.7-highspeed"]["cost"][
                "billing_type"
            ],
            "token_plan",
        )

    def test_trends_groups_usage_cost_and_proxy_metrics(self):
        tmp_path = self.enterContext(TemporaryDirectoryPath())
        store = StatsStore(tmp_path / "stats.db")

        store.record_proxy_request(
            ProxyRequestEvent(
                started_at="2026-05-10T01:10:00+00:00",
                completed_at="2026-05-10T01:10:01+00:00",
                method="CONNECT",
                host="api.deepseek.com",
                route="proxy",
                success=True,
                latency_ms=100,
                error=None,
            )
        )
        store.record_proxy_request(
            ProxyRequestEvent(
                started_at="2026-05-10T01:20:00+00:00",
                completed_at="2026-05-10T01:20:01+00:00",
                method="CONNECT",
                host="api.deepseek.com",
                route="proxy",
                success=False,
                latency_ms=300,
                error="failed",
            )
        )
        store.upsert_usage_event(
            UsageEvent(
                source_file="deepseek.jsonl",
                source_line=1,
                timestamp="2026-05-10T01:30:00+00:00",
                session_id="session-a",
                model="deepseek-v4-flash",
                input_tokens=1_000_000,
                output_tokens=0,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
                web_search_requests=0,
                web_fetch_requests=0,
                service_tier="standard",
                speed="fast",
            )
        )

        trends = store.get_trends(
            "day",
            now="2026-05-10T12:00:00+00:00",
        )

        self.assertEqual(trends["interval"], "hour")
        self.assertEqual(len(trends["points"]), 1)
        point = trends["points"][0]
        self.assertEqual(point["bucket"], "2026-05-10T01:00:00+00:00")
        self.assertEqual(point["proxy_requests"], 2)
        self.assertEqual(point["failed_requests"], 1)
        self.assertEqual(point["average_latency_ms"], 200)
        self.assertEqual(point["total_tokens"], 1_000_000)
        self.assertAlmostEqual(point["estimated_cost"], 1.0)

    def test_trends_can_filter_multiple_models(self):
        tmp_path = self.enterContext(TemporaryDirectoryPath())
        store = StatsStore(tmp_path / "stats.db")

        for model, tokens in (
            ("deepseek-v4-flash", 1_000_000),
            ("deepseek-v4-pro", 2_000_000),
            ("MiniMax-M2.7-highspeed", 3_000_000),
        ):
            store.upsert_usage_event(
                UsageEvent(
                    source_file=f"{model}.jsonl",
                    source_line=1,
                    timestamp="2026-05-10T01:30:00+00:00",
                    session_id=model,
                    model=model,
                    input_tokens=tokens,
                    output_tokens=0,
                    cache_read_input_tokens=0,
                    cache_creation_input_tokens=0,
                    web_search_requests=0,
                    web_fetch_requests=0,
                    service_tier="standard",
                    speed="fast",
                )
            )

        trends = store.get_trends(
            "day",
            now="2026-05-10T12:00:00+00:00",
            models=["deepseek-v4-flash", "deepseek-v4-pro"],
        )

        self.assertEqual(trends["models"], ["deepseek-v4-flash", "deepseek-v4-pro"])
        self.assertEqual(len(trends["points"]), 1)
        self.assertEqual(trends["points"][0]["total_tokens"], 3_000_000)
        self.assertAlmostEqual(trends["points"][0]["estimated_cost"], 7.0)


class TemporaryDirectoryPath:
    def __enter__(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        self._tmp = TemporaryDirectory()
        return Path(self._tmp.__enter__())

    def __exit__(self, exc_type, exc, tb):
        return self._tmp.__exit__(exc_type, exc, tb)


if __name__ == "__main__":
    unittest.main()
