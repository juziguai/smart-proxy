from dataclasses import dataclass


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
