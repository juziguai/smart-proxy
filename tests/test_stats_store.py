from datetime import datetime, timezone
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
