from dataclasses import dataclass
import json
import os
from pathlib import Path


@dataclass(frozen=True)
class UsageEvent:
    source_file: str
    source_line: int
    timestamp: str
    session_id: str | None
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    web_search_requests: int
    web_fetch_requests: int
    service_tier: str | None
    speed: str | None


class ClaudeUsageReader:
    def __init__(self, config_home=None):
        self.config_home = Path(config_home) if config_home else default_config_home()
        self.projects_dir = self.config_home / "projects"

    def read_usage_events(self):
        events = []
        for transcript in self._iter_transcript_files():
            events.extend(self._read_transcript(transcript))
        return sorted(
            events,
            key=lambda event: (
                event.timestamp,
                event.source_file,
                event.source_line,
            ),
        )

    def _iter_transcript_files(self):
        if not self.projects_dir.exists():
            return []
        return sorted(
            path
            for path in self.projects_dir.rglob("*.jsonl")
            if path.is_file()
        )

    def _read_transcript(self, path):
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
            entry = json.loads(line)
        except json.JSONDecodeError:
            return None

        if entry.get("type") != "assistant":
            return None

        message = entry.get("message")
        if not isinstance(message, dict):
            return None

        usage = message.get("usage")
        if not isinstance(usage, dict):
            return None

        server_tool_use = usage.get("server_tool_use") or {}
        return UsageEvent(
            source_file=str(path),
            source_line=line_number,
            timestamp=entry.get("timestamp") or "",
            session_id=entry.get("sessionId") or entry.get("session_id"),
            model=message.get("model") or "unknown",
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            cache_read_input_tokens=int(
                usage.get("cache_read_input_tokens") or 0
            ),
            cache_creation_input_tokens=int(
                usage.get("cache_creation_input_tokens") or 0
            ),
            web_search_requests=int(
                server_tool_use.get("web_search_requests") or 0
            ),
            web_fetch_requests=int(
                server_tool_use.get("web_fetch_requests") or 0
            ),
            service_tier=usage.get("service_tier"),
            speed=usage.get("speed"),
        )


def default_config_home():
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    if configured:
        return Path(configured)
    return Path.home() / ".claude"
