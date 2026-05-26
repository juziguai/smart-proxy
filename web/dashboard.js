    let currentRange = 'day';
    const selectedModels = new Set();
    let layoutEditing = false;
    let draggedWidget = null;
    let refreshTimer = null;
    let whitelistEntriesState = [];
    let doctorLoaded = false;
    let doctorLoading = false;
    let providerHealthLoaded = false;
    let providerHealthLoading = false;
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
      if (target === 'doctor' && !doctorLoaded) {
        refreshDoctor();
      }
      if (target === 'providers' && !providerHealthLoaded) {
        refreshProviderQuotaHealth();
      }
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
    const changeText = (label, current, previous) => {
      if (previous === undefined || previous === null) return label;
      if (previous === 0) return current === 0 ? `${label} 持平` : `${label} 新增`;
      const change = Math.round(((current - previous) / Math.abs(previous)) * 100);
      if (change === 0) return `${label} 持平`;
      return `${label} ${change > 0 ? '+' : ''}${change}%`;
    };
    const comparisonText = (comparison, current, previous) => {
      const label = comparison?.label || '较昨日';
      if (!comparison?.available) return label;
      return changeText(label, current || 0, previous || 0);
    };
    const deltaComparisonText = (comparison, current, previous, unit = '') => {
      const label = comparison?.label || '较昨日';
      if (!comparison?.available || previous === undefined || previous === null) return label;
      const delta = Math.round((current || 0) - (previous || 0));
      if (delta === 0) return `${label} 持平`;
      return `${label} ${delta > 0 ? '+' : ''}${fmt.format(delta)}${unit}`;
    };
    const pointComparisonText = (comparison, current, previous) => {
      const label = comparison?.label || '较昨日';
      if (!comparison?.available || previous === undefined || previous === null) return label;
      const delta = Math.round(((current || 0) - (previous || 0)) * 100);
      if (delta === 0) return `${label} 持平`;
      return `${label} ${delta > 0 ? '+' : ''}${fmt.format(delta)}个百分点`;
    };
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
    const hostIntent = host => {
      const value = String(host || '').toLowerCase();
      if (value.includes('api.deepseek.com') || value.includes('api.minimaxi.com') || value.includes('anthropic')) {
        return 'model_api';
      }
      if (value.includes('github')) return 'developer_service';
      if (value.includes('douyin')) return 'content_site';
      return 'generic';
    };
    const alertAdviceText = alert => {
      const intent = hostIntent(alert.host);
      if (alert.kind === 'host_failures') {
        return intent === 'model_api'
          ? '模型链路失败，优先检查上游代理、Key 与限流'
          : '失败率偏高，先看是否需要白名单直连或临时降噪';
      }
      if (alert.kind === 'slow_requests') {
        if (intent === 'model_api') return '会影响模型响应，建议重点观察上游代理和线路';
        if (intent === 'developer_service') return '开发依赖较慢，可考虑加入白名单直连';
        if (intent === 'content_site') return '非核心链路，通常可忽略或加入白名单降延迟';
        return '建连偏慢，建议结合 Host 频率判断是否白名单直连';
      }
      return '先观察频率，持续出现再处理';
    };
    const requestAdviceText = request => {
      if (!request.success) return '单次请求失败，优先查看错误信息和上游可用性';
      if (request.slow) return '单次请求偏慢，若重复出现再考虑白名单或换线路';
      return '观察即可';
    };
    const alertObservedAt = (alert, requests) => {
      const host = alert.host || '';
      const related = (requests || [])
        .filter(request => request.host === host)
        .filter(request => {
          if (alert.kind === 'slow_requests') return request.slow;
          if (alert.kind === 'host_failures') return !request.success;
          return !request.success || request.slow;
        })
        .sort((a, b) => new Date(b.started_at || 0) - new Date(a.started_at || 0));
      if (related.length && related[0].started_at) {
        return new Date(related[0].started_at).toLocaleTimeString();
      }
      return '聚合告警';
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
          <thead><tr><th>服务商</th><th>状态</th><th>成功率</th><th title="代理层 CONNECT 隧道连接数，不等于模型 API 调用次数">连接</th><th>建连</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      `;
    };
    const successRateText = value => `${(value * 100).toFixed(2)}%`;
    const clientLabel = client => client?.client_label || 'Unknown';
    const clientProcessText = client => {
      const process = client?.client_process || '';
      return process && process !== 'unknown' ? process : 'unknown process';
    };
    const requestClientText = request => {
      const label = request?.client_label || 'Unknown';
      const process = request?.client_process || '';
      const pid = request?.client_pid ? ` PID ${request.client_pid}` : '';
      return process ? `${label} / ${process}${pid}` : label;
    };
    const clientRows = clients => {
      if (!clients.length) {
        return '<div class="row"><span>No client data</span><strong>0</strong></div>';
      }
      return clients.map(client => `
        <div class="host-row client-row">
          <div class="host-main">
            <strong>${escapeHtml(clientLabel(client))}</strong>
            <span>${escapeHtml(clientProcessText(client))}</span>
          </div>
          <div class="host-meta">
            <span class="pill">${fmt.format(client.total_requests || 0)} requests</span>
            <span class="pill good">ok ${fmt.format(client.successful_requests || 0)}</span>
            <span class="pill bad">fail ${fmt.format(client.failed_requests || 0)}</span>
            <span class="pill">slow ${fmt.format(client.slow_requests || 0)}</span>
            <span class="pill">connect ${fmt.format(client.average_connect_latency_ms || 0)}ms</span>
          </div>
        </div>
      `).join('');
    };
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
              <span class="pill client-source" title="${escapeHtml(requestClientText(request))}">${escapeHtml(requestClientText(request))}</span>
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
            <td><span class="pill client-source" title="${escapeHtml(requestClientText(request))}">${escapeHtml(requestClientText(request))}</span></td>
            <td>${escapeHtml(request.method || '-')}</td>
            <td>${escapeHtml(routeText(request.route || '-'))}</td>
            <td><span class="status-badge ${statusClass}">${statusText}</span></td>
            <td>${fmt.format(request.duration_ms || request.latency_ms || 0)}ms</td>
          </tr>
        `;
      }).join('');
      return `
        <table class="data-table">
          <thead><tr><th>时间</th><th>Host</th><th>Client</th><th>方法</th><th>路由</th><th>状态</th><th>耗时</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      `;
    };
    const anomalyTableRows = (requests, alerts) => {
      const requestAnomalies = (requests || []).filter(request => !request.success || request.slow).slice(0, 5);
      const alertRows = (alerts || []).slice(0, 3).map(alert => `
        <article class="anomaly-card">
          <div class="anomaly-meta">
            <span class="status-badge ${alert.severity === 'critical' ? 'bad' : 'warn'}">${escapeHtml(severityLabel(alert.severity))}</span>
            <span class="anomaly-kind">${escapeHtml(alertKindLabel(alert.kind))}</span>
          </div>
          <div class="anomaly-main">
            <strong title="${escapeHtml(alert.host || '-')}">${escapeHtml(alert.host || '-')}</strong>
            <span>${escapeHtml(alertDetailText(alert))}</span>
          </div>
          <div class="anomaly-side">
            <strong>${escapeHtml(alertAdviceText(alert))}</strong>
            <span>${escapeHtml(alertObservedAt(alert, requests))}</span>
          </div>
        </article>
      `);
      const requestRows = requestAnomalies.map(request => {
        const kind = request.success ? '慢请求' : '失败请求';
        const when = request.started_at ? new Date(request.started_at).toLocaleTimeString() : '-';
        return `
          <article class="anomaly-card">
            <div class="anomaly-meta">
              <span class="status-badge ${request.success ? 'warn' : 'bad'}">${request.success ? '提醒' : '严重'}</span>
              <span class="anomaly-kind">${escapeHtml(kind)}</span>
            </div>
            <div class="anomaly-main">
              <strong title="${escapeHtml(request.host || '-')}">${escapeHtml(request.host || '-')}</strong>
              <span>${escapeHtml(request.error || routeText(request.route || '-'))}</span>
            </div>
            <div class="anomaly-side">
              <strong>${escapeHtml(requestAdviceText(request))}</strong>
              <span>${escapeHtml(when)}</span>
            </div>
          </article>
        `;
      });
      const rows = [...alertRows, ...requestRows].join('');
      if (!rows) {
        return '<div class="table-empty">当前范围内暂无异常请求</div>';
      }
      return `
        <div class="anomaly-list">${rows}</div>
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
      let baseHtml = items.map(([label, value]) => `
        <div class="runtime-item">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(value)}</strong>
        </div>
      `).join('');
      
      // 追加：渲染实时智能路由分流状态
      let adaptiveSection = '';
      if (status.active_adaptive_routes && Object.keys(status.active_adaptive_routes).length > 0) {
        const routeItems = Object.entries(status.active_adaptive_routes).map(([host, route]) => {
          const isDemoted = route.status === 'DEMOTED';
          const badgeClass = isDemoted ? 'warn' : 'good';
          const label = isDemoted ? '⚠️ 自动走代理' : '⚡ 智能直连';
          const desc = isDemoted ? '直连崩溃自愈中' : '代理拥堵升级中';
          return `
            <div class="runtime-item adaptive-route-item ${badgeClass}" style="border-left: 3px solid ${isDemoted ? '#f59e0b' : '#10b981'}; padding-left: 8px; margin-top: 6px; background: ${isDemoted ? '#fffbeb' : '#ecfdf5'};">
              <span style="font-weight: 600; color: #1f2937; max-width: 50%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${escapeHtml(host)}">${escapeHtml(host)}</span>
              <strong>
                <span class="status-badge ${badgeClass}" style="font-size: 10px; padding: 2px 6px;">${label}</span>
                <span style="font-size:10px; color:#6b7280; margin-left:4px; font-weight:normal;">${desc} (${route.expires_in}s)</span>
              </strong>
            </div>
          `;
        }).join('');
        adaptiveSection = `
          <div class="runtime-section-title" style="margin-top:20px; margin-bottom:8px; font-size:12px; color:#64748b; font-weight:bold; border-bottom:1px dashed #e2e8f0; padding-bottom:4px;">实时智能分流状态</div>
          ${routeItems}
        `;
      }
      return baseHtml + adaptiveSection;
    };
    const updateShellStatus = status => {
      const proxyChip = document.getElementById('proxyChip');
      const dashboardChip = document.getElementById('dashboardChip');
      const upstreamChip = document.getElementById('upstreamChip');
      proxyChip.textContent = '代理 127.0.0.1:8889';
      dashboardChip.textContent = '管理端 127.0.0.1:8890';
      upstreamChip.textContent = status.proxy_enabled
        ? `Upstream ${status.upstream_proxy || 'enabled'}`
        : 'Upstream direct';
      upstreamChip.classList.toggle('good', status.proxy_enabled === true);
      upstreamChip.classList.toggle('warning', status.proxy_enabled === false);
    };
    const showWhitelistFeedback = (message, type = 'info') => {
      const feedback = document.getElementById('whitelistFeedback');
      feedback.textContent = message;
      feedback.className = `feedback show ${type}`;
    };
    const renderWhitelistEntries = () => {
      const container = document.getElementById('whitelistEntries');
      if (!whitelistEntriesState.length) {
        container.innerHTML = '<div class="table-empty">白名单为空，保存后会自动创建 whitelist.txt</div>';
        return;
      }
      container.innerHTML = whitelistEntriesState.map(entry => `
        <div class="entry-item">
          <div>
            <strong>${escapeHtml(entry)}</strong>
            <span>${entry.includes('*') ? '通配规则' : '精确 Host'}</span>
          </div>
          <button class="mini-danger" data-remove-whitelist="${escapeHtml(entry)}">删除</button>
        </div>
      `).join('');
      container.querySelectorAll('[data-remove-whitelist]').forEach(button => {
        button.addEventListener('click', () => {
          whitelistEntriesState = whitelistEntriesState.filter(entry => entry !== button.dataset.removeWhitelist);
          renderWhitelistEntries();
        });
      });
    };
    const wildcardToRegex = pattern => new RegExp(`^${String(pattern)
      .replace(/[.+?^${}()|[\\]\\\\]/g, '\\\\$&')
      .replaceAll('*', '.*')}$`, 'i');
    const whitelistMatchesHost = host => {
      const value = String(host || '').trim();
      if (!value) return false;
      return whitelistEntriesState.some(entry => {
        const pattern = String(entry || '').trim();
        if (!pattern) return false;
        return pattern.includes('*')
          ? wildcardToRegex(pattern).test(value)
          : pattern.toLowerCase() === value.toLowerCase();
      });
    };
    const renderWhitelistCandidates = candidates => {
      const container = document.getElementById('whitelistCandidates');
      if (!candidates.length) {
        container.innerHTML = '<div class="table-empty">暂无候选 Host</div>';
        return;
      }
      container.innerHTML = candidates.map(candidate => `
        <div class="candidate-item">
          <div>
            <strong>${escapeHtml(candidate.host || '-')}</strong>
            <span>代理 ${fmt.format(candidate.proxy_requests || 0)} 次 · 慢建连 ${fmt.format(candidate.slow_requests || 0)} · 平均 ${fmt.format(candidate.average_connect_latency_ms || 0)}ms</span>
          </div>
          ${whitelistMatchesHost(candidate.host)
            ? `<button class="secondary-action" disabled data-add-candidate="${escapeHtml(candidate.host || '')}">已加入</button>`
            : `<button class="secondary-action" data-add-candidate="${escapeHtml(candidate.host || '')}">加入</button>`}
        </div>
      `).join('');
      container.querySelectorAll('[data-add-candidate]').forEach(button => {
        button.addEventListener('click', async () => {
          if (button.disabled) return;
          await addWhitelistCandidate(button.dataset.addCandidate, button);
        });
      });
    };
    const refreshWhitelist = async () => {
      const response = await fetch('/api/whitelist', { cache: 'no-store' });
      const data = await response.json();
      whitelistEntriesState = data.entries || [];
      const loadedAt = data.loaded_at ? new Date(data.loaded_at).toLocaleString() : '尚未加载';
      text('whitelistMeta', `${data.path || 'whitelist.txt'} · ${fmt.format(data.count || 0)} 条 · ${loadedAt}`);
      renderWhitelistEntries();
      renderWhitelistCandidates(data.candidates || []);
    };
    const addWhitelistEntry = entry => {
      const value = String(entry || '').trim();
      if (!value || whitelistEntriesState.includes(value)) return;
      whitelistEntriesState = [...whitelistEntriesState, value].sort();
      document.getElementById('whitelistInput').value = '';
      renderWhitelistEntries();
    };
    const saveWhitelistEntries = async (successMessage = '白名单保存成功') => {
      const response = await fetch('/api/whitelist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ entries: whitelistEntriesState }),
      });
      const data = await response.json();
      if (!response.ok) {
        showWhitelistFeedback(`保存失败：${data.error || response.status}`, 'error');
        throw new Error(data.error || `HTTP ${response.status}`);
      }
      whitelistEntriesState = data.entries || [];
      const loadedAt = data.loaded_at ? new Date(data.loaded_at).toLocaleString() : '刚刚';
      text('whitelistMeta', `${data.path || 'whitelist.txt'} · ${fmt.format(data.count || 0)} 条 · ${loadedAt}`);
      showWhitelistFeedback(successMessage, 'ok');
      renderWhitelistEntries();
      await refreshWhitelist();
    };
    const addWhitelistCandidate = async (entry, button) => {
      const value = String(entry || '').trim();
      if (!value) return;
      if (whitelistEntriesState.includes(value)) {
        showWhitelistFeedback(`${value} 已在白名单中`, 'info');
        if (button) button.textContent = '已加入';
        return;
      }
      whitelistEntriesState = [...whitelistEntriesState, value].sort();
      renderWhitelistEntries();
      if (button) {
        button.disabled = true;
        button.textContent = '保存中';
      }
      try {
        await saveWhitelistEntries(`${value} 已加入白名单并保存`);
        if (button) button.textContent = '已加入';
      } catch (error) {
        showWhitelistFeedback(`${value} 加入失败：${error.message || error}`, 'error');
        whitelistEntriesState = whitelistEntriesState.filter(item => item !== value);
        renderWhitelistEntries();
        if (button) {
          button.disabled = false;
          button.textContent = '加入';
        }
      }
    };
    const renderDoctor = doctor => {
      const checks = (doctor.checks || [])
        .filter(check => check.key !== 'provider_health');
      const okCount = checks.filter(check => check.status === 'ok').length;
      const generatedAt = doctor.generated_at ? new Date(doctor.generated_at).toLocaleString() : '刚刚';
      text('doctorMeta', `${okCount}/${checks.length} 项通过 · ${generatedAt}`);
      const container = document.getElementById('doctorChecks');
      if (!checks.length) {
        container.innerHTML = '<div class="table-empty">暂无自检结果</div>';
        return;
      }
      container.innerHTML = checks.map(check => `
        <div class="doctor-item">
          <div>
            <strong>${escapeHtml(check.label || check.key || '-')}</strong>
            <span>${escapeHtml(check.detail || '-')}</span>
            ${check.status === 'ok' ? '' : `<span>${escapeHtml(check.fix || '请检查本地配置')}</span>`}
          </div>
          <span class="status-badge doctor-status ${escapeHtml(check.status || 'warning')}">${check.status === 'ok' ? '正常' : '关注'}</span>
        </div>
      `).join('');
    };
    const refreshDoctor = async ({ force = false } = {}) => {
      if (doctorLoading || (doctorLoaded && !force)) return;
      doctorLoading = true;
      try {
        const response = await fetch('/api/doctor', { cache: 'no-store' });
        renderDoctor(await response.json());
        doctorLoaded = true;
      } finally {
        doctorLoading = false;
      }
    };
    const renderProviderQuotaHealth = report => {
      const container = document.getElementById('providerQuotaHealth');
      if (!container) return;
      const check = report?.check;
      const generatedAt = report?.generated_at
        ? new Date(report.generated_at).toLocaleString()
        : '刚刚';
      text('providerQuotaMeta', `服务商额度 / 限流状态 · ${generatedAt}`);
      if (!check) {
        container.innerHTML = '<div class="table-empty">暂无服务商健康数据</div>';
        return;
      }
      const ok = check.status === 'ok';
      container.innerHTML = `
        <div class="doctor-item">
          <div>
            <strong>${escapeHtml(check.label || 'Provider quota / health')}</strong>
            <span>${escapeHtml(check.detail || '-')}</span>
            ${ok ? '' : `<span>${escapeHtml(check.fix || '请检查模型服务商额度或限流状态')}</span>`}
          </div>
          <span class="status-badge doctor-status ${ok ? 'ok' : 'warning'}">${ok ? '正常' : '关注'}</span>
        </div>
      `;
    };
    const providerHealthFromDoctor = doctor => ({
      generated_at: doctor?.generated_at,
      check: (doctor?.checks || [])
        .find(check => check.key === 'provider_health') || null,
    });
    const fetchProviderHealthReport = async () => {
      const response = await fetch('/api/provider-health', { cache: 'no-store' });
      if (response.ok) return response.json();
      const legacyResponse = await fetch('/api/doctor', { cache: 'no-store' });
      return providerHealthFromDoctor(await legacyResponse.json());
    };
    const refreshProviderQuotaHealth = async ({ force = false } = {}) => {
      if (providerHealthLoading || (providerHealthLoaded && !force)) return;
      providerHealthLoading = true;
      try {
        renderProviderQuotaHealth(await fetchProviderHealthReport());
        providerHealthLoaded = true;
      } finally {
        providerHealthLoading = false;
      }
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
          average_duration_ms: 0,
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

    const renderLatencyChart = points => {
      const chart = document.getElementById('latencyChart');
      if (!points.length) {
        chart.className = 'empty-chart';
        chart.innerHTML = '暂无时延趋势数据';
        return;
      }
      const chartPoints = normalizeTrendPoints(points);
      chart.className = 'chart';
      const width = 960;
      const height = 220;
      const pad = 28;
      const maxLatency = Math.max(
        ...chartPoints.map(point => Math.max(point.average_connect_latency_ms || 0, point.average_duration_ms || 0)),
        100
      );
      const connectPath = linePath(chartPoints, 'average_connect_latency_ms', width, height, pad, maxLatency);
      const durationPath = linePath(chartPoints, 'average_duration_ms', width, height, pad, maxLatency);
      const first = trendLabel(chartPoints[0].bucket);
      const last = trendLabel(chartPoints[chartPoints.length - 1].bucket);
      chart.innerHTML = `
        <svg viewBox="0 0 ${width} ${height}" width="100%" height="220" role="img" aria-label="latency trends">
          <line x1="${pad}" y1="${height - pad}" x2="${width - pad}" y2="${height - pad}" stroke="#d7e0ef"/>
          <line x1="${pad}" y1="${pad}" x2="${pad}" y2="${height - pad}" stroke="#d7e0ef"/>
          <path d="${connectPath}" fill="none" stroke="#536dff" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
          <path d="${durationPath}" fill="none" stroke="#8a5a44" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
          <text x="${pad}" y="${height - 6}" fill="#66738a" font-size="13" font-weight="700">${escapeHtml(first)}</text>
          <text x="${width - pad}" y="${height - 6}" text-anchor="end" fill="#66738a" font-size="13" font-weight="700">${escapeHtml(last)}</text>
          <text x="${pad}" y="18" fill="#536dff" font-size="13" font-weight="800">最大建连(T3) ${maxLatency}ms</text>
          <text x="${width - pad}" y="18" text-anchor="end" fill="#8a5a44" font-size="13" font-weight="800">最大持续(T5) ${maxLatency}ms</text>
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
      const comparison = data.comparison || {};
      const previousProxy = comparison.previous?.proxy || {};
      const previousUsage = comparison.previous?.usage || {};
      setMetric('totalRequests', fmt.format(p.total_requests));
      text('requestSub', `${comparisonText(comparison, p.total_requests, previousProxy.total_requests)} · 成功 ${fmt.format(p.successful_requests)} / 失败 ${fmt.format(p.failed_requests)}`);
      setMetric('totalTokens', compactNumber(u.total_tokens), fmt.format(u.total_tokens));
      text('tokenSub', `${comparisonText(comparison, u.total_tokens, previousUsage.total_tokens)} · 输入 ${compactNumber(u.input_tokens)} / 输出 ${compactNumber(u.output_tokens)}`);
      setMetric(
        'cacheTokens',
        compactNumber(u.cache_read_input_tokens + u.cache_creation_input_tokens),
        fmt.format(u.cache_read_input_tokens + u.cache_creation_input_tokens)
      );
      text('cacheSub', `读 ${compactNumber(u.cache_read_input_tokens)} / 写 ${compactNumber(u.cache_creation_input_tokens)}`);
      setMetric('avgLatency', `${fmt.format(p.average_connect_latency_ms || p.average_latency_ms || 0)}ms`);
      setMetric('successRateKpi', percent(p.success_rate));
      text('successRate', `${pointComparisonText(comparison, p.success_rate, previousProxy.success_rate)} · 持续 ${fmt.format(p.average_duration_ms || 0)}ms`);
      text('latencySub', `${deltaComparisonText(comparison, p.average_connect_latency_ms || p.average_latency_ms || 0, previousProxy.average_connect_latency_ms || previousProxy.average_latency_ms, 'ms')} · 慢建连 ${fmt.format(p.slow_requests || 0)}`);
      setMetric('estimatedCost', money(u.cost.total), `${money(u.cost.total)} CNY`);
      text('costSub', `${comparisonText(comparison, u.cost.total, previousUsage.cost?.total)} · API ${u.cost.billable_models} / 套餐 ${u.cost.token_plan_models} / 未计价 ${u.cost.unknown_models}`);
      renderAlerts(p);
      document.getElementById('routes').innerHTML = rows(Object.entries(p.routes));
      document.getElementById('models').innerHTML = modelRows(u.models);
      document.getElementById('hosts').innerHTML = hostRows(p.hosts || []);
      document.getElementById('providerSummary').innerHTML = providerSummaryRows(p.hosts || []);
      document.getElementById('providerHealth').innerHTML = providerHealthTable(p.hosts || []);
      document.getElementById('clientBreakdown').innerHTML = clientRows(p.clients || []);
      document.getElementById('recentRequests').innerHTML = recentRows(recentData.requests || []);
      document.getElementById('recentAnomalies').innerHTML = anomalyRows(recentData.requests || []);
      document.getElementById('recentRequestsTable').innerHTML = requestTableRows(recentData.requests || []);
      document.getElementById('recentAnomaliesTable').innerHTML = anomalyTableRows(recentData.requests || [], p.alerts || []);
      document.getElementById('runtimeStatus').innerHTML = runtimeRows(runtimeData || {});
      updateShellStatus(runtimeData || {});
      renderModelFilter(u.models);
      renderTrendChart(trendData.points);
      renderLatencyChart(trendData.points);
      await Promise.allSettled([refreshWhitelist()]);
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
    document.getElementById('addWhitelistEntry').addEventListener('click', () => {
      addWhitelistEntry(document.getElementById('whitelistInput').value);
      showWhitelistFeedback('已加入本地列表，点击保存后写入 whitelist.txt', 'info');
    });
    document.getElementById('whitelistInput').addEventListener('keydown', event => {
      if (event.key === 'Enter') {
        addWhitelistEntry(event.target.value);
        showWhitelistFeedback('已加入本地列表，点击保存后写入 whitelist.txt', 'info');
      }
    });
    document.getElementById('saveWhitelist').addEventListener('click', saveWhitelistEntries);
    document.getElementById('runDoctor').addEventListener(
      'click',
      () => refreshDoctor({ force: true }),
    );
    document.getElementById('refreshProviderHealth').addEventListener(
      'click',
      () => refreshProviderQuotaHealth({ force: true }),
    );
    scheduleRefresh();
