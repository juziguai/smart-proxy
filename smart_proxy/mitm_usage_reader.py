"""Read token usage JSONL captured by the mitmproxy token addon."""

from __future__ import annotations

import json
import os
from pathlib import Path
from datetime import datetime

from .config import ROOT_DIR
from .provider_classifier import classify_provider
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

    def read_capture_quality(self, since=None, until=None):
        quality = {
            "status": "unknown",
            "label": "未知",
            "detail": "尚未发现 token-capture 记录",
            "file_count": 0,
            "total_records": 0,
            "usage_records": 0,
            "problem_records": 0,
            "total_tokens": 0,
            "latest_at": "",
            "latest_file": "",
            "capture_status_counts": {},
        }
        files = self._iter_capture_files()
        quality["file_count"] = len(files)
        if not files:
            return quality

        latest_mtime = 0.0
        for path in files:
            try:
                mtime = path.stat().st_mtime
                if mtime >= latest_mtime:
                    latest_mtime = mtime
                    quality["latest_file"] = path.name
            except OSError:
                pass
            for payload in self._iter_payloads(path):
                if not self._within_window(payload.get("timestamp"), since, until):
                    continue
                status = self._capture_status(payload)
                counts = quality["capture_status_counts"]
                counts[status] = counts.get(status, 0) + 1
                quality["total_records"] += 1
                if status == "usage_found":
                    quality["usage_records"] += 1
                    quality["total_tokens"] += int(
                        payload.get("total_tokens")
                        or (
                            int(payload.get("input_tokens") or 0)
                            + int(payload.get("output_tokens") or 0)
                        )
                    )
                elif status in {"no_usage", "parse_failed", "stream_incomplete"}:
                    quality["problem_records"] += 1
                ts = payload.get("timestamp") or ""
                if ts and ts > quality["latest_at"]:
                    quality["latest_at"] = ts

        if quality["total_records"] <= 0:
            quality.update(
                {
                    "status": "unknown",
                    "label": "未知",
                    "detail": "今日尚无 token-capture 记录",
                }
            )
        elif quality["problem_records"] > 0:
            quality.update(
                {
                    "status": "partial",
                    "label": "部分",
                    "detail": self._quality_detail(quality),
                }
            )
        elif quality["usage_records"] > 0:
            quality.update(
                {
                    "status": "accurate",
                    "label": "准确",
                    "detail": self._quality_detail(quality),
                }
            )
        else:
            quality.update(
                {
                    "status": "unknown",
                    "label": "未知",
                    "detail": self._quality_detail(quality),
                }
            )
        return quality

    def read_recent_capture_requests(self, limit=50, since=None, source=None):
        limit = max(1, min(int(limit), 200))
        source = (source or "").strip().lower()
        if source and not _source_matches_capture(source):
            return []

        anonymous_requests = []
        requests_by_id = {}
        for path in self._iter_capture_files():
            for line_number, payload in self._iter_payloads(
                path,
                with_line_number=True,
            ):
                if not self._within_window(payload.get("timestamp"), since):
                    continue
                request = self._payload_to_recent_request(path, line_number, payload)
                request_id = request.get("token_usage", {}).get("request_id") or ""
                if not request_id:
                    anonymous_requests.append(request)
                    continue
                current = requests_by_id.get(request_id)
                if current is None or _capture_request_rank(request) >= _capture_request_rank(current):
                    requests_by_id[request_id] = request

        requests = [*requests_by_id.values(), *anonymous_requests]
        return sorted(
            requests,
            key=lambda request: _timestamp_sort_value(request.get("started_at")),
            reverse=True,
        )[:limit]

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
        for line_number, payload in self._iter_payloads(path, with_line_number=True):
            event = self._parse_payload(path, line_number, payload)
            if event is not None:
                events.append(event)
        return events

    def _iter_payloads(self, path, with_line_number=False):
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if with_line_number:
                        yield line_number, payload
                    else:
                        yield payload
        except OSError:
            return

    def _parse_payload(self, path, line_number, payload):
        if self._capture_status(payload) != "usage_found":
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

    def _payload_to_recent_request(self, path, line_number, payload):
        status = self._capture_status(payload)
        input_tokens = _int_value(payload.get("input_tokens"))
        output_tokens = _int_value(payload.get("output_tokens"))
        total_tokens = _int_value(payload.get("total_tokens"))
        if total_tokens <= 0:
            total_tokens = input_tokens + output_tokens

        provider = classify_provider(payload.get("host"))
        timestamp = payload.get("timestamp") or ""
        status_detail = payload.get("status_detail") or payload.get("evidence") or ""
        is_pending = status == "request_started"
        is_problem = status in {"parse_failed", "stream_incomplete"}
        return {
            "started_at": timestamp,
            "completed_at": timestamp,
            "method": payload.get("method") or "POST",
            "host": payload.get("host") or "",
            **provider,
            "route": "mitm_token_capture",
            "success": not is_problem,
            "latency_ms": None,
            "connect_latency_ms": None,
            "duration_ms": None,
            "stage": status,
            "client_addr": "127.0.0.1",
            "client_port": 8891,
            "target_port": 443,
            "upstream_host": "",
            "upstream_port": None,
            "client_pid": None,
            "client_process": "MITM Token Capture",
            "client_exe": "",
            "client_label": "Claude Code",
            "client_evidence": f"token-capture JSONL {path.name}:{line_number}",
            "client_chain": "Claude Code > MITM Token Capture",
            "user_agent": "",
            "slow": False,
            "alertable": False,
            "error": "",
            "diagnosis": "",
            "diagnosis_label": "捕获中" if is_pending else "",
            "diagnosis_detail": (
                "MITM 已收到 Claude Code 模型请求，等待响应完成后写入 token usage"
                if is_pending
                else ""
            ),
            "diagnosis_batch_count": 0,
            "capture_status": status,
            "capture_status_detail": status_detail,
            "token_usage": {
                "model": payload.get("model") or "unknown",
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "cache_read_input_tokens": _int_value(
                    payload.get("cache_read_input_tokens")
                ),
                "cache_creation_input_tokens": _int_value(
                    payload.get("cache_creation_input_tokens")
                ),
                "reasoning_tokens": _int_value(payload.get("reasoning_tokens")),
                "evidence": payload.get("evidence") or "",
                "confidence": payload.get("confidence") or 0,
                "request_id": payload.get("request_id") or "",
            },
        }

    def _capture_status(self, payload):
        status = str(payload.get("capture_status") or "").strip()
        if status:
            return status
        input_tokens = int(payload.get("input_tokens") or 0)
        output_tokens = int(payload.get("output_tokens") or 0)
        return "usage_found" if input_tokens + output_tokens > 0 else "unknown"

    def _within_window(self, timestamp, since=None, until=None):
        if not since and not until:
            return True
        if not timestamp:
            return False
        try:
            current = _parse_datetime(timestamp)
            start = _parse_datetime(since) if since else None
            end = _parse_datetime(until) if until else None
        except ValueError:
            return False
        if start and current < start:
            return False
        if end and current >= end:
            return False
        return True

    def _quality_detail(self, quality):
        counts = quality["capture_status_counts"]
        parts = [
            f"usage {counts.get('usage_found', 0)}",
            f"no_usage {counts.get('no_usage', 0)}",
            f"incomplete {counts.get('stream_incomplete', 0)}",
            f"parse_failed {counts.get('parse_failed', 0)}",
        ]
        return " · ".join(parts)


def default_capture_dir():
    configured = os.environ.get("SMART_PROXY_TOKEN_CAPTURE_DIR")
    if configured:
        return Path(configured)
    return ROOT_DIR / "logs"


def _parse_datetime(value):
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def _timestamp_sort_value(value):
    try:
        return _parse_datetime(value).timestamp()
    except (TypeError, ValueError, OSError):
        return 0.0


def _int_value(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _source_matches_capture(source):
    haystack = "claude code / mitm token capture bun.exe cli.cjs 127.0.0.1:8891"
    return source in haystack or "claude code" in source or "mitm" in source


def _capture_request_rank(request):
    status = request.get("capture_status") or ""
    ranks = {
        "request_started": 0,
        "no_usage": 1,
        "parse_failed": 1,
        "stream_incomplete": 1,
        "usage_found": 2,
    }
    return (
        ranks.get(status, 1),
        _timestamp_sort_value(request.get("started_at")),
    )
