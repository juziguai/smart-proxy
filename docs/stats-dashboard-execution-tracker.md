# Stats Dashboard Execution Tracker

This file tracks the smart-proxy stats dashboard implementation. Update the
status column whenever a task is completed.

Status legend:

- `Not Started`
- `In Progress`
- `Done`
- `Blocked`

| Step | Status | Scope | Deliverable | Verification |
| --- | --- | --- | --- | --- |
| 1 | Done | Stats backend foundation | SQLite store for proxy request events, schema initialization, aggregate queries, clear proxy stats behavior | Unit tests cover insert, aggregate, and clear behavior |
| 2 | Done | Proxy telemetry integration | `smart-proxy.py` records request route, success/failure, and latency without breaking forwarding | Existing proxy tests pass; new tests cover successful and failed request recording |
| 3 | Done | Claude usage reader | Incremental parser for Claude Code JSONL transcripts under `CLAUDE_CONFIG_DIR` or `%USERPROFILE%\.claude` | Tests cover full usage, missing fields, malformed JSONL, and subagent files |
| 4 | Done | Token aggregation | Day/week/month/all summaries for input, output, cache read, cache write, model usage, and web search/fetch counts | Tests verify range filters and totals |
| 5 | Done | Local dashboard API | Same `smart-proxy.py` process serves `127.0.0.1:8890` with summary and clear endpoints | API tests verify `/api/summary` and clear endpoint responses |
| 6 | Done | Web UI | Local dashboard page with cards, range selector, route split, token split, and clear button | Browser/manual check confirms page loads and updates from API |
| 7 | Done | Claude usage ingestion integration | Background scanner reads Claude JSONL usage events and upserts them into SQLite while smart-proxy runs | Tests verify scanner stores events and ignores repeated scans |
| 8 | Done | Launcher integration | `claude-with-proxy.ps1` checks both `8889` and `8890`, starts smart-proxy only when needed, prints dashboard URL | PowerShell parser test passes; manual run shows skip/start behavior |
| 9 | Done | README and docs | Usage docs for dashboard URL, stats behavior, clear behavior, and token source | README updated and cross-links design/source notes |
| 10 | Done | End-to-end verification | Full workflow from `C:\Users\juzi> .\claude.ps1` to proxy + dashboard + Claude Code | Verified new smart-proxy process listens on `8889` and `8890`; dashboard HTML and `/api/summary` return HTTP 200 |
| 11 | Done | Launcher hardening after live debug | Launcher writes sidecar stdout/stderr to `logs/` and waits for dashboard HTTP 200 instead of only checking the port | `claude.ps1`, `claude-with-proxy.ps1`, and `setup.ps1` parse successfully; `/api/summary` returns HTTP 200 |
| 12 | Done | Model leaderboard enhancement | Model split rows show total tokens plus input, output, cache read, and cache write breakdowns sorted by total tokens | Dashboard HTML contains model detail renderer; stats server tests and full suite pass; live dashboard serves updated HTML |
| 13 | Done | Cost estimate and trend chart | DeepSeek API usage is priced from official per-million-token rates; token-plan models are excluded from cash API spend; `/api/trends` powers token/cost chart | Pricing, summary, trends, API, and dashboard tests pass; full suite passes |
| 14 | Done | Trend filtering and KPI display polish | Dashboard rounds displayed costs to two decimals, adjusts KPI font size for long values, and supports multi-select model trend filtering | Store/server tests cover filtered trends; full suite passes |

## Current Decision Log

| Decision | Value |
| --- | --- |
| Proxy port | `127.0.0.1:8889` |
| Dashboard port | `127.0.0.1:8890` |
| Process model | One `smart-proxy.py` process serves both proxy and dashboard |
| Token source | Claude Code local transcript JSONL files |
| TLS strategy | No MITM, no HTTPS decryption |
| Clear button default | Clear smart-proxy request stats only |
| Headline token total | `input_tokens + output_tokens` |
| Cache tokens | Display separately as cache read and cache write |
| API cost estimate | DeepSeek API models are billable; MiniMax/MiMo token-plan models are non-billable in this dashboard |

## Update Rule

When implementation begins, update exactly one row to `In Progress`. When that
task is verified, update it to `Done` before starting the next row.
