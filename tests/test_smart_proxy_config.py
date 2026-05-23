import json
from tempfile import TemporaryDirectory
from pathlib import Path
import unittest

from smart_proxy_config import load_config

ROOT = Path(__file__).resolve().parents[1]


class SmartProxyConfigTests(unittest.TestCase):
    def test_defaults_match_existing_runtime_contract(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)

            config = load_config(root_dir=root, environ={})

        self.assertEqual(config.listen_host, "127.0.0.1")
        self.assertEqual(config.listen_port, 8889)
        self.assertEqual(config.dashboard_host, "127.0.0.1")
        self.assertEqual(config.dashboard_port, 8890)
        self.assertEqual(config.cache_sec, 3)
        self.assertEqual(config.read_size, 65536)
        self.assertEqual(config.whitelist_file, root / "whitelist.txt")
        self.assertEqual(config.stats_db_file, root / "smart-proxy-stats.db")
        self.assertEqual(config.provider_health_path, root / "logs" / "provider-health.json")

    def test_json_config_and_environment_overrides_are_merged(self):
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "smart-proxy.json"
            config_path.write_text(
                json.dumps(
                    {
                        "listen_port": 18089,
                        "dashboard_port": 18090,
                        "cache_sec": 9,
                        "whitelist_file": "config/whitelist.txt",
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(
                root_dir=root,
                environ={
                    "SMART_PROXY_CONFIG": str(config_path),
                    "SMART_PROXY_DASHBOARD_PORT": "28090",
                    "SMART_PROXY_STATS_DB_FILE": "data/runtime.db",
                },
            )

        self.assertEqual(config.listen_port, 18089)
        self.assertEqual(config.dashboard_port, 28090)
        self.assertEqual(config.cache_sec, 9)
        self.assertEqual(config.whitelist_file, root / "config" / "whitelist.txt")
        self.assertEqual(config.stats_db_file, root / "data" / "runtime.db")

    def test_invalid_numeric_values_fail_fast(self):
        with self.assertRaisesRegex(ValueError, "listen_port"):
            load_config(environ={"SMART_PROXY_LISTEN_PORT": "0"})

        with self.assertRaisesRegex(ValueError, "cache_sec"):
            load_config(environ={"SMART_PROXY_CACHE_SEC": "-1"})

    def test_python_services_read_runtime_defaults_from_config(self):
        smart_proxy_source = (ROOT / "smart-proxy.py").read_text(encoding="utf-8")
        stats_server_source = (ROOT / "stats_server.py").read_text(encoding="utf-8")

        self.assertIn("from smart_proxy_config import DEFAULT_CONFIG", smart_proxy_source)
        self.assertIn("LISTEN_PORT = DEFAULT_CONFIG.listen_port", smart_proxy_source)
        self.assertIn("STATS_DB_FILE = DEFAULT_CONFIG.stats_db_file", smart_proxy_source)
        self.assertIn("from smart_proxy_config import DEFAULT_CONFIG", stats_server_source)
        self.assertIn("DASHBOARD_PORT = DEFAULT_CONFIG.dashboard_port", stats_server_source)


if __name__ == "__main__":
    unittest.main()
