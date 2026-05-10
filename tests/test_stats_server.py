import json
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import urlparse

from stats_server import build_stats_response, handle_stats_request
from stats_store import ProxyRequestEvent, StatsStore


class StatsServerTests(unittest.TestCase):
    def test_summary_endpoint_returns_json_summary(self):
        with TemporaryDirectory() as temp_dir:
            store = StatsStore(f"{temp_dir}/stats.db")
            store.record_proxy_request(
                ProxyRequestEvent(
                    started_at="2026-05-10T01:00:00+00:00",
                    completed_at="2026-05-10T01:00:01+00:00",
                    method="CONNECT",
                    host="api.example.com",
                    route="proxy",
                    success=True,
                    latency_ms=100,
                    error=None,
                )
            )

            status, headers, body = handle_stats_request(
                "GET",
                urlparse("/api/summary?range=all"),
                store,
            )

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["proxy"]["total_requests"], 1)
        self.assertEqual(payload["proxy"]["routes"], {"proxy": 1})

    def test_clear_proxy_stats_endpoint_clears_proxy_events(self):
        with TemporaryDirectory() as temp_dir:
            store = StatsStore(f"{temp_dir}/stats.db")
            store.record_proxy_request(
                ProxyRequestEvent(
                    started_at="2026-05-10T01:00:00+00:00",
                    completed_at="2026-05-10T01:00:01+00:00",
                    method="CONNECT",
                    host="api.example.com",
                    route="proxy",
                    success=True,
                    latency_ms=100,
                    error=None,
                )
            )

            status, headers, body = handle_stats_request(
                "POST",
                urlparse("/api/clear-proxy-stats"),
                store,
            )
            summary = store.get_summary("all")

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(json.loads(body.decode("utf-8")), {"ok": True})
        self.assertEqual(summary["proxy"]["total_requests"], 0)

    def test_unknown_endpoint_returns_404_json(self):
        with TemporaryDirectory() as temp_dir:
            store = StatsStore(f"{temp_dir}/stats.db")

            status, headers, body = handle_stats_request(
                "GET",
                urlparse("/missing"),
                store,
            )

        self.assertEqual(status, 404)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(json.loads(body.decode("utf-8")), {"error": "not found"})

    def test_root_endpoint_returns_dashboard_html(self):
        with TemporaryDirectory() as temp_dir:
            store = StatsStore(f"{temp_dir}/stats.db")

            status, headers, body = handle_stats_request(
                "GET",
                urlparse("/"),
                store,
            )

        html = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/html; charset=utf-8")
        self.assertIn("Smart Proxy", html)
        self.assertIn("/api/summary", html)
        self.assertIn("clear-proxy-stats", html)

    def test_dashboard_html_renders_model_token_breakdown(self):
        with TemporaryDirectory() as temp_dir:
            store = StatsStore(f"{temp_dir}/stats.db")

            status, _headers, body = handle_stats_request(
                "GET",
                urlparse("/"),
                store,
            )

        html = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn("modelRows", html)
        self.assertIn("cache_read_input_tokens", html)
        self.assertIn("cache_creation_input_tokens", html)
        self.assertIn("输入", html)
        self.assertIn("输出", html)

    def test_build_stats_response_encodes_json(self):
        status, headers, body = build_stats_response(201, {"ok": True})

        self.assertEqual(status, 201)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(body, b'{"ok": true}')


if __name__ == "__main__":
    unittest.main()
