"""Host-to-provider classification helpers for proxy telemetry."""

from __future__ import annotations

import json
import time
from pathlib import Path

from .config import DEFAULT_CONFIG


RULE_CACHE_SEC = 30

UNKNOWN_PROVIDER = {
    "provider_key": "unknown",
    "provider": "Unknown Provider",
    "provider_name": "Unknown Provider",
    "provider_kind": "unknown",
    "is_model_provider": False,
    "provider_source": "none",
    "provider_match": "",
    "provider_evidence": "no provider rule matched",
    "provider_confidence": 0.0,
}

OTHER_PROVIDER = {
    "provider_key": "other",
    "provider": "Other (其他网络流量)",
    "provider_name": "Other",
    "provider_kind": "other",
    "is_model_provider": False,
    "provider_source": "default",
    "provider_match": "",
    "provider_evidence": "matched non-model traffic rule",
    "provider_confidence": 0.6,
}

DEFAULT_PROVIDER_RULES = (
    {
        "provider_key": "mimo",
        "provider": "MiMo (小米中转)",
        "provider_name": "MiMo",
        "provider_kind": "model",
        "is_model_provider": True,
        "markers": ("xiaomimimo.com", "mimo"),
        "source": "default",
    },
    {
        "provider_key": "deepseek",
        "provider": "DeepSeek (深度求索)",
        "provider_name": "DeepSeek",
        "provider_kind": "model",
        "is_model_provider": True,
        "markers": ("deepseek.com",),
        "source": "default",
    },
    {
        "provider_key": "minimax",
        "provider": "MiniMax (海螺AI)",
        "provider_name": "MiniMax",
        "provider_kind": "model",
        "is_model_provider": True,
        "markers": ("minimax.com", "minimaxi.com"),
        "source": "default",
    },
    {
        "provider_key": "anthropic",
        "provider": "Anthropic (Claude)",
        "provider_name": "Anthropic",
        "provider_kind": "model",
        "is_model_provider": True,
        "markers": ("anthropic.com", "claude.ai"),
        "source": "default",
    },
    {
        "provider_key": "openai",
        "provider": "OpenAI (ChatGPT/Codex)",
        "provider_name": "OpenAI",
        "provider_kind": "model",
        "is_model_provider": True,
        "markers": ("openai.com", "chatgpt.com"),
        "source": "default",
    },
    {
        "provider_key": "google",
        "provider": "Google (Gemini/Antigravity)",
        "provider_name": "Google",
        "provider_kind": "model",
        "is_model_provider": True,
        "markers": (
            "generativelanguage.googleapis.com",
            "aiplatform.googleapis.com",
            "cloudcode-pa.googleapis.com",
            "cloudaicompanion",
            "antigravity",
        ),
        "source": "default",
    },
)

DEFAULT_OTHER_MARKERS = (
    "dns.google",
    "github.com",
    "gstatic.com",
    "googleusercontent.com",
    "douyin.com",
)

_RULE_CACHE = {
    "loaded_at": 0.0,
    "path": None,
    "mtime": None,
    "status": None,
}


def classify_provider(host, rules_path=None):
    value = (host or "").strip().lower()
    if not value or value in {"unknown", "(unknown)", "-"}:
        result = dict(UNKNOWN_PROVIDER)
        result["provider_evidence"] = "host is empty or unknown"
        return result

    status = get_provider_rules_status(rules_path)
    for rule in status["rules"]:
        marker = _matching_marker(value, rule["markers"])
        if marker:
            return {
                "provider_key": rule["provider_key"],
                "provider": rule["provider"],
                "provider_name": rule["provider_name"],
                "provider_kind": rule["provider_kind"],
                "is_model_provider": rule["is_model_provider"],
                "provider_source": rule["source"],
                "provider_match": marker,
                "provider_evidence": f"host contains {marker}",
                "provider_confidence": 0.95,
            }

    other_marker = _matching_marker(value, status["other_markers"])
    if other_marker:
        result = dict(OTHER_PROVIDER)
        result["provider_match"] = other_marker
        result["provider_evidence"] = f"host contains {other_marker}"
        return result

    return dict(UNKNOWN_PROVIDER)


def get_provider_rules_status(rules_path=None):
    path = Path(rules_path or DEFAULT_CONFIG.provider_rules_path)
    now = time.monotonic()
    try:
        mtime = path.stat().st_mtime if path.exists() else None
    except OSError:
        mtime = None

    cached = _RULE_CACHE["status"]
    if (
        cached
        and _RULE_CACHE["path"] == str(path)
        and _RULE_CACHE["mtime"] == mtime
        and now - _RULE_CACHE["loaded_at"] < RULE_CACHE_SEC
    ):
        return cached

    status = _load_provider_rules(path)
    _RULE_CACHE.update(
        {
            "loaded_at": now,
            "path": str(path),
            "mtime": mtime,
            "status": status,
        }
    )
    return status


def _load_provider_rules(path):
    rules = [_normalize_rule(rule, "default") for rule in DEFAULT_PROVIDER_RULES]
    other_markers = list(DEFAULT_OTHER_MARKERS)
    payload = {}
    error = ""

    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("provider rules json must be an object")
            for rule in payload.get("providers", []):
                rules.append(_normalize_rule(rule, "custom"))
            for marker in payload.get("other_markers", []):
                if isinstance(marker, str) and marker.strip():
                    other_markers.append(marker.strip().lower())
        except (OSError, ValueError, json.JSONDecodeError, TypeError) as exc:
            error = str(exc)

    return {
        "path": str(path),
        "exists": path.exists(),
        "error": error,
        "rules": rules,
        "other_markers": tuple(dict.fromkeys(other_markers)),
        "format": {
            "providers": [
                {
                    "provider_key": "moonshot",
                    "provider": "Moonshot (Kimi)",
                    "provider_name": "Moonshot",
                    "provider_kind": "model",
                    "is_model_provider": True,
                    "markers": ["moonshot.cn"],
                }
            ],
            "other_markers": ["example-static-host.com"],
        },
    }


def _normalize_rule(rule, source):
    markers = rule.get("markers", ()) if isinstance(rule, dict) else ()
    if not isinstance(markers, (list, tuple)):
        markers = ()
    clean_markers = tuple(
        dict.fromkeys(
            marker.strip().lower()
            for marker in markers
            if isinstance(marker, str) and marker.strip()
        )
    )
    if not clean_markers:
        raise ValueError("provider rule markers must be a non-empty list")

    key = str(rule.get("provider_key") or rule.get("key") or "").strip().lower()
    name = str(rule.get("provider_name") or rule.get("name") or key or "Provider").strip()
    provider = str(rule.get("provider") or name).strip()
    kind = str(rule.get("provider_kind") or rule.get("kind") or "model").strip()
    is_model = bool(rule.get("is_model_provider", kind == "model"))

    return {
        "provider_key": key or name.lower().replace(" ", "-"),
        "provider": provider,
        "provider_name": name,
        "provider_kind": kind,
        "is_model_provider": is_model,
        "markers": clean_markers,
        "source": source,
    }


def _matching_marker(host, markers):
    for marker in markers:
        if marker and marker in host:
            return marker
    return ""
