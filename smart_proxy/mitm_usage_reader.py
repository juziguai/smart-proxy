"""Read token usage JSONL captured by the mitmproxy token addon."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .config import ROOT_DIR
from .usage_events import UsageEvent


class MitmUsageReader:
    def __init__(self, log_dir=None):
        self.log_dir = Path(log_dir) if log_dir else default_capture_dir()

    def read_usage_events(self):
        events = []
        for path in self._iter_capture_files():
            events.extend(self._read_capture_file(path))
        return sorted(
            events,
            key=lambda event: (
                event.timestamp,
                event.source_file,
                event.source_line,
            ),
        )

    def _iter_capture_files(self):
        if not self.log_dir.exists():
            return []
        return sorted(
            path
            for path in self.log_dir.glob("token-capture-*.jsonl")
            if path.is_file()
        )

    def _read_capture_file(self, path):
        events = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    event = self._parse_line(path, line_number, line)
                    if event is not None:
                        events.append(event)
        except OSError:
            return []
        return events

    def _parse_line(self, path, line_number, line):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            return None

        input_tokens = int(payload.get("input_tokens") or 0)
        output_tokens = int(payload.get("output_tokens") or 0)
        if input_tokens + output_tokens <= 0:
            return None

        return UsageEvent(
            source_file=str(path),
            source_line=line_number,
            timestamp=payload.get("timestamp") or "",
            session_id=payload.get("request_id") or None,
            model=payload.get("model") or "unknown",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=int(
                payload.get("cache_read_input_tokens") or 0
            ),
            cache_creation_input_tokens=int(
                payload.get("cache_creation_input_tokens") or 0
            ),
            web_search_requests=0,
            web_fetch_requests=0,
            service_tier="mitm",
            speed=payload.get("provider_key") or payload.get("provider"),
        )


def default_capture_dir():
    configured = os.environ.get("SMART_PROXY_TOKEN_CAPTURE_DIR")
    if configured:
        return Path(configured)
    return ROOT_DIR / "logs"
