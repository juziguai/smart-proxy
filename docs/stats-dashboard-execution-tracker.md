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
| 15 | Done | Proxy diagnostics panels | Dashboard adds runtime proxy status, Host breakdown, and latest proxy request list backed by `/api/runtime-status` and `/api/recent-requests` | Full suite passes; live endpoints return runtime status and recent request data; dashboard HTML contains new renderers |
| 16 | Done | Alert strip and anomaly highlighting | Dashboard summarizes slow requests and failing Hosts, marks Host/request rows by health state, and exposes slow/failure metadata in summary/recent APIs | Store/server tests cover alert fields, slow request markers, and dashboard render hooks |
| 17 | Done | Editable dashboard layout | Dashboard adds an edit-layout toggle, module drag sorting, local browser persistence, and restore-default action | Server HTML tests cover layout controls, widget ids, localStorage persistence hooks, and drag handlers |
| 18 | Done | Telemetry semantics hardening | Split connect latency from total tunnel/request duration so long-lived CONNECT streams no longer trigger slow-request alerts; legacy no-error CONNECT failures no longer trigger failure alerts | Full suite passes; live `/api/summary` reports zero alerts with historical DeepSeek tunnel rows retained as raw stats |
| 19 | Done | Console tab shell redesign | Dashboard is reorganized into Overview, Providers, Requests, Usage & Cost, Whitelist, and Doctor tabs while preserving existing metrics and overview layout editing | Server HTML tests cover tab shell, status chips, domain tab placement, and existing dashboard render hooks |
| 20 | Done | Reference console visual alignment | Dashboard shell now matches the approved reference: flat full-width topbar, underline tabs, green health banner, 5 KPI cards, trend + Provider health grid, and bottom anomaly/request tables | Added reference-layout HTML test; full test suite passes; live dashboard screenshot captured at `logs/dashboard-reference-match.png` |
| 21 | Done | Local day timezone filter fix | Day/week/month filters compare timestamp values after timezone normalization so UTC-stored events after local midnight appear in today's dashboard | Added regression test for UTC events inside China local day; full test suite passes; live screenshot captured at `logs/dashboard-data-restored.png` |
| 22 | Done | Dense data visual polish | Dashboard handles real high-volume data better: KPI values fit cards, alert chips collapse with `+N`, Provider health groups hosts into provider rows, and day trend fills hourly buckets | Added dense-layout HTML test; full suite passes; live screenshot captured at `logs/dashboard-polished-reference-2.png` |
| 23 | Done | Alert label readability | Health-banner alert chips use concise Chinese provider summaries instead of raw backend English messages, with detailed text kept in tooltips and the anomaly table | Dense-layout HTML test covers alert label helpers; full suite passes; live screenshot captured at `logs/dashboard-alert-labels.png` |
| 24 | Done | Raw enum display cleanup | Dashboard maps raw alert severities, alert kinds, and synthetic model labels into Chinese UI copy while keeping raw values in titles for diagnostics | Dense-layout HTML test covers enum/model label helpers; full suite passes; live screenshot captured at `logs/dashboard-alert-labels-2.png` |
| 25 | Done | Operational anomaly guidance | Recent anomaly table now separates level/type/object, replaces placeholder `-` with observed time or aggregate-alert source, and adds handling advice per alert/request | Dense-layout HTML test covers advice/time helpers; full suite passes; live screenshot captured at `logs/dashboard-anomaly-guidance.png` |
| 26 | Done | Anomaly card layout | Recent anomalies render as responsive alert cards instead of a narrow six-column table, preventing Chinese labels from wrapping vertically in compact panels | Dense-layout HTML test covers anomaly card hooks; full suite passes; live screenshot captured at `logs/dashboard-anomaly-cards.png` |
| 27 | Done | Backend alert noise gating | Slow-connect backend alerts now prioritize model API hosts, require higher volume for developer services, and suppress noisy content-site slow connects so system health focuses on Claude Code impact | Added regression covering GitHub/Douyin suppression and DeepSeek retention; full suite passes |

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
| Diagnostics panels | Runtime status, Host ranking, and recent requests are displayed in the local dashboard |
| Alert thresholds | Slow connect `>= 3000ms`; Host warning failure rate `>= 10%`; Host critical failure rate `>= 50%` |
| Layout persistence | Dashboard module order is stored in browser `localStorage`; no backend schema change |
| Dashboard information architecture | Top-level tabs are Overview, Providers, Requests, Usage & Cost, Whitelist, and Doctor |
| Dashboard visual style | Flat full-width operations console matching the approved reference image: no rounded outer shell, dense card grid, health banner, table-first diagnostics |

## Candidate Backlog

| Status | Feature | Notes |
| --- | --- | --- |
| Accepted | Provider health panel | Group Hosts by provider such as MiniMax, DeepSeek, and MiMo; show recent success rate, connect latency, route, latest error, and whether the provider looks healthy. |
| Accepted | Request detail drawer | Click a Host or recent request row to inspect started/completed time, route, connect latency, duration, error, alert state, and recent same-Host context. |
| Accepted | Whitelist hit analysis | Show Hosts that frequently use proxy routing and may be candidates for whitelist-direct routing, based on route mix, connect latency, duration, and failure behavior. |
| Accepted | Whitelist UI management | Add a dashboard tab for viewing, adding, removing, saving, and reloading whitelist entries without editing `whitelist.txt` manually. |
| Accepted | Doctor page | Add a dashboard tab that checks local service health, Python path, Claude transcript readability, whitelist status, system proxy state, and upstream proxy connectivity. |
| Accepted | Anomaly baseline | Compare each Host/provider against its own historical baseline so unusual latency or failure spikes can be detected before fixed thresholds are crossed. |

## Update Rule

When implementation begins, update exactly one row to `In Progress`. When that
task is verified, update it to `Done` before starting the next row.
