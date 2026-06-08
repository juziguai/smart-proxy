"""Token usage extraction helpers for decrypted model API traffic."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from typing import Any


MODEL_HOST_MARKERS = (
    "xiaomimimo.com",
    "mimo-v2.com",
    "deepseek.com",
    "openai.com",
    "chatgpt.com",
    "anthropic.com",
    "claude.ai",
    "openrouter.ai",
    "minimax.com",
    "minimaxi.com",
    "generativelanguage.googleapis.com",
    "aiplatform.googleapis.com",
    "cloudcode-pa.googleapis.com",
)


@dataclass(frozen=True)
class CapturedTokenUsage:
    capture_status: str
    status_detail: str
    timestamp: str
    request_id: str
    provider: str
    provider_key: str
    host: str
    method: str
    path: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    reasoning_tokens: int
    evidence: str
    confidence: float

    def to_json_line(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":"))


def local_now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def host_allowed(host: str, markers: tuple[str, ...] = MODEL_HOST_MARKERS) -> bool:
    value = (host or "").lower()
    return any(marker in value for marker in markers)


def provider_for_host(host: str) -> dict[str, Any]:
    try:
        from .provider_classifier import classify_provider

        meta = classify_provider(host)
    except Exception:
        meta = {}

    return {
        "provider": meta.get("provider") or "Unknown Provider",
        "provider_key": meta.get("provider_key") or "unknown",
    }


def extract_token_usage(
    content: bytes,
    content_type: str = "",
    *,
    host: str = "",
    method: str = "",
    path: str = "",
    request_id: str = "",
    request_model: str = "",
    timestamp: str | None = None,
) -> CapturedTokenUsage | None:
    usage = extract_token_capture_record(
        content,
        content_type,
        host=host,
        method=method,
        path=path,
        request_id=request_id,
        request_model=request_model,
        timestamp=timestamp,
    )
    if usage.capture_status != "usage_found":
        return None
    return usage


def extract_token_capture_record(
    content: bytes,
    content_type: str = "",
    *,
    host: str = "",
    method: str = "",
    path: str = "",
    request_id: str = "",
    request_model: str = "",
    timestamp: str | None = None,
) -> CapturedTokenUsage:
    text = _decode_text(content)
    provider = provider_for_host(host)
    base = {
        "timestamp": timestamp or local_now_iso(),
        "request_id": request_id,
        "provider": provider["provider"],
        "provider_key": provider["provider_key"],
        "host": host,
        "method": method,
        "path": path,
        "model": request_model or "unknown",
    }
    if not text:
        status = "stream_incomplete" if "text/event-stream" in content_type.lower() else "no_usage"
        return _empty_capture_record(
            base,
            status,
            "empty response body",
        )

    usage, failure_status, failure_detail = _extract_usage_with_status(
        text,
        content_type,
    )
    if usage is None:
        return _empty_capture_record(
            base,
            failure_status,
            failure_detail,
        )

    total_tokens = usage.get("total_tokens") or (
        usage["input_tokens"] + usage["output_tokens"]
    )
    if total_tokens <= 0:
        return _empty_capture_record(
            base,
            "no_usage",
            "usage fields contained zero tokens",
        )

    model = usage.get("model") or ""
    if model == "unknown":
        model = ""
    return CapturedTokenUsage(
        capture_status="usage_found",
        status_detail="usage fields captured",
        timestamp=base["timestamp"],
        request_id=base["request_id"],
        provider=base["provider"],
        provider_key=base["provider_key"],
        host=base["host"],
        method=base["method"],
        path=base["path"],
        model=model or request_model or "unknown",
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        total_tokens=total_tokens,
        cache_read_input_tokens=usage["cache_read_input_tokens"],
        cache_creation_input_tokens=usage["cache_creation_input_tokens"],
        reasoning_tokens=usage["reasoning_tokens"],
        evidence=usage["evidence"],
        confidence=usage["confidence"],
    )


def extract_request_model(content: bytes) -> str:
    text = _decode_text(content)
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return ""
    return _find_model(payload)


def _decode_text(content: bytes) -> str:
    if not content:
        return ""
    for encoding in ("utf-8", "utf-8-sig"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _looks_like_sse(text: str, content_type: str) -> bool:
    return "text/event-stream" in content_type.lower() or "\ndata:" in text


def _extract_usage_with_status(
    text: str,
    content_type: str,
) -> tuple[dict[str, Any] | None, str, str]:
    stripped = text.strip()
    if stripped and stripped[0] in "{[":
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            return None, "parse_failed", f"json parse failed: {exc.msg}"
        usage = _usage_from_payload(payload)
        if usage is not None:
            return usage, "", ""
        return None, "no_usage", "json response did not contain usage fields"

    if _looks_like_sse(text, content_type):
        usage, saw_done, parse_errors = _extract_from_sse_text_with_meta(text)
        if usage is not None:
            return usage, "", ""
        if parse_errors:
            return None, "parse_failed", f"sse data parse failed {parse_errors} time(s)"
        if not saw_done:
            return None, "stream_incomplete", "sse stream ended before [DONE] and no usage fields were captured"
        return None, "no_usage", "sse stream completed without usage fields"

    return None, "no_usage", "response did not look like JSON or SSE usage payload"


def _extract_from_json_text(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return _usage_from_payload(payload)


def _extract_from_sse_text(text: str) -> dict[str, Any] | None:
    usage, _saw_done, _parse_errors = _extract_from_sse_text_with_meta(text)
    return usage


def _extract_from_sse_text_with_meta(
    text: str,
) -> tuple[dict[str, Any] | None, bool, int]:
    merged = _empty_usage()
    evidence = []
    model = ""
    found = False
    saw_done = False
    parse_errors = 0
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data:
            continue
        if data == "[DONE]":
            saw_done = True
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            parse_errors += 1
            continue
        item = _usage_from_payload(payload)
        if item is None:
            continue
        found = True
        model = model or item.get("model") or ""
        evidence.append(item["evidence"])
        _merge_usage_max(merged, item)

    if not found:
        return None, saw_done, parse_errors
    merged["model"] = model or merged.get("model") or "unknown"
    merged["evidence"] = "sse data usage: " + "; ".join(sorted(set(evidence)))
    merged["confidence"] = 0.9
    return merged, saw_done, parse_errors


def _empty_capture_record(
    base: dict[str, Any],
    capture_status: str,
    status_detail: str,
) -> CapturedTokenUsage:
    return CapturedTokenUsage(
        capture_status=capture_status,
        status_detail=status_detail,
        timestamp=base["timestamp"],
        request_id=base["request_id"],
        provider=base["provider"],
        provider_key=base["provider_key"],
        host=base["host"],
        method=base["method"],
        path=base["path"],
        model=base["model"],
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
        reasoning_tokens=0,
        evidence=status_detail,
        confidence=0.0,
    )


def _usage_from_payload(payload: Any) -> dict[str, Any] | None:
    candidates: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    _collect_usage_candidates(payload, candidates)
    if not candidates:
        return None

    merged = _empty_usage()
    model = _find_model(payload)
    evidence = []
    for name, usage, owner in candidates:
        item = _normalize_usage(name, usage, owner)
        if item is None:
            continue
        evidence.append(item["evidence"])
        _merge_usage_max(merged, item)
        model = model or _find_model(owner)

    total = merged["input_tokens"] + merged["output_tokens"]
    if total <= 0 and merged["total_tokens"] > 0:
        merged["output_tokens"] = merged["total_tokens"]
    if merged["input_tokens"] + merged["output_tokens"] <= 0:
        return None

    merged["model"] = model or "unknown"
    merged["evidence"] = "; ".join(sorted(set(evidence)))
    merged["confidence"] = 0.95
    return merged


def _collect_usage_candidates(
    node: Any,
    candidates: list[tuple[str, dict[str, Any], dict[str, Any]]],
    owner: dict[str, Any] | None = None,
) -> None:
    if isinstance(node, dict):
        for key in ("usage", "usageMetadata"):
            value = node.get(key)
            if isinstance(value, dict):
                candidates.append((key, value, node))
        for value in node.values():
            _collect_usage_candidates(value, candidates, node)
    elif isinstance(node, list):
        for value in node:
            _collect_usage_candidates(value, candidates, owner)


def _normalize_usage(
    name: str,
    usage: dict[str, Any],
    owner: dict[str, Any],
) -> dict[str, Any] | None:
    input_tokens = _first_int(
        usage,
        "input_tokens",
        "prompt_tokens",
        "promptTokenCount",
    )
    output_tokens = _first_int(
        usage,
        "output_tokens",
        "completion_tokens",
        "completionTokenCount",
        "candidatesTokenCount",
    )
    total_tokens = _first_int(usage, "total_tokens", "totalTokenCount")
    cache_read = _first_int(
        usage,
        "cache_read_input_tokens",
        "cachedContentTokenCount",
    )
    cache_creation = _first_int(usage, "cache_creation_input_tokens")
    reasoning_tokens = _first_int(usage, "thoughtsTokenCount")

    for details_key in (
        "input_tokens_details",
        "prompt_tokens_details",
    ):
        details = usage.get(details_key)
        if isinstance(details, dict):
            cache_read = max(cache_read, _first_int(details, "cached_tokens"))

    for details_key in (
        "output_tokens_details",
        "completion_tokens_details",
    ):
        details = usage.get(details_key)
        if isinstance(details, dict):
            reasoning_tokens = max(
                reasoning_tokens,
                _first_int(details, "reasoning_tokens"),
            )

    if total_tokens and not output_tokens and input_tokens:
        output_tokens = max(0, total_tokens - input_tokens)
    if total_tokens and not input_tokens and not output_tokens:
        output_tokens = total_tokens

    if input_tokens + output_tokens + total_tokens <= 0:
        return None

    item = _empty_usage()
    item.update(
        {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_creation,
            "reasoning_tokens": reasoning_tokens,
            "model": _find_model(owner),
            "evidence": f"{name} fields: {','.join(sorted(usage.keys()))}",
        }
    )
    return item


def _empty_usage() -> dict[str, Any]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "reasoning_tokens": 0,
        "model": "",
        "evidence": "",
        "confidence": 0.0,
    }


def _merge_usage_max(target: dict[str, Any], item: dict[str, Any]) -> None:
    for key in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "reasoning_tokens",
    ):
        target[key] = max(int(target.get(key) or 0), int(item.get(key) or 0))
    if not target.get("model") and item.get("model"):
        target["model"] = item["model"]


def _first_int(mapping: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = mapping.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _find_model(node: Any) -> str:
    if isinstance(node, dict):
        model = node.get("model")
        if isinstance(model, str) and model:
            return model
        for value in node.values():
            found = _find_model(value)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_model(value)
            if found:
                return found
    return ""
