import asyncio
import json
from urllib.parse import parse_qs, urlparse


DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 8890


def build_stats_response(status, payload):
    return (
        status,
        {"Content-Type": "application/json; charset=utf-8"},
        json.dumps(payload).encode("utf-8"),
    )


def build_html_response(status, html):
    return (
        status,
        {"Content-Type": "text/html; charset=utf-8"},
        html.encode("utf-8"),
    )


def handle_stats_request(method, parsed_url, stats_store, status_provider=None):
    if method == "GET" and parsed_url.path in ("", "/"):
        return build_html_response(200, DASHBOARD_HTML)

    if method == "GET" and parsed_url.path == "/api/summary":
        params = parse_qs(parsed_url.query)
        range_name = (params.get("range") or ["day"])[0]
        return build_stats_response(200, stats_store.get_summary(range_name))

    if method == "GET" and parsed_url.path == "/api/trends":
        params = parse_qs(parsed_url.query)
        range_name = (params.get("range") or ["day"])[0]
        models = params.get("model") or []
        return build_stats_response(
            200,
            stats_store.get_trends(range_name, models=models),
        )

    if method == "GET" and parsed_url.path == "/api/recent-requests":
        params = parse_qs(parsed_url.query)
        raw_limit = (params.get("limit") or ["50"])[0]
        try:
            limit = int(raw_limit)
        except ValueError:
            limit = 50
        return build_stats_response(
            200,
            {"requests": stats_store.get_recent_proxy_requests(limit=limit)},
        )

    if method == "GET" and parsed_url.path == "/api/runtime-status":
        if status_provider is None:
            status = {
                "proxy_enabled": None,
                "upstream_proxy": "",
                "whitelist_count": 0,
                "whitelist_path": "",
                "whitelist_loaded_at": "",
            }
        else:
            status = status_provider()
        return build_stats_response(200, status)

    if method == "POST" and parsed_url.path == "/api/clear-proxy-stats":
        stats_store.clear_proxy_stats()
        return build_stats_response(200, {"ok": True})

    return build_stats_response(404, {"error": "not found"})


DASHBOARD_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Smart Proxy Stats</title>
  <style>
    :root {
      --ink: #121826;
      --muted: #66738a;
      --line: #d7e0ef;
      --panel: #ffffff;
      --wash: #f4f7fb;
      --blue: #2459e6;
      --green: #137f6d;
      --violet: #6d42df;
      --orange: #bd6418;
      --red: #d92d3a;
      --shadow: 0 16px 40px rgba(18, 24, 38, 0.08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background:
        linear-gradient(180deg, rgba(255,255,255,0.86), rgba(244,247,251,0.92)),
        repeating-linear-gradient(90deg, rgba(36,89,230,0.04) 0 1px, transparent 1px 84px);
      color: var(--ink);
      font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
    }
    main {
      width: min(1360px, calc(100vw - 48px));
      margin: 28px auto;
      border: 1px solid var(--line);
      border-radius: 28px;
      background: rgba(255,255,255,0.78);
      box-shadow: var(--shadow);
      padding: 28px;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 20px;
      margin-bottom: 26px;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 14px;
      font-weight: 800;
      font-size: 22px;
    }
    .pulse {
      width: 28px;
      height: 28px;
      color: var(--blue);
    }
    .controls {
      display: flex;
      align-items: center;
      gap: 14px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    .segment {
      display: inline-grid;
      grid-template-columns: repeat(4, 1fr);
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px;
      background: var(--wash);
    }
    .segment button, .danger, .layout-action {
      border: 0;
      font: inherit;
      font-weight: 800;
      cursor: pointer;
    }
    .segment button {
      min-width: 54px;
      padding: 10px 12px;
      border-radius: 999px;
      color: #29416d;
      background: transparent;
    }
    .segment button.active {
      background: #dbe9ff;
      color: var(--blue);
      box-shadow: inset 0 0 0 1px rgba(36,89,230,0.25);
    }
    .danger {
      padding: 13px 18px;
      border-radius: 14px;
      color: white;
      background: var(--red);
    }
    .layout-action {
      border: 1px solid var(--line);
      border-radius: 14px;
      color: #29416d;
      background: var(--wash);
      padding: 12px 14px;
    }
    .layout-action.active {
      background: #dbe9ff;
      border-color: rgba(36,89,230,0.35);
      color: var(--blue);
    }
    .layout-action[hidden] {
      display: none;
    }
    .layout-root {
      display: grid;
      gap: 18px;
    }
    .layout-widget,
    [data-widget] {
      position: relative;
    }
    .layout-root > .alerts-panel,
    .layout-root > .details,
    .layout-root > .trend-panel,
    .layout-root > .recent-panel {
      margin-top: 0;
      margin-bottom: 0;
    }
    .drag-handle {
      display: none;
      position: absolute;
      right: 14px;
      top: 12px;
      z-index: 3;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: white;
      color: #29416d;
      cursor: grab;
      font: inherit;
      font-size: 12px;
      font-weight: 900;
      padding: 6px 10px;
      box-shadow: 0 8px 18px rgba(18, 24, 38, 0.08);
    }
    main.layout-editing [data-widget] {
      outline: 2px dashed rgba(36,89,230,0.28);
      outline-offset: 5px;
    }
    main.layout-editing [data-widget].dragging {
      opacity: 0.55;
    }
    main.layout-editing .drag-handle {
      display: inline-flex;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(5, minmax(170px, 1fr));
      gap: 18px;
    }
    .alerts-panel {
      display: grid;
      grid-template-columns: auto minmax(0, 1fr) auto;
      gap: 14px;
      align-items: center;
      border: 1px solid #f1c4a4;
      border-left: 6px solid var(--orange);
      border-radius: 18px;
      background: #fff8ef;
      margin-bottom: 18px;
      padding: 14px 16px;
    }
    .alerts-panel.clean {
      border-color: #cceadd;
      border-left-color: var(--green);
      background: #f3fbf7;
    }
    .alert-title {
      color: var(--ink);
      font-size: 14px;
      font-weight: 900;
      white-space: nowrap;
    }
    .alert-list {
      min-width: 0;
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .alert-chip {
      min-width: 0;
      max-width: 100%;
      border-radius: 999px;
      background: white;
      color: #68411e;
      font-size: 12px;
      font-weight: 850;
      overflow: hidden;
      padding: 6px 10px;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .alert-chip.critical {
      background: #ffe4e8;
      color: var(--red);
    }
    .alert-chip.warning {
      background: #fff0d8;
      color: var(--orange);
    }
    .alert-count {
      color: var(--muted);
      font-size: 12px;
      font-weight: 900;
      white-space: nowrap;
    }
    .card {
      min-width: 0;
      min-height: 148px;
      border: 1px solid var(--line);
      border-top: 4px solid var(--accent);
      border-radius: 20px;
      background: var(--panel);
      padding: 22px 24px;
    }
    .label {
      color: var(--accent);
      font-weight: 800;
      font-size: 15px;
      margin-bottom: 12px;
    }
    .value {
      max-width: 100%;
      overflow: hidden;
      text-overflow: clip;
      white-space: nowrap;
      font-size: 42px;
      line-height: 1;
      font-weight: 900;
      letter-spacing: 0;
      margin-bottom: 12px;
    }
    .sub {
      color: var(--muted);
      font-size: 15px;
      font-weight: 650;
    }
    .details {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 18px;
      margin-top: 18px;
    }
    .trend-panel {
      margin-top: 18px;
      border: 1px solid var(--line);
      border-radius: 18px;
      background: var(--panel);
      padding: 18px;
    }
    .trend-head {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: center;
      margin-bottom: 12px;
    }
    .trend-head h2 {
      margin: 0;
      font-size: 16px;
    }
    .legend {
      display: flex;
      gap: 14px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 800;
    }
    .model-filter {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 0 0 14px;
    }
    .model-filter button {
      border: 1px solid var(--line);
      border-radius: 999px;
      background: var(--wash);
      color: #29416d;
      cursor: pointer;
      font: inherit;
      font-size: 12px;
      font-weight: 800;
      max-width: 260px;
      overflow: hidden;
      padding: 8px 10px;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .model-filter button.active {
      background: #dbe9ff;
      border-color: rgba(36,89,230,0.35);
      color: var(--blue);
    }
    .legend span::before {
      content: "";
      display: inline-block;
      width: 10px;
      height: 10px;
      border-radius: 999px;
      margin-right: 6px;
      background: var(--dot);
    }
    .chart {
      width: 100%;
      height: 220px;
      display: block;
      border-top: 1px solid #edf1f7;
    }
    .empty-chart {
      color: var(--muted);
      font-weight: 700;
      padding: 42px 0;
    }
    .table {
      border: 1px solid var(--line);
      border-radius: 18px;
      background: var(--panel);
      padding: 18px;
    }
    .table h2 {
      margin: 0 0 14px;
      font-size: 16px;
    }
    .row {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      padding: 9px 0;
      border-top: 1px solid #edf1f7;
      color: var(--muted);
      font-weight: 650;
    }
    .row strong { color: var(--ink); }
    .model-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 14px;
      padding: 12px 0;
      border-top: 1px solid #edf1f7;
      align-items: start;
    }
    .model-name {
      min-width: 0;
      overflow-wrap: anywhere;
      color: var(--muted);
      font-weight: 800;
    }
    .model-total {
      color: var(--ink);
      font-weight: 900;
      white-space: nowrap;
    }
    .model-metrics {
      grid-column: 1 / -1;
      display: grid;
      grid-template-columns: repeat(4, minmax(96px, 1fr));
      gap: 8px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
    }
    .metric {
      min-width: 0;
      border-radius: 10px;
      background: var(--wash);
      padding: 8px 10px;
      white-space: nowrap;
    }
    .metric strong {
      color: var(--ink);
      font-weight: 900;
    }
    .diagnostics {
      grid-template-columns: minmax(280px, 0.8fr) minmax(360px, 1.2fr);
    }
    .recent-panel {
      margin-top: 18px;
    }
    .runtime-list {
      display: grid;
      gap: 10px;
    }
    .runtime-item {
      display: grid;
      grid-template-columns: minmax(96px, 0.8fr) minmax(0, 1.2fr);
      gap: 12px;
      align-items: center;
      border-top: 1px solid #edf1f7;
      padding: 10px 0;
      color: var(--muted);
      font-weight: 700;
    }
    .runtime-item strong,
    .host-main strong,
    .request-main strong {
      min-width: 0;
      color: var(--ink);
      overflow-wrap: anywhere;
    }
    .host-row,
    .request-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      align-items: start;
      border-top: 1px solid #edf1f7;
      padding: 12px 0;
    }
    .host-row.warning,
    .request-row.slow-request {
      border-left: 4px solid var(--orange);
      padding-left: 10px;
    }
    .host-row.critical,
    .request-row.failed-request {
      border-left: 4px solid var(--red);
      padding-left: 10px;
    }
    .host-main,
    .request-main {
      min-width: 0;
      display: grid;
      gap: 6px;
      color: var(--muted);
      font-weight: 700;
    }
    .host-meta,
    .request-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      width: max-content;
      max-width: 100%;
      border-radius: 999px;
      background: var(--wash);
      color: #29416d;
      padding: 5px 9px;
      font-size: 12px;
      font-weight: 900;
      white-space: nowrap;
    }
    .pill.good {
      background: #dff7ee;
      color: var(--green);
    }
    .pill.bad {
      background: #ffe4e8;
      color: var(--red);
    }
    .request-time {
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
      white-space: nowrap;
    }
    .status {
      margin-top: 18px;
      color: var(--muted);
      font-size: 13px;
    }
    .shell-status {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: flex-end;
    }
    .status-chip {
      border: 1px solid var(--line);
      border-radius: 999px;
      background: var(--wash);
      color: #29416d;
      font-size: 12px;
      font-weight: 900;
      padding: 7px 10px;
      white-space: nowrap;
    }
    .status-chip.good {
      background: #dff7ee;
      border-color: #bfe9d8;
      color: var(--green);
    }
    .status-chip.warning {
      background: #fff0d8;
      border-color: #ffd39a;
      color: var(--orange);
    }
    .tab-bar {
      display: flex;
      gap: 8px;
      margin: 0 0 18px;
      overflow-x: auto;
      padding-bottom: 4px;
    }
    .tab-button {
      border: 1px solid var(--line);
      border-radius: 999px;
      background: var(--wash);
      color: #29416d;
      cursor: pointer;
      flex: 0 0 auto;
      font: inherit;
      font-size: 13px;
      font-weight: 900;
      padding: 10px 14px;
      white-space: nowrap;
    }
    .tab-button.active {
      background: #dbe9ff;
      border-color: rgba(36,89,230,0.35);
      color: var(--blue);
    }
    .tab-panel {
      display: none;
    }
    .tab-panel.active {
      display: block;
    }
    .overview-split {
      display: grid;
      grid-template-columns: minmax(0, 1.4fr) minmax(340px, 0.6fr);
      gap: 18px;
    }
    .panel-note {
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
      line-height: 1.7;
      margin: 0;
    }
    .placeholder-list {
      display: grid;
      gap: 10px;
      margin-top: 12px;
    }
    .placeholder-list span {
      border-top: 1px solid #edf1f7;
      color: var(--muted);
      font-weight: 750;
      padding-top: 10px;
    }
    .topbar {
      height: 64px;
      margin: 0;
      padding: 0 28px;
      border-bottom: 1px solid #e7ebf2;
      background: rgba(255,255,255,0.96);
    }
    .brand-mark {
      display: grid;
      place-items: center;
      width: 34px;
      height: 34px;
      border: 2px solid #8a92a3;
      border-radius: 10px;
      color: #323946;
      font-size: 18px;
      font-weight: 950;
      line-height: 1;
    }
    .topbar .brand {
      font-size: 20px;
      gap: 12px;
    }
    .topbar .shell-status {
      flex: 1;
      justify-content: flex-start;
      margin-left: 28px;
    }
    .top-actions {
      display: flex;
      align-items: center;
      gap: 16px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 750;
      white-space: nowrap;
    }
    .refresh-dot {
      color: #70798a;
      font-size: 18px;
      line-height: 1;
    }
    .auto-select {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: white;
      color: #344154;
      font: inherit;
      font-weight: 800;
      padding: 9px 12px;
    }
    .theme-toggle {
      border: 0;
      background: transparent;
      color: #344154;
      cursor: pointer;
      font-size: 24px;
      line-height: 1;
      padding: 4px;
    }
    .tab-nav {
      display: flex;
      height: 48px;
      gap: 22px;
      align-items: stretch;
      border-bottom: 1px solid #e7ebf2;
      background: rgba(255,255,255,0.96);
      padding: 0 28px;
    }
    .tab-nav .tab-button {
      border: 0;
      border-radius: 0;
      background: transparent;
      color: #5d6472;
      padding: 0 14px;
      position: relative;
      font-size: 14px;
    }
    .tab-nav .tab-button.active {
      background: transparent;
      box-shadow: none;
      color: var(--blue);
    }
    .tab-nav .tab-button.active::after {
      content: "";
      position: absolute;
      left: 0;
      right: 0;
      bottom: 0;
      height: 3px;
      border-radius: 999px 999px 0 0;
      background: var(--blue);
    }
    body {
      background: #f7f8fb;
    }
    main {
      width: 100%;
      min-height: 100vh;
      margin: 0;
      border: 0;
      border-radius: 0;
      background: #f7f8fb;
      box-shadow: none;
      padding: 0;
    }
    .console-content {
      padding: 18px 28px 30px;
    }
    .health-banner {
      display: grid;
      grid-template-columns: minmax(280px, 1fr) auto minmax(280px, 0.8fr);
      align-items: center;
      gap: 18px;
      min-height: 52px;
      border: 1px solid #cbe9d6;
      border-radius: 7px;
      background: linear-gradient(90deg, #f3fbf6, #f8fdf9);
      padding: 0 18px;
      margin-bottom: 16px;
    }
    .health-banner-main {
      display: flex;
      align-items: center;
      gap: 12px;
      min-width: 0;
    }
    .health-dot {
      display: grid;
      place-items: center;
      width: 22px;
      height: 22px;
      border-radius: 999px;
      background: #2ca95f;
      color: white;
      font-weight: 950;
      line-height: 1;
    }
    .health-title {
      color: #279356;
      font-size: 16px;
      font-weight: 950;
    }
    .health-subtitle {
      color: var(--muted);
      font-size: 13px;
      font-weight: 750;
    }
    .health-link {
      border: 0;
      background: transparent;
      color: #2a9b5b;
      cursor: pointer;
      font: inherit;
      font-weight: 900;
      white-space: nowrap;
    }
    .health-banner .alert-list {
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      min-width: 0;
      overflow: hidden;
    }
    .health-banner .alert-chip {
      max-width: 280px;
      background: #fff1dc;
      color: #c46206;
      font-size: 12px;
      line-height: 1.2;
      padding: 7px 12px;
    }
    .health-banner .alert-chip.alert-overflow {
      background: #eef4ff;
      color: var(--blue);
    }
    .grid {
      grid-template-columns: repeat(5, minmax(180px, 1fr));
      gap: 14px;
    }
    .metric-card {
      display: grid;
      grid-template-columns: 50px minmax(0, 1fr);
      column-gap: 16px;
      align-items: center;
      min-height: 118px;
      border: 1px solid #e3e8f0;
      border-top: 1px solid #e3e8f0;
      border-radius: 7px;
      background: white;
      padding: 20px;
    }
    .metric-icon {
      display: grid;
      place-items: center;
      width: 48px;
      height: 48px;
      border-radius: 16px;
      background: var(--accent);
      color: white;
      font-size: 24px;
      font-weight: 950;
    }
    .metric-card .label {
      color: #2e3544;
      font-size: 13px;
      margin-bottom: 6px;
    }
    .metric-card .value {
      font-size: 27px;
      line-height: 1.05;
      margin-bottom: 7px;
      overflow: visible;
      white-space: nowrap;
    }
    .metric-card .sub {
      font-size: 12px;
      line-height: 1.45;
      overflow-wrap: anywhere;
    }
    .metric-card .metric-change {
      color: #2ca95f;
      font-weight: 900;
      margin-left: 8px;
    }
    .metric-card.cost .metric-change {
      color: var(--red);
    }
    .overview-main {
      display: grid;
      grid-template-columns: minmax(0, 1.1fr) minmax(430px, 0.9fr);
      gap: 14px;
      margin-top: 16px;
    }
    .trend-panel,
    .table,
    .recent-panel {
      border-radius: 7px;
      border-color: #e3e8f0;
      background: white;
      box-shadow: none;
    }
    .trend-panel {
      margin-top: 0;
      min-height: 334px;
    }
    .trend-head {
      margin-bottom: 8px;
    }
    .trend-actions {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 10px;
      justify-content: flex-end;
    }
    .trend-head p {
      margin: 4px 0 0;
      color: var(--muted);
      font-size: 12px;
      font-weight: 750;
    }
    .time-window {
      border: 1px solid #dfe5ee;
      border-radius: 7px;
      background: white;
      color: #344154;
      font: inherit;
      font-size: 12px;
      font-weight: 850;
      padding: 8px 10px;
    }
    .provider-table,
    .data-table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    .provider-table th,
    .provider-table td,
    .data-table th,
    .data-table td {
      border-top: 1px solid #edf1f7;
      padding: 11px 10px;
      text-align: left;
      vertical-align: middle;
    }
    .provider-table th,
    .data-table th {
      color: #697386;
      font-weight: 900;
    }
    .status-badge {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 950;
      padding: 4px 9px;
      white-space: nowrap;
    }
    .status-badge.good {
      background: #def7ed;
      color: var(--green);
    }
    .status-badge.warn {
      background: #fff0d8;
      color: var(--orange);
    }
    .status-badge.bad {
      background: #ffe4e8;
      color: var(--red);
    }
    .table-empty {
      border-top: 1px solid #edf1f7;
      color: var(--muted);
      font-size: 13px;
      font-weight: 800;
      padding: 18px 0 4px;
    }
    .provider-name {
      display: flex;
      align-items: center;
      gap: 10px;
      font-weight: 950;
    }
    .provider-title {
      display: grid;
      gap: 2px;
    }
    .provider-subtitle {
      color: var(--muted);
      font-size: 12px;
      font-weight: 750;
    }
    .provider-logo {
      display: grid;
      place-items: center;
      width: 26px;
      height: 26px;
      border-radius: 8px;
      color: white;
      font-size: 12px;
      font-weight: 950;
    }
    .sparkline {
      width: 74px;
      height: 18px;
      vertical-align: middle;
    }
    .bottom-grid {
      display: grid;
      grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.1fr);
      gap: 14px;
      margin-top: 14px;
    }
    .table-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 8px;
    }
    .table-head h2,
    .trend-head h2 {
      margin: 0;
      font-size: 16px;
      font-weight: 950;
    }
    .table-link {
      color: var(--blue);
      font-size: 13px;
      font-weight: 900;
      text-decoration: none;
    }
    .table-actions {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    body.dark-mode {
      --ink: #e7ecf6;
      --muted: #aab4c5;
      --line: #273244;
      --panel: #111827;
      --wash: #1b2535;
      background: #0b1019;
    }
    body.dark-mode main {
      background: #0b1019;
    }
    body.dark-mode .topbar,
    body.dark-mode .tab-nav,
    body.dark-mode .trend-panel,
    body.dark-mode .table,
    body.dark-mode .metric-card {
      background: #111827;
    }
    body.dark-mode .auto-select,
    body.dark-mode .time-window {
      background: #101722;
      color: var(--ink);
    }
    @media (max-width: 1100px) {
      .overview-main,
      .bottom-grid {
        grid-template-columns: 1fr;
      }
      .health-banner {
        grid-template-columns: 1fr;
        align-items: flex-start;
        padding: 14px 18px;
      }
      .health-banner .alert-list {
        justify-content: flex-start;
      }
      .topbar {
        height: auto;
        align-items: flex-start;
        padding: 14px 18px;
        flex-wrap: wrap;
      }
      .topbar .shell-status {
        margin-left: 0;
      }
    }
    @media (max-width: 920px) {
      main { width: 100%; padding: 0; border-radius: 0; }
      header { align-items: flex-start; flex-direction: column; }
      .grid, .details, .overview-split { grid-template-columns: 1fr; }
      .model-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .trend-head { align-items: flex-start; flex-direction: column; }
      .controls { width: 100%; justify-content: space-between; }
      .shell-status { justify-content: flex-start; }
      .alerts-panel { grid-template-columns: 1fr; }
      .runtime-item, .host-row, .request-row { grid-template-columns: 1fr; }
      .request-time { white-space: normal; }
    }
  </style>
</head>
<body>
  <main>
    <header class="topbar">
      <div class="brand">
        <span class="brand-mark" aria-hidden="true">⬢</span>
        <span>Smart Proxy Console</span>
      </div>
      <div class="shell-status" aria-label="service status">
        <span class="status-chip good" id="proxyChip">Proxy 127.0.0.1:8889</span>
        <span class="status-chip good" id="dashboardChip">Dashboard 127.0.0.1:8890</span>
        <span class="status-chip" id="upstreamChip" hidden>Upstream detecting</span>
      </div>
      <div class="top-actions">
        <span class="refresh-dot" aria-hidden="true">⟳</span>
        <span id="lastRefreshAt">刚刚刷新</span>
        <select class="auto-select" id="autoRefresh" aria-label="自动刷新">
          <option value="5000">5 秒</option>
          <option value="10000">10 秒</option>
          <option value="30000">30 秒</option>
          <option value="0">关闭</option>
        </select>
        <button class="theme-toggle" id="themeToggle" type="button" aria-label="切换主题">◐</button>
      </div>
    </header>

    <nav class="tab-nav" aria-label="dashboard sections">
      <button class="tab-button active" data-tab-target="overview">总览</button>
      <button class="tab-button" data-tab-target="providers">Providers</button>
      <button class="tab-button" data-tab-target="requests">Requests</button>
      <button class="tab-button" data-tab-target="usage">Usage & Cost</button>
      <button class="tab-button" data-tab-target="whitelist">Whitelist</button>
      <button class="tab-button" data-tab-target="doctor">Doctor</button>
    </nav>

    <div class="console-content">
    <section class="tab-panel active" data-tab-panel="overview">
      <div class="layout-root" id="layoutRoot">
        <section class="health-banner" id="alertsPanel" data-widget="alerts" aria-live="polite">
          <button class="drag-handle" type="button" aria-label="拖动系统健康">拖动</button>
          <div class="health-banner-main">
            <span class="health-dot" aria-hidden="true">✓</span>
            <div>
              <div class="health-title" id="systemHealthText">系统运行正常</div>
              <div class="health-subtitle" id="systemHealthSub">代理、Dashboard 与上游连接均处于可用状态。</div>
            </div>
          </div>
          <button class="health-link" type="button" data-tab-target="doctor">查看详情</button>
          <div class="alert-list" id="alertsList" hidden></div>
          <div class="alert-count" id="alertCount" hidden>0 条</div>
        </section>

        <section class="grid layout-widget" data-widget="kpis">
          <button class="drag-handle" type="button" aria-label="拖动指标卡片">拖动</button>
          <article class="card metric-card" style="--accent: var(--blue)">
            <div class="metric-icon" aria-hidden="true">↗</div>
            <div>
              <div class="label">总请求数</div>
              <div class="value" id="totalRequests">0</div>
              <div class="sub" id="requestSub">较昨日 0% · 成功 0 / 失败 0</div>
            </div>
          </article>
          <article class="card metric-card" style="--accent: var(--green)">
            <div class="metric-icon" aria-hidden="true">%</div>
            <div>
              <div class="label">成功率</div>
              <div class="value" id="successRateKpi">0%</div>
              <div class="sub" id="successRate">较昨日 0% · 持续 0ms</div>
            </div>
          </article>
          <article class="card metric-card" style="--accent: var(--orange)">
            <div class="metric-icon" aria-hidden="true">⏱</div>
            <div>
              <div class="label">平均建连(P50)</div>
              <div class="value" id="avgLatency">0ms</div>
              <div class="sub" id="latencySub">较昨日 0% · 慢建连 0</div>
            </div>
          </article>
          <article class="card metric-card" style="--accent: var(--red)">
            <div class="metric-icon" aria-hidden="true">¥</div>
            <div>
              <div class="label">今日费用</div>
              <div class="value" id="estimatedCost">¥0</div>
              <div class="sub" id="costSub">较昨日 0% · API 0 / 套餐 0</div>
            </div>
          </article>
          <article class="card metric-card" style="--accent: var(--violet)">
            <div class="metric-icon" aria-hidden="true">T</div>
            <div>
              <div class="label">今日 Token</div>
              <div class="value" id="totalTokens">0</div>
              <div class="sub" id="tokenSub">较昨日 0% · 输入 0 / 输出 0</div>
            </div>
            <div id="cacheTokens" hidden>0</div>
            <div id="cacheSub" hidden>读 0 / 写 0</div>
          </article>
        </section>

        <section class="overview-main" data-widget="trend">
          <button class="drag-handle" type="button" aria-label="拖动趋势图">拖动</button>
          <div class="trend-panel">
            <div class="trend-head">
              <div>
                <h2>请求趋势</h2>
                <p>按时间查看请求量、Token 与费用变化</p>
              </div>
              <div class="trend-actions">
                <select class="time-window" id="timeWindow" aria-label="趋势时间窗口">
                  <option value="day">今日</option>
                  <option value="week">近 7 天</option>
                  <option value="month">近 30 天</option>
                  <option value="all">全部</option>
                </select>
                <div class="segment" role="group" aria-label="range">
                  <button data-range="day" class="active">日</button>
                  <button data-range="week">周</button>
                  <button data-range="month">月</button>
                  <button data-range="all">全</button>
                </div>
                <div class="legend">
                  <span style="--dot: var(--green)">Token</span>
                  <span style="--dot: var(--red)">费用</span>
                </div>
              </div>
            </div>
            <div class="model-filter" id="modelFilter"></div>
            <div id="trendChart" class="empty-chart">暂无趋势数据</div>
          </div>
          <section class="table provider-health-panel">
            <div class="table-head">
              <h2>Provider 健康状态</h2>
              <button class="layout-action" id="layoutToggle" aria-pressed="false">编辑布局</button>
            </div>
            <div id="providerHealth"></div>
            <div id="providerSummary" hidden></div>
          </section>
        </section>

        <section class="bottom-grid layout-widget" data-widget="bottom">
          <div class="table">
            <div class="table-head">
              <h2>最近异常</h2>
              <a class="table-link" href="#" data-tab-target="requests">全部请求</a>
            </div>
            <div id="recentAnomaliesTable"></div>
            <div id="recentAnomalies" hidden></div>
          </div>
          <div class="table">
            <div class="table-head">
              <h2>最近请求</h2>
              <div class="table-actions">
                <button class="layout-action" id="resetLayout" hidden>恢复默认</button>
                <button class="danger" id="clearProxy">清除统计</button>
              </div>
            </div>
            <div id="recentRequestsTable"></div>
          </div>
        </section>
      </div>
    </section>

    <section class="tab-panel" data-tab-panel="providers">
      <section class="details diagnostics">
        <div class="table">
          <h2>代理状态</h2>
          <div class="runtime-list" id="runtimeStatus"></div>
        </div>
        <div class="table">
          <h2>Host 统计</h2>
          <div id="hosts"></div>
        </div>
      </section>
    </section>

    <section class="tab-panel" data-tab-panel="requests">
      <section class="table recent-panel">
        <h2>最近请求</h2>
        <div id="recentRequests"></div>
      </section>
    </section>

    <section class="tab-panel" data-tab-panel="usage">
      <section class="details">
        <div class="table">
          <h2>路由拆分</h2>
          <div id="routes"></div>
        </div>
        <div class="table">
          <h2>模型拆分</h2>
          <div id="models"></div>
        </div>
      </section>
    </section>

    <section class="tab-panel" data-tab-panel="whitelist">
      <section class="table">
        <h2>Whitelist</h2>
        <p class="panel-note">白名单命中分析和白名单 UI 管理会放在这里。当前版本先预留独立页签，避免继续挤压总览页。</p>
        <div class="placeholder-list">
          <span>候选 Host 分析</span>
          <span>当前 whitelist.txt 条目</span>
          <span>添加 / 删除 / 保存 / reload</span>
        </div>
      </section>
    </section>

    <section class="tab-panel" data-tab-panel="doctor">
      <section class="table">
        <h2>Doctor</h2>
        <p class="panel-note">启动自检会放在这里：本地端口、Python 路径、Claude transcript、白名单、系统代理和上游代理连通性。</p>
        <div class="placeholder-list">
          <span>Proxy 端口与 Dashboard 端口</span>
          <span>Claude transcript 可读性</span>
          <span>系统代理与上游代理连通性</span>
        </div>
      </section>
    </section>

    <div class="status" id="status">等待刷新</div>
    </div>
  </main>
  <script>
    let currentRange = 'day';
    const selectedModels = new Set();
    let layoutEditing = false;
    let draggedWidget = null;
    let refreshTimer = null;
    const layoutStorageKey = 'smartProxyOverviewDashboardLayout.v1';
    const defaultLayout = ['alerts', 'kpis', 'trend', 'bottom'];
    const layoutRoot = document.getElementById('layoutRoot');
    const layoutToggle = document.getElementById('layoutToggle');
    const resetLayout = document.getElementById('resetLayout');
    const tabButtons = [...document.querySelectorAll('.tab-nav [data-tab-target]')];
    const tabLinks = [...document.querySelectorAll('[data-tab-target]')];
    const tabPanels = [...document.querySelectorAll('[data-tab-panel]')];
    const fmt = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 });
    const unitFmt = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 });
    const moneyFmt = new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    const text = (id, value) => { document.getElementById(id).textContent = value; };
    const switchTab = target => {
      tabButtons.forEach(button => {
        const active = button.dataset.tabTarget === target;
        button.classList.toggle('active', active);
        button.setAttribute('aria-selected', active ? 'true' : 'false');
      });
      tabPanels.forEach(panel => {
        panel.classList.toggle('active', panel.dataset.tabPanel === target);
      });
    };
    const layoutWidgets = () => [...layoutRoot.querySelectorAll('[data-widget]')];
    const normalizeLayout = order => {
      const known = new Set(defaultLayout);
      const clean = (Array.isArray(order) ? order : []).filter(id => known.has(id));
      return [...new Set([...clean, ...defaultLayout])];
    };
    const applyLayout = order => {
      normalizeLayout(order).forEach(widgetId => {
        const widget = layoutRoot.querySelector(`[data-widget="${widgetId}"]`);
        if (widget) layoutRoot.appendChild(widget);
      });
    };
    const loadLayout = () => {
      try {
        applyLayout(JSON.parse(localStorage.getItem(layoutStorageKey) || '[]'));
      } catch (_error) {
        applyLayout(defaultLayout);
      }
    };
    const saveLayout = () => {
      localStorage.setItem(layoutStorageKey, JSON.stringify(
        layoutWidgets().map(widget => widget.dataset.widget)
      ));
    };
    const restoreDefaultLayout = () => {
      localStorage.removeItem(layoutStorageKey);
      applyLayout(defaultLayout);
    };
    const setLayoutEditing = enabled => {
      layoutEditing = enabled;
      document.querySelector('main').classList.toggle('layout-editing', enabled);
      layoutToggle.classList.toggle('active', enabled);
      layoutToggle.setAttribute('aria-pressed', enabled ? 'true' : 'false');
      layoutToggle.textContent = enabled ? '完成布局' : '编辑布局';
      resetLayout.hidden = !enabled;
      layoutWidgets().forEach(widget => {
        widget.draggable = enabled;
      });
    };
    const widgetAfterPointer = y => {
      const widgets = layoutWidgets().filter(widget => widget !== draggedWidget);
      return widgets.reduce((closest, widget) => {
        const box = widget.getBoundingClientRect();
        const offset = y - box.top - box.height / 2;
        if (offset < 0 && offset > closest.offset) {
          return { offset, widget };
        }
        return closest;
      }, { offset: Number.NEGATIVE_INFINITY, widget: null }).widget;
    };
    const setMetric = (id, value, title) => {
      const element = document.getElementById(id);
      element.textContent = value;
      element.title = title || value;
      element.style.fontSize = fitMetricValue(value);
    };
    const fitMetricValue = value => {
      const textValue = String(value);
      if (textValue.length >= 10) return '24px';
      if (textValue.length >= 8) return '26px';
      if (textValue.length >= 7) return '28px';
      return '';
    };
    const percent = value => `${Math.round(value * 100)}%`;
    const compactNumber = value => {
      const abs = Math.abs(value);
      if (abs >= 100000000) {
        return `${unitFmt.format(value / 100000000)}亿`;
      }
      if (abs >= 10000) {
        return `${unitFmt.format(value / 10000)}万`;
      }
      return fmt.format(value);
    };
    const money = value => `¥${moneyFmt.format(value)}`;
    const escapeHtml = value => String(value)
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
    const rows = entries => entries.length
      ? entries.map(([k, v]) => `<div class="row"><span>${escapeHtml(k)}</span><strong>${fmt.format(v)}</strong></div>`).join('')
      : '<div class="row"><span>暂无数据</span><strong>0</strong></div>';
    const costLabel = cost => {
      if (cost.billing_type === 'token_plan') return '套餐内';
      if (cost.billing_type === 'unknown') return '未计价';
      return money(cost.total);
    };
    const modelDisplayName = model => {
      if (model === '<synthetic>') return '未识别模型';
      return model || '未知模型';
    };
    const modelRows = models => {
      const entries = Object.entries(models)
        .sort((a, b) => b[1].total_tokens - a[1].total_tokens);
      if (!entries.length) {
        return '<div class="row"><span>暂无数据</span><strong>0</strong></div>';
      }
      return entries.map(([model, usage]) => `
        <div class="model-row">
          <span class="model-name" title="${escapeHtml(model)}">${escapeHtml(modelDisplayName(model))}</span>
          <strong class="model-total">${fmt.format(usage.total_tokens)} · ${costLabel(usage.cost)}</strong>
          <div class="model-metrics">
            <span class="metric">输入 <strong>${fmt.format(usage.input_tokens)}</strong></span>
            <span class="metric">输出 <strong>${fmt.format(usage.output_tokens)}</strong></span>
            <span class="metric">缓存读 <strong>${fmt.format(usage.cache_read_input_tokens)}</strong></span>
            <span class="metric">缓存写 <strong>${fmt.format(usage.cache_creation_input_tokens)}</strong></span>
          </div>
        </div>
      `).join('');
    };
    const routeText = route => ({
      proxy: '系统代理',
      direct: '直连',
      direct_whitelist: '白名单直连'
    })[route] || route;
    const severityLabel = severity => ({
      critical: '严重',
      warning: '提醒',
      info: '信息'
    })[severity] || severity || '提醒';
    const alertKindLabel = kind => ({
      slow_requests: '慢建连',
      host_failures: '失败率异常'
    })[kind] || kind || '告警';
    const providerLabelForHost = host => {
      const value = String(host || '').toLowerCase();
      if (value.includes('minimax')) return 'MiniMax';
      if (value.includes('deepseek')) return 'DeepSeek';
      if (value.includes('xiaomimimo') || value.includes('mimo')) return 'MiMo';
      if (value.includes('github')) return 'GitHub';
      if (value.includes('anthropic')) return 'Anthropic';
      if (value.includes('douyin')) return 'Douyin';
      return host || '未知 Host';
    };
    const alertSummaryLabel = alert => {
      const label = providerLabelForHost(alert.host);
      if (alert.kind === 'slow_requests') {
        return `${label} 慢建连 ${fmt.format(alert.value || 0)} 次`;
      }
      if (alert.kind === 'host_failures') {
        return `${label} 失败率 ${percent(alert.value || 0)}`;
      }
      return `${label} ${alert.kind || '异常'}`;
    };
    const alertDetailText = alert => {
      if (alert.kind === 'slow_requests') {
        return `${alert.host || '未知 Host'} 有 ${fmt.format(alert.value || 0)} 次建连超过 ${fmt.format(3000)}ms`;
      }
      if (alert.kind === 'host_failures') {
        return `${alert.host || '未知 Host'} 失败率 ${percent(alert.value || 0)}`;
      }
      return alert.message || alert.kind || '异常';
    };
    const alertRows = alerts => {
      if (!alerts.length) {
        return '<span class="alert-chip">当前范围内暂无异常</span>';
      }
      const visibleAlerts = alerts.slice(0, 2).map(alert => `
        <span class="alert-chip ${escapeHtml(alert.severity || 'warning')}" title="${escapeHtml(alertDetailText(alert))}">
          ${escapeHtml(alertSummaryLabel(alert))}
        </span>
      `);
      if (alerts.length > 2) {
        visibleAlerts.push(alertOverflowChip(alerts.length - 2));
      }
      return visibleAlerts.join('');
    };
    const alertOverflowChip = count => `
      <span class="alert-chip alert-overflow" title="还有 ${fmt.format(count)} 条异常">+${fmt.format(count)}</span>
    `;
    const renderAlerts = proxy => {
      const alerts = proxy.alerts || [];
      const panel = document.getElementById('alertsPanel');
      panel.classList.toggle('clean', alerts.length === 0);
      document.getElementById('alertsList').innerHTML = alertRows(alerts);
      const critical = alerts.some(alert => alert.severity === 'critical');
      const warning = alerts.length > 0 && !critical;
      const healthText = critical
        ? '系统存在严重异常'
        : warning
          ? '系统需要关注'
          : '系统运行正常';
      const healthSub = alerts.length
        ? alerts.slice(0, 2).map(alert => alertSummaryLabel(alert)).join(' / ')
        : '代理、Dashboard 与上游连接均处于可用状态。';
      text('systemHealthText', healthText);
      text('systemHealthSub', healthSub);
      const counts = proxy.alert_counts || { critical: 0, warning: 0 };
      document.getElementById('alertCount').textContent =
        `${alerts.length} 条 · 严重 ${counts.critical || 0} / 提醒 ${counts.warning || 0}`;
    };
    const hostRows = hosts => {
      if (!hosts.length) {
        return '<div class="row"><span>暂无数据</span><strong>0</strong></div>';
      }
      return hosts.map(host => {
        const routeInfo = Object.entries(host.routes || {})
          .map(([route, count]) => `${routeText(route)} ${fmt.format(count)}`)
          .join(' / ');
        const healthClass = host.health === 'critical'
          ? 'critical'
          : host.health === 'warning'
            ? 'warning'
            : '';
        return `
          <div class="host-row ${healthClass}">
            <div class="host-main">
              <strong>${escapeHtml(host.host || '-')}</strong>
              <span>${escapeHtml(routeInfo || '无路由记录')} · 失败率 ${percent(host.failure_rate || 0)}</span>
            </div>
            <div class="host-meta">
              <span class="pill">${fmt.format(host.total_requests)} 次</span>
              <span class="pill good">成功 ${fmt.format(host.successful_requests)}</span>
              <span class="pill bad">失败 ${fmt.format(host.failed_requests)}</span>
              <span class="pill">慢建连 ${fmt.format(host.slow_requests || 0)}</span>
              <span class="pill">建连 ${fmt.format(host.average_connect_latency_ms || host.average_latency_ms || 0)}ms</span>
              <span class="pill">持续 ${fmt.format(host.average_duration_ms || 0)}ms</span>
            </div>
          </div>
        `;
      }).join('');
    };
    const providerSummaryRows = hosts => {
      const topHosts = (hosts || []).slice(0, 5);
      if (!topHosts.length) {
        return '<div class="row"><span>暂无 Provider 数据</span><strong>0</strong></div>';
      }
      return topHosts.map(host => {
        const health = host.health === 'critical'
          ? '异常'
          : host.health === 'warning'
            ? '观察'
            : '正常';
        const pillClass = host.health === 'ok' ? 'good' : 'bad';
        return `
          <div class="host-row ${host.health === 'ok' ? '' : host.health}">
            <div class="host-main">
              <strong>${escapeHtml(host.host || '-')}</strong>
              <span>成功 ${fmt.format(host.successful_requests || 0)} / 失败 ${fmt.format(host.failed_requests || 0)}</span>
            </div>
            <div class="host-meta">
              <span class="pill ${pillClass}">${health}</span>
              <span class="pill">建连 ${fmt.format(host.average_connect_latency_ms || host.average_latency_ms || 0)}ms</span>
            </div>
          </div>
        `;
      }).join('');
    };
    const providerMeta = host => {
      const value = String(host || '').toLowerCase();
      if (value.includes('minimax')) return { key: 'minimax', name: 'MiniMax', logo: 'M', color: '#ff5b7f' };
      if (value.includes('deepseek')) return { key: 'deepseek', name: 'DeepSeek', logo: 'D', color: '#536dff' };
      if (value.includes('xiaomimimo') || value.includes('mimo')) return { key: 'mimo', name: 'MiMo', logo: 'Mi', color: '#111827' };
      if (value.includes('github')) return { key: 'github', name: 'GitHub', logo: 'G', color: '#24292f' };
      if (value.includes('anthropic')) return { key: 'anthropic', name: 'Anthropic', logo: 'A', color: '#8a5a44' };
      if (value.includes('douyin')) return { key: 'douyin', name: 'Douyin', logo: 'Dy', color: '#ff7a1a' };
      const name = (host || 'Other').split('.').slice(-2, -1)[0] || 'Other';
      return { key: value || 'other', name, logo: name.slice(0, 2).toUpperCase(), color: '#64748b' };
    };
    const providerGroups = hosts => {
      const groups = new Map();
      (hosts || []).forEach(host => {
        const meta = providerMeta(host.host);
        if (!groups.has(meta.key)) {
          groups.set(meta.key, {
            ...meta,
            hosts: [],
            total_requests: 0,
            successful_requests: 0,
            failed_requests: 0,
            slow_requests: 0,
            latency_sum: 0,
          });
        }
        const group = groups.get(meta.key);
        const requestCount = host.total_requests || 0;
        group.hosts.push(host.host || '-');
        group.total_requests += requestCount;
        group.successful_requests += host.successful_requests || 0;
        group.failed_requests += host.failed_requests || 0;
        group.slow_requests += host.slow_requests || 0;
        group.latency_sum += (host.average_connect_latency_ms || host.average_latency_ms || 0) * requestCount;
      });
      return [...groups.values()]
        .map(group => {
          group.success_rate = group.total_requests ? group.successful_requests / group.total_requests : 0;
          group.failure_rate = group.total_requests ? group.failed_requests / group.total_requests : 0;
          group.average_connect_latency_ms = group.total_requests ? Math.round(group.latency_sum / group.total_requests) : 0;
          group.health = group.failure_rate >= 0.5 || group.slow_requests >= 3
            ? 'warning'
            : group.failure_rate >= 0.1 || group.slow_requests > 0
              ? 'warning'
              : 'ok';
          return group;
        })
        .sort((a, b) => b.total_requests - a.total_requests);
    };
    const providerHealthTable = hosts => {
      const groups = providerGroups(hosts).slice(0, 6);
      if (!groups.length) {
        return '<div class="table-empty">暂无 Provider 数据</div>';
      }
      const rows = groups.map(group => {
        const health = group.health === 'ok' ? '正常' : '观察';
        const badgeClass = group.health === 'ok' ? 'good' : 'warn';
        const subtitle = group.hosts.slice(0, 2).join(' / ');
        return `
          <tr>
            <td>
              <div class="provider-name">
                <span class="provider-logo" style="background:${escapeHtml(group.color)}">${escapeHtml(group.logo)}</span>
                <span class="provider-title">
                  <strong>${escapeHtml(group.name)}</strong>
                  <span class="provider-subtitle">${escapeHtml(subtitle)}</span>
                </span>
              </div>
            </td>
            <td><span class="status-badge ${badgeClass}">${health}</span></td>
            <td>${successRateText(group.success_rate)}</td>
            <td>${fmt.format(group.total_requests || 0)}</td>
            <td>${fmt.format(group.average_connect_latency_ms || 0)}ms</td>
          </tr>
        `;
      }).join('');
      return `
        <table class="provider-table">
          <thead><tr><th>Provider</th><th>状态</th><th>成功率</th><th>请求</th><th>建连</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      `;
    };
    const successRateText = value => `${(value * 100).toFixed(2)}%`;
    const recentRows = requests => {
      if (!requests.length) {
        return '<div class="row"><span>暂无请求</span><strong>0</strong></div>';
      }
      return requests.map(request => {
        const when = request.started_at
          ? new Date(request.started_at).toLocaleTimeString()
          : '-';
        const statusClass = request.success ? 'good' : 'bad';
        const statusText = request.success ? '成功' : '失败';
        const error = request.error ? ` / ${request.error}` : '';
        const rowClass = request.success
          ? (request.slow ? 'slow-request' : '')
          : 'failed-request';
        return `
          <div class="request-row ${rowClass}">
            <div class="request-main">
              <strong>${escapeHtml(request.host || '-')}</strong>
              <span>${escapeHtml(request.method || '-')} · ${escapeHtml(routeText(request.route || '-'))}${escapeHtml(error)}</span>
            </div>
            <div class="host-meta">
              <span class="pill ${statusClass}">${statusText}</span>
              <span class="pill">建连 ${fmt.format(request.connect_latency_ms || 0)}ms</span>
              <span class="pill">持续 ${fmt.format(request.duration_ms || request.latency_ms || 0)}ms</span>
              <span class="request-time">${escapeHtml(when)}</span>
            </div>
          </div>
        `;
      }).join('');
    };
    const anomalyRows = requests => {
      const anomalies = (requests || []).filter(request => !request.success || request.slow).slice(0, 5);
      if (!anomalies.length) {
        return '<div class="row"><span>当前范围内暂无异常请求</span><strong>OK</strong></div>';
      }
      return recentRows(anomalies);
    };
    const requestTableRows = requests => {
      const items = (requests || []).slice(0, 6);
      if (!items.length) {
        return '<div class="table-empty">暂无请求</div>';
      }
      const rows = items.map(request => {
        const when = request.started_at ? new Date(request.started_at).toLocaleTimeString() : '-';
        const statusClass = request.success ? 'good' : 'bad';
        const statusText = request.success ? '成功' : '失败';
        return `
          <tr>
            <td>${escapeHtml(when)}</td>
            <td><strong>${escapeHtml(request.host || '-')}</strong></td>
            <td>${escapeHtml(request.method || '-')}</td>
            <td>${escapeHtml(routeText(request.route || '-'))}</td>
            <td><span class="status-badge ${statusClass}">${statusText}</span></td>
            <td>${fmt.format(request.duration_ms || request.latency_ms || 0)}ms</td>
          </tr>
        `;
      }).join('');
      return `
        <table class="data-table">
          <thead><tr><th>时间</th><th>Host</th><th>方法</th><th>路由</th><th>状态</th><th>耗时</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      `;
    };
    const anomalyTableRows = (requests, alerts) => {
      const requestAnomalies = (requests || []).filter(request => !request.success || request.slow).slice(0, 5);
      const alertRows = (alerts || []).slice(0, 3).map(alert => `
        <tr>
          <td>${escapeHtml(severityLabel(alert.severity))}</td>
          <td><strong>${escapeHtml(alertKindLabel(alert.kind))}</strong></td>
          <td>${escapeHtml(alertDetailText(alert))}</td>
          <td>-</td>
        </tr>
      `);
      const requestRows = requestAnomalies.map(request => {
        const kind = request.success ? '慢请求' : '失败请求';
        const when = request.started_at ? new Date(request.started_at).toLocaleTimeString() : '-';
        return `
          <tr>
            <td>${escapeHtml(kind)}</td>
            <td><strong>${escapeHtml(request.host || '-')}</strong></td>
            <td>${escapeHtml(request.error || routeText(request.route || '-'))}</td>
            <td>${escapeHtml(when)}</td>
          </tr>
        `;
      });
      const rows = [...alertRows, ...requestRows].join('');
      if (!rows) {
        return '<div class="table-empty">当前范围内暂无异常请求</div>';
      }
      return `
        <table class="data-table">
          <thead><tr><th>类型</th><th>对象</th><th>说明</th><th>时间</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      `;
    };
    const runtimeRows = status => {
      const proxyText = status.proxy_enabled === true
        ? '已启用'
        : status.proxy_enabled === false
          ? '未启用'
          : '未知';
      const upstream = status.upstream_proxy || '直连 / 未检测到系统代理';
      const whitelistLoadedAt = status.whitelist_loaded_at
        ? new Date(status.whitelist_loaded_at).toLocaleString()
        : '尚未加载';
      const items = [
        ['系统代理', proxyText],
        ['上游地址', upstream],
        ['白名单条目', fmt.format(status.whitelist_count || 0)],
        ['白名单文件', status.whitelist_path || '-'],
        ['加载时间', whitelistLoadedAt],
      ];
      return items.map(([label, value]) => `
        <div class="runtime-item">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(value)}</strong>
        </div>
      `).join('');
    };
    const updateShellStatus = status => {
      const proxyChip = document.getElementById('proxyChip');
      const dashboardChip = document.getElementById('dashboardChip');
      const upstreamChip = document.getElementById('upstreamChip');
      proxyChip.textContent = 'Proxy 127.0.0.1:8889';
      dashboardChip.textContent = 'Dashboard 127.0.0.1:8890';
      upstreamChip.textContent = status.proxy_enabled
        ? `Upstream ${status.upstream_proxy || 'enabled'}`
        : 'Upstream direct';
      upstreamChip.classList.toggle('good', status.proxy_enabled === true);
      upstreamChip.classList.toggle('warning', status.proxy_enabled === false);
    };
    const renderModelFilter = models => {
      const filter = document.getElementById('modelFilter');
      const entries = Object.entries(models)
        .sort((a, b) => b[1].total_tokens - a[1].total_tokens);
      if (!entries.length) {
        filter.innerHTML = '';
        selectedModels.clear();
        return;
      }
      const allActive = selectedModels.size === 0 ? ' active' : '';
      filter.innerHTML = `<button data-model="__all" class="${allActive}">全部模型</button>` + entries.map(([model]) => {
        const active = selectedModels.has(model) ? ' active' : '';
        return `<button data-model="${escapeHtml(model)}" class="${active}" title="${escapeHtml(model)}">${escapeHtml(modelDisplayName(model))}</button>`;
      }).join('');
      filter.querySelectorAll('[data-model]').forEach(button => {
        button.addEventListener('click', () => {
          const model = button.dataset.model;
          if (model === '__all') {
            selectedModels.clear();
          } else if (selectedModels.has(model)) {
            selectedModels.delete(model);
          } else {
            selectedModels.add(model);
          }
          refresh();
        });
      });
    };
    const linePath = (points, key, width, height, pad, maxValue) => {
      if (points.length < 2 || maxValue <= 0) return '';
      return points.map((point, index) => {
        const x = pad + (index * (width - pad * 2)) / (points.length - 1);
        const y = height - pad - ((point[key] || 0) / maxValue) * (height - pad * 2);
        return `${index ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`;
      }).join(' ');
    };
    const hourKey = value => {
      const date = new Date(value);
      return [
        date.getFullYear(),
        String(date.getMonth() + 1).padStart(2, '0'),
        String(date.getDate()).padStart(2, '0'),
        String(date.getHours()).padStart(2, '0'),
      ].join('-');
    };
    const normalizeTrendPoints = points => {
      if (currentRange !== 'day') return points;
      const byHour = new Map((points || []).map(point => [hourKey(point.bucket), point]));
      const cursor = new Date();
      cursor.setHours(0, 0, 0, 0);
      const end = new Date();
      end.setMinutes(0, 0, 0);
      const filled = [];
      while (cursor <= end) {
        const key = hourKey(cursor);
        filled.push(byHour.get(key) || {
          bucket: cursor.toISOString(),
          proxy_requests: 0,
          failed_requests: 0,
          average_latency_ms: 0,
          average_connect_latency_ms: 0,
          input_tokens: 0,
          output_tokens: 0,
          total_tokens: 0,
          cache_read_input_tokens: 0,
          cache_creation_input_tokens: 0,
          estimated_cost: 0,
        });
        cursor.setHours(cursor.getHours() + 1);
      }
      return filled;
    };
    const trendLabel = value => {
      const date = new Date(value);
      return currentRange === 'day'
        ? `${String(date.getHours()).padStart(2, '0')}:00`
        : date.toLocaleDateString();
    };
    const renderTrendChart = points => {
      const chart = document.getElementById('trendChart');
      if (!points.length) {
        chart.className = 'empty-chart';
        chart.innerHTML = '暂无趋势数据';
        return;
      }
      const chartPoints = normalizeTrendPoints(points);
      chart.className = 'chart';
      const width = 960;
      const height = 220;
      const pad = 28;
      const maxTokens = Math.max(...chartPoints.map(point => point.total_tokens || 0), 1);
      const maxCost = Math.max(...chartPoints.map(point => point.estimated_cost || 0), 1);
      const tokenPath = linePath(chartPoints, 'total_tokens', width, height, pad, maxTokens);
      const costPath = linePath(chartPoints, 'estimated_cost', width, height, pad, maxCost);
      const first = trendLabel(chartPoints[0].bucket);
      const last = trendLabel(chartPoints[chartPoints.length - 1].bucket);
      chart.innerHTML = `
        <svg viewBox="0 0 ${width} ${height}" width="100%" height="220" role="img" aria-label="token and cost trends">
          <line x1="${pad}" y1="${height - pad}" x2="${width - pad}" y2="${height - pad}" stroke="#d7e0ef"/>
          <line x1="${pad}" y1="${pad}" x2="${pad}" y2="${height - pad}" stroke="#d7e0ef"/>
          <path d="${tokenPath}" fill="none" stroke="#137f6d" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
          <path d="${costPath}" fill="none" stroke="#d92d3a" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
          <text x="${pad}" y="${height - 6}" fill="#66738a" font-size="13" font-weight="700">${escapeHtml(first)}</text>
          <text x="${width - pad}" y="${height - 6}" text-anchor="end" fill="#66738a" font-size="13" font-weight="700">${escapeHtml(last)}</text>
          <text x="${pad}" y="18" fill="#137f6d" font-size="13" font-weight="800">Token ${compactNumber(maxTokens)}</text>
          <text x="${width - pad}" y="18" text-anchor="end" fill="#d92d3a" font-size="13" font-weight="800">费用 ${money(maxCost)}</text>
        </svg>
      `;
    };

    async function refresh() {
      const modelParams = [...selectedModels]
        .map(model => `model=${encodeURIComponent(model)}`)
        .join('&');
      const trendQuery = modelParams
        ? `range=${currentRange}&${modelParams}`
        : `range=${currentRange}`;
      const [res, trendRes, recentRes, runtimeRes] = await Promise.all([
        fetch(`/api/summary?range=${currentRange}`, { cache: 'no-store' }),
        fetch(`/api/trends?${trendQuery}`, { cache: 'no-store' }),
        fetch('/api/recent-requests?limit=20', { cache: 'no-store' }),
        fetch('/api/runtime-status', { cache: 'no-store' }),
      ]);
      const data = await res.json();
      const trendData = await trendRes.json();
      const recentData = await recentRes.json();
      const runtimeData = await runtimeRes.json();
      const p = data.proxy;
      const u = data.usage;
      setMetric('totalRequests', fmt.format(p.total_requests));
      text('requestSub', `较昨日 0% · 成功 ${fmt.format(p.successful_requests)} / 失败 ${fmt.format(p.failed_requests)}`);
      setMetric('totalTokens', compactNumber(u.total_tokens), fmt.format(u.total_tokens));
      text('tokenSub', `较昨日 0% · 输入 ${compactNumber(u.input_tokens)} / 输出 ${compactNumber(u.output_tokens)}`);
      setMetric(
        'cacheTokens',
        compactNumber(u.cache_read_input_tokens + u.cache_creation_input_tokens),
        fmt.format(u.cache_read_input_tokens + u.cache_creation_input_tokens)
      );
      text('cacheSub', `读 ${compactNumber(u.cache_read_input_tokens)} / 写 ${compactNumber(u.cache_creation_input_tokens)}`);
      setMetric('avgLatency', `${fmt.format(p.average_connect_latency_ms || p.average_latency_ms || 0)}ms`);
      setMetric('successRateKpi', percent(p.success_rate));
      text('successRate', `较昨日 0% · 持续 ${fmt.format(p.average_duration_ms || 0)}ms`);
      text('latencySub', `较昨日 0% · 慢建连 ${fmt.format(p.slow_requests || 0)}`);
      setMetric('estimatedCost', money(u.cost.total), `${money(u.cost.total)} CNY`);
      text('costSub', `较昨日 0% · API ${u.cost.billable_models} / 套餐 ${u.cost.token_plan_models} / 未计价 ${u.cost.unknown_models}`);
      renderAlerts(p);
      document.getElementById('routes').innerHTML = rows(Object.entries(p.routes));
      document.getElementById('models').innerHTML = modelRows(u.models);
      document.getElementById('hosts').innerHTML = hostRows(p.hosts || []);
      document.getElementById('providerSummary').innerHTML = providerSummaryRows(p.hosts || []);
      document.getElementById('providerHealth').innerHTML = providerHealthTable(p.hosts || []);
      document.getElementById('recentRequests').innerHTML = recentRows(recentData.requests || []);
      document.getElementById('recentAnomalies').innerHTML = anomalyRows(recentData.requests || []);
      document.getElementById('recentRequestsTable').innerHTML = requestTableRows(recentData.requests || []);
      document.getElementById('recentAnomaliesTable').innerHTML = anomalyTableRows(recentData.requests || [], p.alerts || []);
      document.getElementById('runtimeStatus').innerHTML = runtimeRows(runtimeData || {});
      updateShellStatus(runtimeData || {});
      renderModelFilter(u.models);
      renderTrendChart(trendData.points);
      const refreshedAt = new Date().toLocaleTimeString();
      text('status', `最后刷新 ${refreshedAt}`);
      text('lastRefreshAt', `最后刷新 ${refreshedAt}`);
    }

    document.querySelectorAll('[data-range]').forEach(button => {
      button.addEventListener('click', () => {
        document.querySelectorAll('[data-range]').forEach(b => b.classList.remove('active'));
        button.classList.add('active');
        currentRange = button.dataset.range;
        document.getElementById('timeWindow').value = currentRange;
        refresh();
      });
    });
    document.getElementById('timeWindow').addEventListener('change', event => {
      currentRange = event.target.value;
      document.querySelectorAll('[data-range]').forEach(button => {
        button.classList.toggle('active', button.dataset.range === currentRange);
      });
      refresh();
    });
    document.getElementById('clearProxy').addEventListener('click', async () => {
      await fetch('/api/clear-proxy-stats', { method: 'POST' });
      refresh();
    });
    tabLinks.forEach(button => {
      button.addEventListener('click', event => {
        event.preventDefault();
        switchTab(button.dataset.tabTarget);
      });
    });
    layoutToggle.addEventListener('click', () => {
      setLayoutEditing(!layoutEditing);
    });
    resetLayout.addEventListener('click', () => {
      restoreDefaultLayout();
      saveLayout();
    });
    layoutRoot.addEventListener('dragstart', event => {
      if (!layoutEditing) {
        event.preventDefault();
        return;
      }
      draggedWidget = event.target.closest('[data-widget]');
      if (!draggedWidget) return;
      draggedWidget.classList.add('dragging');
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', draggedWidget.dataset.widget);
    });
    layoutRoot.addEventListener('dragover', event => {
      if (!layoutEditing || !draggedWidget) return;
      event.preventDefault();
      const after = widgetAfterPointer(event.clientY);
      if (after) {
        layoutRoot.insertBefore(draggedWidget, after);
      } else {
        layoutRoot.appendChild(draggedWidget);
      }
    });
    layoutRoot.addEventListener('dragend', () => {
      if (!draggedWidget) return;
      draggedWidget.classList.remove('dragging');
      draggedWidget = null;
      saveLayout();
    });
    loadLayout();
    refresh();
    const scheduleRefresh = () => {
      if (refreshTimer) {
        clearInterval(refreshTimer);
        refreshTimer = null;
      }
      const interval = Number(document.getElementById('autoRefresh').value || 0);
      if (interval > 0) {
        refreshTimer = setInterval(refresh, interval);
      }
    };
    document.getElementById('autoRefresh').addEventListener('change', scheduleRefresh);
    document.getElementById('themeToggle').addEventListener('click', () => {
      document.body.classList.toggle('dark-mode');
    });
    scheduleRefresh();
  </script>
</body>
</html>"""


async def start_stats_server(stats_store, host=DASHBOARD_HOST, port=DASHBOARD_PORT):
    return await start_stats_server_with_status(stats_store, host, port)


async def start_stats_server_with_status(
    stats_store,
    host=DASHBOARD_HOST,
    port=DASHBOARD_PORT,
    status_provider=None,
):
    server = await asyncio.start_server(
        lambda reader, writer: _handle_client(
            reader,
            writer,
            stats_store,
            status_provider,
        ),
        host,
        port,
    )
    return server


async def _handle_client(reader, writer, stats_store, status_provider=None):
    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=5)
        if not request_line:
            writer.close()
            return

        parts = request_line.decode("latin-1", errors="replace").strip().split()
        method = parts[0] if len(parts) > 0 else "GET"
        target = parts[1] if len(parts) > 1 else "/"

        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break

        status, headers, body = handle_stats_request(
            method,
            urlparse(target),
            stats_store,
            status_provider=status_provider,
        )
        reason = {
            200: "OK",
            201: "Created",
            404: "Not Found",
        }.get(status, "OK")
        header_lines = [
            f"HTTP/1.1 {status} {reason}",
            f"Content-Length: {len(body)}",
            "Connection: close",
        ]
        for key, value in headers.items():
            header_lines.append(f"{key}: {value}")
        writer.write(("\r\n".join(header_lines) + "\r\n\r\n").encode() + body)
        await writer.drain()
    finally:
        writer.close()
