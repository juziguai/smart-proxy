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
        self.assertEqual(payload["proxy"]["hosts"][0]["host"], "api.example.com")
        self.assertIn("alerts", payload["proxy"])
        self.assertIn("alert_counts", payload["proxy"])

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

    def test_trends_endpoint_returns_json_trends(self):
        with TemporaryDirectory() as temp_dir:
            store = StatsStore(f"{temp_dir}/stats.db")

            status, headers, body = handle_stats_request(
                "GET",
                urlparse("/api/trends?range=day"),
                store,
            )

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["range"], "day")
        self.assertEqual(payload["interval"], "hour")
        self.assertEqual(payload["models"], [])
        self.assertEqual(payload["points"], [])

    def test_trends_endpoint_accepts_model_filters(self):
        with TemporaryDirectory() as temp_dir:
            store = StatsStore(f"{temp_dir}/stats.db")

            status, _headers, body = handle_stats_request(
                "GET",
                urlparse("/api/trends?range=day&model=a&model=b"),
                store,
            )

        self.assertEqual(status, 200)
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["models"], ["a", "b"])

    def test_recent_requests_endpoint_returns_latest_proxy_events(self):
        with TemporaryDirectory() as temp_dir:
            store = StatsStore(f"{temp_dir}/stats.db")
            store.record_proxy_request(
                ProxyRequestEvent(
                    started_at="2026-05-10T01:00:00+00:00",
                    completed_at="2026-05-10T01:00:01+00:00",
                    method="CONNECT",
                    host="api.example.com",
                    route="proxy",
                    success=False,
                    latency_ms=100,
                    error="bad gateway",
                )
            )

            status, headers, body = handle_stats_request(
                "GET",
                urlparse("/api/recent-requests?limit=5"),
                store,
            )

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["requests"][0]["host"], "api.example.com")
        self.assertFalse(payload["requests"][0]["success"])
        self.assertFalse(payload["requests"][0]["slow"])

    def test_runtime_status_endpoint_uses_status_provider(self):
        with TemporaryDirectory() as temp_dir:
            store = StatsStore(f"{temp_dir}/stats.db")

            status, _headers, body = handle_stats_request(
                "GET",
                urlparse("/api/runtime-status"),
                store,
                status_provider=lambda: {
                    "proxy_enabled": True,
                    "upstream_proxy": "127.0.0.1:10808",
                    "whitelist_count": 3,
                },
            )

        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(status, 200)
        self.assertTrue(payload["proxy_enabled"])
        self.assertEqual(payload["upstream_proxy"], "127.0.0.1:10808")
        self.assertEqual(payload["whitelist_count"], 3)

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
        self.assertIn("/api/trends", html)
        self.assertIn("clear-proxy-stats", html)

    def test_dashboard_uses_console_tab_shell(self):
        with TemporaryDirectory() as temp_dir:
            store = StatsStore(f"{temp_dir}/stats.db")

            status, _headers, body = handle_stats_request(
                "GET",
                urlparse("/"),
                store,
            )

        html = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn("Smart Proxy Console", html)
        self.assertIn('id="proxyChip"', html)
        self.assertIn('id="dashboardChip"', html)
        self.assertIn('data-tab-target="overview"', html)
        self.assertIn('data-tab-target="providers"', html)
        self.assertIn('data-tab-target="requests"', html)
        self.assertIn('data-tab-target="usage"', html)
        self.assertIn('data-tab-target="whitelist"', html)
        self.assertIn('data-tab-target="doctor"', html)
        self.assertIn("switchTab", html)

    def test_dashboard_matches_reference_console_layout(self):
        with TemporaryDirectory() as temp_dir:
            store = StatsStore(f"{temp_dir}/stats.db")

            status, _headers, body = handle_stats_request(
                "GET",
                urlparse("/"),
                store,
            )

        html = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn('class="topbar"', html)
        self.assertIn('class="brand-mark"', html)
        self.assertIn('class="tab-nav"', html)
        self.assertIn('class="health-banner"', html)
        self.assertIn('id="systemHealthText"', html)
        self.assertIn('class="metric-icon"', html)
        self.assertIn("较昨日", html)
        self.assertIn('id="timeWindow"', html)
        self.assertIn('id="autoRefresh"', html)
        self.assertIn('id="themeToggle"', html)
        self.assertIn('class="overview-main"', html)
        self.assertIn('id="providerHealth"', html)
        self.assertIn('id="recentAnomaliesTable"', html)
        self.assertIn('id="recentRequestsTable"', html)

    def test_dashboard_handles_dense_real_data_layout(self):
        with TemporaryDirectory() as temp_dir:
            store = StatsStore(f"{temp_dir}/stats.db")

            status, _headers, body = handle_stats_request(
                "GET",
                urlparse("/"),
                store,
            )

        html = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn("fitMetricValue", html)
        self.assertIn("alertOverflowChip", html)
        self.assertIn("alertSummaryLabel", html)
        self.assertIn("alertDetailText", html)
        self.assertIn("providerLabelForHost", html)
        self.assertIn("severityLabel", html)
        self.assertIn("alertKindLabel", html)
        self.assertIn("modelDisplayName", html)
        self.assertIn("providerGroups", html)
        self.assertIn("provider-logo", html)
        self.assertIn("provider-subtitle", html)
        self.assertIn("successRateText", html)
        self.assertIn("normalizeTrendPoints", html)
        self.assertIn("metric-card .value", html)

    def test_dashboard_places_existing_widgets_in_domain_tabs(self):
        with TemporaryDirectory() as temp_dir:
            store = StatsStore(f"{temp_dir}/stats.db")

            status, _headers, body = handle_stats_request(
                "GET",
                urlparse("/"),
                store,
            )

        html = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertLess(
            html.index('data-tab-panel="overview"'),
            html.index('id="trendChart"'),
        )
        self.assertLess(
            html.index('data-tab-panel="providers"'),
            html.index('id="runtimeStatus"'),
        )
        self.assertLess(
            html.index('data-tab-panel="requests"'),
            html.index('id="recentRequests"'),
        )
        self.assertLess(
            html.index('data-tab-panel="usage"'),
            html.index('id="models"'),
        )

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
        self.assertIn("costLabel(usage.cost)", html)
        self.assertIn("costLabel", html)
        self.assertIn("套餐内", html)

    def test_dashboard_html_compacts_large_kpi_numbers(self):
        with TemporaryDirectory() as temp_dir:
            store = StatsStore(f"{temp_dir}/stats.db")

            status, _headers, body = handle_stats_request(
                "GET",
                urlparse("/"),
                store,
            )

        html = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn("compactNumber", html)
        self.assertIn("setMetric", html)
        self.assertIn("100000000", html)

    def test_dashboard_html_renders_trend_chart(self):
        with TemporaryDirectory() as temp_dir:
            store = StatsStore(f"{temp_dir}/stats.db")

            status, _headers, body = handle_stats_request(
                "GET",
                urlparse("/"),
                store,
            )

        html = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn("trendChart", html)
        self.assertIn("renderTrendChart", html)
        self.assertIn("selectedModels", html)
        self.assertIn("renderModelFilter", html)
        self.assertIn("/api/trends", html)
        self.assertIn("/api/recent-requests", html)
        self.assertIn("/api/runtime-status", html)
        self.assertIn("hostRows", html)
        self.assertIn("recentRows", html)
        self.assertIn("alertRows", html)
        self.assertIn("alertsPanel", html)
        self.assertIn("slow-request", html)

    def test_dashboard_places_proxy_diagnostics_above_trend_chart(self):
        with TemporaryDirectory() as temp_dir:
            store = StatsStore(f"{temp_dir}/stats.db")

            status, _headers, body = handle_stats_request(
                "GET",
                urlparse("/"),
                store,
            )

        html = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertLess(html.index('data-tab-panel="providers"'), html.index('id="runtimeStatus"'))
        self.assertLess(html.index('data-tab-panel="providers"'), html.index('id="hosts"'))
        self.assertLess(html.index('data-tab-panel="overview"'), html.index('id="trendChart"'))

    def test_dashboard_supports_editable_local_layout(self):
        with TemporaryDirectory() as temp_dir:
            store = StatsStore(f"{temp_dir}/stats.db")

            status, _headers, body = handle_stats_request(
                "GET",
                urlparse("/"),
                store,
            )

        html = body.decode("utf-8")
        self.assertEqual(status, 200)
        self.assertIn('id="layoutToggle"', html)
        self.assertIn('id="resetLayout"', html)
        self.assertIn('id="layoutRoot"', html)
        self.assertIn('data-widget="alerts"', html)
        self.assertIn('data-widget="kpis"', html)
        self.assertIn('data-widget="trend"', html)
        self.assertIn("smartProxyOverviewDashboardLayout", html)
        self.assertIn("layoutStorageKey", html)
        self.assertIn("localStorage.setItem(layoutStorageKey", html)
        self.assertIn("setLayoutEditing", html)
        self.assertIn("dragstart", html)
        self.assertIn("restoreDefaultLayout", html)

    def test_build_stats_response_encodes_json(self):
        status, headers, body = build_stats_response(201, {"ok": True})

        self.assertEqual(status, 201)
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(body, b'{"ok": true}')


if __name__ == "__main__":
    unittest.main()
