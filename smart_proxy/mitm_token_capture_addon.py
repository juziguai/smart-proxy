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
    MODEL_HOST_MARKERS,
    extract_request_model,
    extract_token_usage,
    host_allowed,
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
        if hasattr(flow.request, "get_content"):
            request_content = flow.request.get_content(strict=False) or b""
        else:
            request_content = getattr(flow.request, "content", b"") or b""
        content_type = response.headers.get("content-type", "")
        usage = extract_token_usage(
            content,
            content_type,
            host=host,
            method=getattr(flow.request, "method", ""),
            path=getattr(flow.request, "path", ""),
            request_id=getattr(flow, "id", ""),
            request_model=extract_request_model(request_content),
        )
        if usage is None:
            return

        self.output_dir.mkdir(parents=True, exist_ok=True)
        local_date = datetime.now().astimezone().strftime("%Y-%m-%d")
        path = self.output_dir / f"token-capture-{local_date}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(usage.to_json_line() + "\n")


addons = [TokenUsageCaptureAddon()]
