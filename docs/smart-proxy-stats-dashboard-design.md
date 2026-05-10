# Smart-Proxy Stats Dashboard Design

## Goal

Add a local statistics dashboard to smart-proxy that shows proxy request
telemetry and Claude Code token usage without decrypting HTTPS traffic or
modifying Claude Code.

## Non-Goals

- Do not implement HTTPS MITM.
- Do not read or expose API keys.
- Do not delete Claude Code transcript history when clearing smart-proxy stats.
- Do not depend on Claude Code's internal `stats-cache.json` for the first
  version.
- Do not attempt accurate PLUS/TEAM quota or remaining allowance unless a
  reliable local/API source is identified in a future phase.

## User-Facing Features

Version 1 should provide:

- Total requests
- Successful requests
- Failed requests
- Success rate
- Average latency
- Route counts:
  - direct
  - direct by whitelist
  - upstream proxy
- Token metrics from Claude Code transcripts:
  - input tokens
  - output tokens
  - cache read tokens
  - cache creation tokens
  - headline total tokens as `input + output`
- Time range filters:
  - day
  - week
  - month
  - all time
- Clear smart-proxy statistics
- A local web dashboard served from `127.0.0.1`

Nice-to-have after V1:

- Host/domain ranking
- Model breakdown
- Tokens per day chart
- Service tier/speed breakdown
- Web search/fetch request counts from transcript usage
- Export JSON

## Architecture

The dashboard should combine two local data sources.

smart-proxy should run as one Python process that provides both services:

- proxy service on `127.0.0.1:8889`
- dashboard service on `127.0.0.1:8890`

The user's normal entrypoint remains `C:\Users\juzi\claude.ps1`. The launcher
script should guard both services before starting Claude Code:

1. Check whether `127.0.0.1:8889` is listening.
2. Check whether `127.0.0.1:8890` is listening.
3. If both ports are already listening, skip service startup.
4. If either port is missing, start `smart-proxy.py` in the background.
5. Wait briefly and re-check both ports.
6. Continue with the existing environment variable injection, model provider
   menu, launch mode menu, and `claude` execution.

This preserves the current `.\claude.ps1` workflow while making the dashboard
available automatically.

### Source 1: Smart-Proxy Runtime Telemetry

smart-proxy owns these values because it observes every proxy request it handles:

- request start time
- method
- target host
- route decision
- success/failure
- latency
- bytes relayed if a future phase chooses to count them

This data is generated inside `smart-proxy.py` around:

- `handle`
- `connect_direct_tunnel`
- `connect_via_proxy`
- `http_direct`
- `http_via_proxy`
- `relay`

### Source 2: Claude Code Transcript Usage

Claude Code owns token usage. The dashboard should read local transcript JSONL
files from:

```text
%USERPROFILE%\.claude\projects\**\*.jsonl
```

If `CLAUDE_CONFIG_DIR` is set, use:

```text
%CLAUDE_CONFIG_DIR%\projects\**\*.jsonl
```

The parser should include subagent files:

```text
<config>\projects\<project>\<session-id>\subagents\agent-*.jsonl
```

For each JSONL entry:

- Keep only assistant transcript messages that have `message.usage`.
- Read `message.model` when present.
- Read `timestamp` for time range grouping.
- Sum token fields with missing values treated as zero.

## Data Model

Use SQLite for persisted smart-proxy telemetry and transcript indexing. SQLite is
available in Python's standard library and fits the project's zero-third-party
dependency style.

### Tables

`proxy_requests`

```sql
CREATE TABLE proxy_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  completed_at TEXT NOT NULL,
  method TEXT NOT NULL,
  host TEXT NOT NULL,
  route TEXT NOT NULL,
  success INTEGER NOT NULL,
  latency_ms INTEGER NOT NULL,
  error TEXT
);
```

`usage_events`

```sql
CREATE TABLE usage_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_file TEXT NOT NULL,
  source_line INTEGER NOT NULL,
  timestamp TEXT NOT NULL,
  session_id TEXT,
  model TEXT NOT NULL,
  input_tokens INTEGER NOT NULL,
  output_tokens INTEGER NOT NULL,
  cache_read_input_tokens INTEGER NOT NULL,
  cache_creation_input_tokens INTEGER NOT NULL,
  web_search_requests INTEGER NOT NULL,
  web_fetch_requests INTEGER NOT NULL,
  service_tier TEXT,
  speed TEXT,
  UNIQUE(source_file, source_line)
);
```

`usage_file_offsets`

```sql
CREATE TABLE usage_file_offsets (
  source_file TEXT PRIMARY KEY,
  size_bytes INTEGER NOT NULL,
  mtime REAL NOT NULL,
  processed_lines INTEGER NOT NULL,
  updated_at TEXT NOT NULL
);
```

Rationale:

- `proxy_requests` is owned by smart-proxy and can be cleared safely.
- `usage_events` is derived from Claude transcripts and can be rebuilt.
- `usage_file_offsets` enables incremental scanning without rescanning the full
  Claude history on every refresh.

## Runtime Components

### `stats_store.py`

Responsibility:

- Initialize SQLite schema.
- Insert proxy request records.
- Insert transcript usage events idempotently.
- Query aggregate snapshots for day/week/month/all.
- Clear smart-proxy-owned proxy request stats.
- Optionally clear derived transcript usage cache and rebuild.

Public shape:

```python
class StatsStore:
    def record_proxy_request(self, event: ProxyRequestEvent) -> None: ...
    def upsert_usage_event(self, event: UsageEvent) -> None: ...
    def get_summary(self, range_name: str) -> dict: ...
    def clear_proxy_stats(self) -> None: ...
    def clear_usage_cache(self) -> None: ...
```

### `claude_usage_reader.py`

Responsibility:

- Resolve Claude config directory.
- Enumerate main and subagent JSONL files.
- Incrementally parse new lines.
- Extract usage events.
- Ignore malformed JSONL lines without failing the dashboard.

Important parsing rule:

```text
entry.type == "assistant" and entry.message.usage exists
```

Fields:

```text
timestamp = entry.timestamp
session_id = entry.sessionId or entry.session_id or null
model = entry.message.model or "unknown"
usage = entry.message.usage
```

### `stats_server.py`

Responsibility:

- Serve local dashboard assets and JSON endpoints.
- Bind only to `127.0.0.1`.
- Avoid blocking proxy forwarding.

Suggested endpoints:

```text
GET  /                 dashboard HTML
GET  /api/summary?range=day|week|month|all
GET  /api/trends?range=day|week|month|all&model=<name>
GET  /api/recent-requests?limit=50
GET  /api/runtime-status
POST /api/clear-proxy-stats
POST /api/rebuild-usage-cache
```

Default address:

```text
http://127.0.0.1:8890
```

The launcher should print this URL after confirming that the dashboard port is
available.

### `smart-proxy.py` Integration

Responsibility:

- Create a global `StatsStore`.
- Record request start time in `handle`.
- Determine route:
  - `direct_whitelist`
  - `proxy`
  - `direct`
- Wrap forwarding calls with success/failure recording.
- Start a background usage scanner task.
- Start the dashboard HTTP server in the same Python process.
- Log both listening addresses on startup.

The process should fail clearly if the proxy port is unavailable. If only the
dashboard port is unavailable, the safest V1 behavior is also to fail clearly so
the launcher can report that the expected service did not start, rather than
silently running without the UI.

## Range Semantics

Use local time.

- `day`: since local midnight today.
- `week`: last 7 days including today.
- `month`: last 30 days including today.
- `all`: no lower bound.

The screenshot showed day/week/month segmented controls. Internally, the labels
can map to `day`, `week`, and `month`.

## Dashboard Display

V1 layout should be practical rather than decorative:

- Top row: total requests, total tokens, cache/thinking-style bucket, average
  latency.
- Secondary row: success/failure, input/output token split, route split.
- Model leaderboard: sort models by total `input + output` tokens and show
  each model's input, output, cache read, and cache write counters.
- Estimated API cost: apply local model pricing to usage totals and clearly
  label it as an estimate rather than the provider's final bill.
- Trend chart: show token and estimated cost movement for the selected range.
  It defaults to all models and supports multi-select model filtering.
- Alert strip: summarize current-range failures and slow requests above the KPI
  cards without hiding the rest of the dashboard. The first implementation uses
  built-in conservative thresholds: slow request `>= 3000ms`, Host warning
  failure rate `>= 10%`, and critical Host failure rate `>= 50%`.
- Runtime status panel: show whether Windows system proxy is currently enabled,
  the detected upstream proxy, whitelist path, whitelist entry count, and the
  latest whitelist load timestamp.
- Host diagnostics: rank recent hosts by failures, latency, and request count;
  show route composition, failure rate, slow request count, and health state so
  it is clear whether a host is going direct, whitelist-direct, or through the
  upstream proxy.
- Recent request list: show the latest proxy events with host, method, route,
  success state, latency, timestamp, and error text when present. Failed and
  slow rows should be visually highlighted.
- Range selector: day/week/month/all.
- Clear button: clears only smart-proxy request statistics by default.

Terminology:

- Display "cache read" and "cache write" instead of "cache/thinking" unless we
  confirm a reliable thinking-token field in a future phase.
- Display total token headline as `input + output`, matching Claude Code's own
  stats UI.
- Show cache token counts separately.
- DeepSeek API billed models use official CNY-per-million-token rates from
  <https://api-docs.deepseek.com/zh-cn/quick_start/pricing/>.
- MiniMax and MiMo are token-plan models in this setup, so the dashboard should
  display their tokens but not add them to estimated cash API spend.
- Cost values in the dashboard are rounded to two decimal places for display;
  raw API values may keep more precision.

## Performance Design

Proxy forwarding must stay hot-path-light.

Rules:

- Do not scan transcript files inside request handlers.
- Do not perform slow full-history aggregation for every dashboard refresh.
- Do not write SQLite synchronously for every relayed chunk.
- Record one proxy event per completed request.
- Run transcript scanning in a background task every few seconds or on dashboard
  refresh with throttling.

Expected overhead:

- Request recording: low single-digit milliseconds or less.
- Dashboard refresh: local-only, no external network.
- Transcript scan: background disk IO, incremental after initial build.

## Security And Privacy

- Bind dashboard to `127.0.0.1` only.
- Never print or store auth tokens.
- Do not expose raw prompts or transcript text in the dashboard.
- Store only usage numbers, model name, timestamp, and source bookkeeping.
- Provide a clear distinction between:
  - clearing smart-proxy request stats
  - rebuilding derived usage cache
  - deleting Claude Code transcripts, which should not be implemented by default

## Error Handling

Proxy telemetry:

- If stats recording fails, log once and continue forwarding traffic.
- Stats failures must not break proxy behavior.

Transcript parsing:

- Skip malformed JSONL lines.
- Skip entries without `message.usage`.
- Treat missing token fields as zero.
- If a file shrinks or changes unexpectedly, reset that file's offset and
  reprocess it idempotently.

Dashboard server:

- If dashboard port is occupied, either disable dashboard with a clear log or
  allow a configurable port.
- API errors should return JSON with `error` and HTTP 500.

## Testing Plan

Unit tests:

- Parse a JSONL line with full usage fields.
- Parse a JSONL line with missing cache fields.
- Ignore malformed JSONL.
- Include subagent paths in file enumeration.
- Aggregate day/week/month/all ranges.
- Record proxy request success and failure.
- Clear proxy stats without clearing usage events.

Integration tests:

- Start smart-proxy with a temp SQLite database.
- Feed fake transcript JSONL files under a temp Claude config directory.
- Verify `/api/summary` returns combined request and token metrics.
- Verify malformed transcript files do not break the endpoint.

Manual verification:

- Launch Claude through `claude-with-proxy.ps1`.
- Make a few requests.
- Open dashboard on localhost.
- Confirm proxy request count increments immediately.
- Confirm token counts appear shortly after Claude writes transcript updates.

## Rollout Plan

### Phase 1: Backend Stats Foundation

- Add SQLite stats store.
- Add request event recording.
- Add transcript parser.
- Add aggregate query tests.

### Phase 2: Local API

- Add a local HTTP stats server using Python standard library or `asyncio`.
- Expose summary and clear endpoints.
- Run the dashboard server by default in the same `smart-proxy.py` process.

### Phase 2.5: Launcher Integration

- Update `claude-with-proxy.ps1`.
- Check both `8889` and `8890` before launching Claude Code.
- Start `smart-proxy.py` only when at least one expected port is missing.
- Re-check both ports after startup.
- Print `http://127.0.0.1:8890` when the dashboard is available.
- Preserve direct argument passthrough behavior and the existing model/mode
  menus.

### Phase 3: Dashboard UI

- Add static HTML/CSS/JS served locally.
- Poll `/api/summary`.
- Implement range selector and clear button.

### Phase 4: Polish

- Add route breakdown.
- Add model breakdown.
- Add host ranking.
- Add README documentation.

## Open Questions

1. Dashboard default port:
   - Decision: proxy remains `8889`, dashboard uses `8890`.

2. Clear behavior:
   - Suggested default: clear only `proxy_requests`.
   - Add a separate "rebuild usage cache" action for derived Claude transcript
     data.

3. Thinking token label:
   - Current source review did not identify a stable `thinking_tokens` usage
     field. Do not show "thinking" as a separate metric in V1.

4. PLUS/TEAM labels:
   - Local transcript usage does not provide account quota category or remaining
     plan allowance. Treat as future work requiring a separate source.

## Recommendation

Build V1 as a local observability layer:

- smart-proxy records network/request metrics.
- a transcript reader records Claude token usage.
- a dashboard displays both with honest labels.

This gives the useful part of the screenshot without risky TLS interception or
fragile provider-specific API parsing.
