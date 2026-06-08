"""mitmproxy addon that captures model API token usage only.

Run with:
    mitmdump -s smart_proxy/mitm_token_capture_addon.py --listen-host 127.0.0.1 --listen-port 8891
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
from datetime import datetime

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from smart_proxy.config import ROOT_DIR as CONFIG_ROOT_DIR
from smart_proxy.token_capture import (
    CapturedTokenUsage,
    MODEL_HOST_MARKERS,
    extract_token_capture_record,
    extract_request_model,
    host_allowed,
    local_now_iso,
    provider_for_host,
)


def _marker_tuple(value: str | None) -> tuple[str, ...]:
    if not value:
        return MODEL_HOST_MARKERS
    return tuple(part.strip().lower() for part in value.split(",") if part.strip())


class TokenUsageCaptureAddon:
    def __init__(self) -> None:
        self.markers = _marker_tuple(os.environ.get("SMART_PROXY_MITM_ALLOWED_HOSTS"))
        self.output_dir = Path(
            os.environ.get("SMART_PROXY_TOKEN_CAPTURE_DIR")
            or CONFIG_ROOT_DIR / "logs"
        )

    def request(self, flow) -> None:
        host = getattr(flow.request, "pretty_host", "") or getattr(
            flow.request,
            "host",
            "",
        )
        if not host_allowed(host, self.markers):
            return

        request_content = self._request_content(flow)
        provider = provider_for_host(host)
        usage = CapturedTokenUsage(
            capture_status="request_started",
            status_detail="model request received by MITM Token Capture",
            timestamp=local_now_iso(),
            request_id=getattr(flow, "id", ""),
            provider=provider["provider"],
            provider_key=provider["provider_key"],
            host=host,
            method=getattr(flow.request, "method", ""),
            path=getattr(flow.request, "path", ""),
            model=extract_request_model(request_content) or "unknown",
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
            reasoning_tokens=0,
            evidence="request hook: host matched model provider",
            confidence=0.8,
        )
        self._write_usage(usage)

    def response(self, flow) -> None:
        host = getattr(flow.request, "pretty_host", "") or getattr(
            flow.request,
            "host",
            "",
        )
        if not host_allowed(host, self.markers):
            return

        response = getattr(flow, "response", None)
        if response is None:
            return

        if hasattr(response, "get_content"):
            content = response.get_content(strict=False) or b""
        else:
            content = getattr(response, "content", b"") or b""
        request_content = self._request_content(flow)
        content_type = response.headers.get("content-type", "")
        usage = extract_token_capture_record(
            content,
            content_type,
            host=host,
            method=getattr(flow.request, "method", ""),
            path=getattr(flow.request, "path", ""),
            request_id=getattr(flow, "id", ""),
            request_model=extract_request_model(request_content),
        )

        self._write_usage(usage)

    def _request_content(self, flow) -> bytes:
        if hasattr(flow.request, "get_content"):
            return flow.request.get_content(strict=False) or b""
        return getattr(flow.request, "content", b"") or b""

    def _write_usage(self, usage: CapturedTokenUsage) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        local_date = datetime.now().astimezone().strftime("%Y-%m-%d")
        path = self.output_dir / f"token-capture-{local_date}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(usage.to_json_line() + "\n")


addons = [TokenUsageCaptureAddon()]
