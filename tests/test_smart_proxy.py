import asyncio
import importlib.util
from pathlib import Path
import subprocess
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "smart-proxy.py"
SPEC = importlib.util.spec_from_file_location("smart_proxy", MODULE_PATH)
smart_proxy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smart_proxy)


class FakeReader:
    def __init__(self, lines=None, chunks=None):
        self.lines = list(lines or [])
        self.chunks = list(chunks or [])

    async def readline(self):
        if self.lines:
            return self.lines.pop(0)
        return b""

    async def read(self, size):
        if self.chunks:
            return self.chunks.pop(0)
        return b""


class FakeWriter:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, data):
        self.data.extend(data)

    async def drain(self):
        return None

    def close(self):
        self.closed = True


class FakeProxyCache:
    def __init__(self, upstream):
        self.upstream = upstream

    def get(self):
        return self.upstream


class FakeWhitelist:
    def __init__(self, matched):
        self.matched = matched

    def match(self, host):
        return self.matched


class FakeStatsStore:
    def __init__(self):
        self.events = []

    def record_proxy_request(self, event):
        self.events.append(event)


class HttpDirectTests(unittest.IsolatedAsyncioTestCase):
    async def test_forwards_request_body_when_content_length_is_present(self):
        client_reader = FakeReader(
            lines=[
                b"Host: example.com\r\n",
                b"Content-Length: 5\r\n",
                b"\r\n",
            ],
            chunks=[b"hello"],
        )
        client_writer = FakeWriter()
        remote_reader = FakeReader()
        remote_writer = FakeWriter()

        original_connect_to = smart_proxy.connect_to
        try:
            async def fake_connect_to(host, port, timeout=5):
                self.assertEqual(host, "example.com")
                self.assertEqual(port, 80)
                return remote_reader, remote_writer

            smart_proxy.connect_to = fake_connect_to

            await smart_proxy.http_direct(
                client_reader,
                client_writer,
                b"POST /submit HTTP/1.1\r\n",
            )
        finally:
            smart_proxy.connect_to = original_connect_to

        self.assertTrue(remote_writer.data.endswith(b"\r\n\r\nhello"))
        self.assertIn(b"Content-Length: 5\r\n", remote_writer.data)


class ProxyTelemetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_records_successful_proxy_request(self):
        stats_store = FakeStatsStore()
        client_reader = FakeReader(
            lines=[
                b"CONNECT api.example.com:443 HTTP/1.1\r\n",
                b"\r\n",
            ]
        )
        client_writer = FakeWriter()

        original_cache = smart_proxy.proxy_cache
        original_whitelist = smart_proxy.whitelist
        original_stats_store = smart_proxy.stats_store
        original_connect_via_proxy = smart_proxy.connect_via_proxy
        try:
            smart_proxy.proxy_cache = FakeProxyCache(("127.0.0.1", 10090))
            smart_proxy.whitelist = FakeWhitelist(False)
            smart_proxy.stats_store = stats_store

            async def fake_connect_via_proxy(client_r, client_w, target, upstream):
                self.assertEqual(target, "api.example.com:443")
                self.assertEqual(upstream, ("127.0.0.1", 10090))

            smart_proxy.connect_via_proxy = fake_connect_via_proxy

            await smart_proxy.handle(client_reader, client_writer)
        finally:
            smart_proxy.proxy_cache = original_cache
            smart_proxy.whitelist = original_whitelist
            smart_proxy.stats_store = original_stats_store
            smart_proxy.connect_via_proxy = original_connect_via_proxy

        self.assertEqual(len(stats_store.events), 1)
        event = stats_store.events[0]
        self.assertEqual(event.method, "CONNECT")
        self.assertEqual(event.host, "api.example.com")
        self.assertEqual(event.route, "proxy")
        self.assertTrue(event.success)
        self.assertIsNone(event.error)
        self.assertGreaterEqual(event.latency_ms, 0)

    async def test_handle_records_failed_direct_request(self):
        stats_store = FakeStatsStore()
        client_reader = FakeReader(
            lines=[
                b"CONNECT broken.example.com:443 HTTP/1.1\r\n",
                b"\r\n",
            ]
        )
        client_writer = FakeWriter()

        original_cache = smart_proxy.proxy_cache
        original_whitelist = smart_proxy.whitelist
        original_stats_store = smart_proxy.stats_store
        original_connect_direct_tunnel = smart_proxy.connect_direct_tunnel
        try:
            smart_proxy.proxy_cache = FakeProxyCache(None)
            smart_proxy.whitelist = FakeWhitelist(False)
            smart_proxy.stats_store = stats_store

            async def fake_connect_direct_tunnel(client_r, client_w, target):
                raise RuntimeError("boom")

            smart_proxy.connect_direct_tunnel = fake_connect_direct_tunnel

            await smart_proxy.handle(client_reader, client_writer)
        finally:
            smart_proxy.proxy_cache = original_cache
            smart_proxy.whitelist = original_whitelist
            smart_proxy.stats_store = original_stats_store
            smart_proxy.connect_direct_tunnel = original_connect_direct_tunnel

        self.assertEqual(len(stats_store.events), 1)
        event = stats_store.events[0]
        self.assertEqual(event.method, "CONNECT")
        self.assertEqual(event.host, "broken.example.com")
        self.assertEqual(event.route, "direct")
        self.assertFalse(event.success)
        self.assertEqual(event.error, "boom")
        self.assertGreaterEqual(event.latency_ms, 0)


class SetupScriptTests(unittest.TestCase):
    def test_setup_script_parses_as_powershell(self):
        script = (
            "$tokens=$null; $errors=$null; "
            "[System.Management.Automation.Language.Parser]::ParseFile("
            "(Resolve-Path setup.ps1), [ref]$tokens, [ref]$errors) > $null; "
            "if ($errors) { "
            "$errors | ForEach-Object { "
            "\"$($_.Extent.StartLineNumber):$($_.Extent.StartColumnNumber) $($_.Message)\" "
            "}; exit 1 }"
        )

        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            cwd=MODULE_PATH.parent,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class LauncherProfileTests(unittest.TestCase):
    def test_deepseek_profiles_use_dedicated_long_context_and_fast_models(self):
        launcher = (MODULE_PATH.parent / "claude-with-proxy.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn('Model = "deepseek-v4-pro[1m]"', launcher)
        self.assertIn('HaikuModel = "deepseek-v4-flash"', launcher)
        self.assertIn('SubagentModel = "deepseek-v4-flash"', launcher)
        self.assertIn('EffortLevel = "max"', launcher)
        self.assertIn("$env:CLAUDE_CODE_SUBAGENT_MODEL", launcher)
        self.assertIn("$env:CLAUDE_CODE_EFFORT_LEVEL", launcher)

    def test_launcher_checks_proxy_and_dashboard_ports(self):
        launcher = (MODULE_PATH.parent / "claude-with-proxy.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("$proxyReady", launcher)
        self.assertIn("$dashboardReady", launcher)
        self.assertIn('Test-LocalPort "8889"', launcher)
        self.assertIn('Test-LocalPort "8890"', launcher)
        self.assertIn("http://127.0.0.1:8890", launcher)


if __name__ == "__main__":
    unittest.main()
