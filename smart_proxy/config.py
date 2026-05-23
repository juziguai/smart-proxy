from dataclasses import dataclass
import json
import os
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class SmartProxyConfig:
    listen_host: str
    listen_port: int
    dashboard_host: str
    dashboard_port: int
    cache_sec: int
    read_size: int
    whitelist_file: Path
    whitelist_reload_sec: int
    stats_db_file: Path
    provider_health_path: Path


DEFAULTS = {
    "listen_host": "127.0.0.1",
    "listen_port": 8889,
    "dashboard_host": "127.0.0.1",
    "dashboard_port": 8890,
    "cache_sec": 3,
    "read_size": 65536,
    "whitelist_file": "whitelist.txt",
    "whitelist_reload_sec": 60,
    "stats_db_file": "smart-proxy-stats.db",
    "provider_health_path": "logs/provider-health.json",
}

ENV_KEYS = {
    "SMART_PROXY_LISTEN_HOST": "listen_host",
    "SMART_PROXY_LISTEN_PORT": "listen_port",
    "SMART_PROXY_DASHBOARD_HOST": "dashboard_host",
    "SMART_PROXY_DASHBOARD_PORT": "dashboard_port",
    "SMART_PROXY_CACHE_SEC": "cache_sec",
    "SMART_PROXY_READ_SIZE": "read_size",
    "SMART_PROXY_WHITELIST_FILE": "whitelist_file",
    "SMART_PROXY_WHITELIST_RELOAD_SEC": "whitelist_reload_sec",
    "SMART_PROXY_STATS_DB_FILE": "stats_db_file",
    "SMART_PROXY_PROVIDER_HEALTH_PATH": "provider_health_path",
}

INT_FIELDS = {
    "listen_port",
    "dashboard_port",
    "cache_sec",
    "read_size",
    "whitelist_reload_sec",
}

PATH_FIELDS = {
    "whitelist_file",
    "stats_db_file",
    "provider_health_path",
}


def load_config(config_path=None, environ=None, root_dir=None):
    env = os.environ if environ is None else environ
    root = Path(root_dir) if root_dir is not None else ROOT_DIR
    values = dict(DEFAULTS)

    selected_config = config_path or env.get("SMART_PROXY_CONFIG")
    if selected_config:
        values.update(_read_json_config(_resolve_path(selected_config, root)))
    else:
        default_config = root / "smart-proxy.json"
        if default_config.exists():
            values.update(_read_json_config(default_config))

    for env_key, field in ENV_KEYS.items():
        if env_key in env and env[env_key] != "":
            values[field] = env[env_key]

    for field in INT_FIELDS:
        values[field] = _positive_int(field, values[field])
    for field in PATH_FIELDS:
        values[field] = _resolve_path(values[field], root)

    return SmartProxyConfig(**values)


def _read_json_config(path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"config file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid config json: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("config json must be an object")
    unknown = sorted(set(payload) - set(DEFAULTS))
    if unknown:
        raise ValueError(f"unknown config fields: {', '.join(unknown)}")
    return payload


def _resolve_path(value, root):
    path = Path(value)
    if path.is_absolute():
        return path
    return root / path


def _positive_int(field, value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be an integer") from None
    if parsed <= 0:
        raise ValueError(f"{field} must be positive")
    return parsed


DEFAULT_CONFIG = load_config()
