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


def handle_stats_request(method, parsed_url, stats_store):
    if method == "GET" and parsed_url.path in ("", "/"):
        return build_html_response(200, DASHBOARD_HTML)

    if method == "GET" and parsed_url.path == "/api/summary":
        params = parse_qs(parsed_url.query)
        range_name = (params.get("range") or ["day"])[0]
        return build_stats_response(200, stats_store.get_summary(range_name))

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
    .segment button, .danger {
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
    .grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(180px, 1fr));
      gap: 18px;
    }
    .card {
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
      font-size: 44px;
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
    .status {
      margin-top: 18px;
      color: var(--muted);
      font-size: 13px;
    }
    @media (max-width: 920px) {
      main { width: calc(100vw - 24px); padding: 18px; border-radius: 20px; }
      header { align-items: flex-start; flex-direction: column; }
      .grid, .details { grid-template-columns: 1fr; }
      .model-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .controls { width: 100%; justify-content: space-between; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div class="brand">
        <svg class="pulse" viewBox="0 0 32 32" aria-hidden="true">
          <path d="M3 17h6l3-11 6 22 4-11h7" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        <span>Smart Proxy</span>
      </div>
      <div class="controls">
        <div class="segment" role="group" aria-label="range">
          <button data-range="day" class="active">日</button>
          <button data-range="week">周</button>
          <button data-range="month">月</button>
          <button data-range="all">全</button>
        </div>
        <button class="danger" id="clearProxy">清除统计</button>
      </div>
    </header>

    <section class="grid">
      <article class="card" style="--accent: var(--blue)">
        <div class="label">总请求数</div>
        <div class="value" id="totalRequests">0</div>
        <div class="sub" id="requestSub">成功 0 / 失败 0</div>
      </article>
      <article class="card" style="--accent: var(--green)">
        <div class="label">总 TOKEN 数</div>
        <div class="value" id="totalTokens">0</div>
        <div class="sub" id="tokenSub">输入 0 / 输出 0</div>
      </article>
      <article class="card" style="--accent: var(--violet)">
        <div class="label">缓存 TOKEN</div>
        <div class="value" id="cacheTokens">0</div>
        <div class="sub" id="cacheSub">读 0 / 写 0</div>
      </article>
      <article class="card" style="--accent: var(--orange)">
        <div class="label">平均延迟</div>
        <div class="value" id="avgLatency">0ms</div>
        <div class="sub" id="successRate">成功率 0%</div>
      </article>
    </section>

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
    <div class="status" id="status">等待刷新</div>
  </main>
  <script>
    let currentRange = 'day';
    const fmt = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 });
    const unitFmt = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 });
    const text = (id, value) => { document.getElementById(id).textContent = value; };
    const setMetric = (id, value, title) => {
      const element = document.getElementById(id);
      element.textContent = value;
      element.title = title || value;
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
    const escapeHtml = value => String(value)
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
    const rows = entries => entries.length
      ? entries.map(([k, v]) => `<div class="row"><span>${escapeHtml(k)}</span><strong>${fmt.format(v)}</strong></div>`).join('')
      : '<div class="row"><span>暂无数据</span><strong>0</strong></div>';
    const modelRows = models => {
      const entries = Object.entries(models)
        .sort((a, b) => b[1].total_tokens - a[1].total_tokens);
      if (!entries.length) {
        return '<div class="row"><span>暂无数据</span><strong>0</strong></div>';
      }
      return entries.map(([model, usage]) => `
        <div class="model-row">
          <span class="model-name">${escapeHtml(model)}</span>
          <strong class="model-total">${fmt.format(usage.total_tokens)}</strong>
          <div class="model-metrics">
            <span class="metric">输入 <strong>${fmt.format(usage.input_tokens)}</strong></span>
            <span class="metric">输出 <strong>${fmt.format(usage.output_tokens)}</strong></span>
            <span class="metric">缓存读 <strong>${fmt.format(usage.cache_read_input_tokens)}</strong></span>
            <span class="metric">缓存写 <strong>${fmt.format(usage.cache_creation_input_tokens)}</strong></span>
          </div>
        </div>
      `).join('');
    };

    async function refresh() {
      const res = await fetch(`/api/summary?range=${currentRange}`, { cache: 'no-store' });
      const data = await res.json();
      const p = data.proxy;
      const u = data.usage;
      setMetric('totalRequests', fmt.format(p.total_requests));
      text('requestSub', `成功 ${fmt.format(p.successful_requests)} / 失败 ${fmt.format(p.failed_requests)}`);
      setMetric('totalTokens', compactNumber(u.total_tokens), fmt.format(u.total_tokens));
      text('tokenSub', `输入 ${compactNumber(u.input_tokens)} / 输出 ${compactNumber(u.output_tokens)}`);
      setMetric(
        'cacheTokens',
        compactNumber(u.cache_read_input_tokens + u.cache_creation_input_tokens),
        fmt.format(u.cache_read_input_tokens + u.cache_creation_input_tokens)
      );
      text('cacheSub', `读 ${compactNumber(u.cache_read_input_tokens)} / 写 ${compactNumber(u.cache_creation_input_tokens)}`);
      setMetric('avgLatency', `${fmt.format(p.average_latency_ms)}ms`);
      text('successRate', `成功率 ${percent(p.success_rate)}`);
      document.getElementById('routes').innerHTML = rows(Object.entries(p.routes));
      document.getElementById('models').innerHTML = modelRows(u.models);
      text('status', `最后刷新 ${new Date().toLocaleTimeString()}`);
    }

    document.querySelectorAll('[data-range]').forEach(button => {
      button.addEventListener('click', () => {
        document.querySelectorAll('[data-range]').forEach(b => b.classList.remove('active'));
        button.classList.add('active');
        currentRange = button.dataset.range;
        refresh();
      });
    });
    document.getElementById('clearProxy').addEventListener('click', async () => {
      await fetch('/api/clear-proxy-stats', { method: 'POST' });
      refresh();
    });
    refresh();
    setInterval(refresh, 5000);
  </script>
</body>
</html>"""


async def start_stats_server(stats_store, host=DASHBOARD_HOST, port=DASHBOARD_PORT):
    server = await asyncio.start_server(
        lambda reader, writer: _handle_client(reader, writer, stats_store),
        host,
        port,
    )
    return server


async def _handle_client(reader, writer, stats_store):
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
