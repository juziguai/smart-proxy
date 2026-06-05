    let currentRange = 'day';
    let analyticsLoaded = false;
    let analyticsRange = 'today';
    let requestSourceFilterValue = '';
    const selectedModels = new Set();
    let layoutEditing = false;
    let draggedWidget = null;
    let refreshTimer = null;
    let whitelistEntriesState = [];
    let whitelistEntriesBackupState = [];
    let blocklistEntriesState = [];
    let blocklistEntriesBackupState = [];
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
    const initMetricCardFlow = () => {
      const cards = [...document.querySelectorAll('[data-widget="kpis"] [data-kpi-card]')];
      cards.forEach((card, index) => {
        card.style.setProperty('--metric-flow-count', cards.length);
        card.style.setProperty('--metric-flow-index', index);
      });
    };
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
      if (target === 'analytics' && !analyticsLoaded) {
        refreshTrafficAnalytics();
      }
    };

    const refreshTrafficAnalytics = async () => {
      const softwareList = document.getElementById('analyticsSoftwareList');
      const providerList = document.getElementById('analyticsProviderList');
      const claudeCodePanel = document.getElementById('claudeCodePanel');
      const providerColorClass = item => {
        const key = item?.provider_key || '';
        if (key === 'openai') return 'openai-bar';
        if (key === 'anthropic') return 'anthropic-bar';
        if (key === 'google') return 'google-bar';
        if (key === 'deepseek') return 'deepseek-bar';
        if (key === 'mimo') return 'mimo-bar';
        return 'other-bar';
      };
      const providerRows = items => (items || []).map(item => `
        <div class="analytics-row">
          <div class="analytics-label">
            <span class="provider-name">${escapeHtml(item.provider)}</span>
            <span class="provider-count">${item.count} 次 (${item.ratio})</span>
          </div>
          <div class="provider-evidence">${escapeHtml(item.provider_evidence || item.provider_match || '规则匹配')}</div>
          <div class="analytics-progress-bg">
            <div class="analytics-progress-bar ${providerColorClass(item)}" style="width: ${item.ratio}"></div>
          </div>
        </div>
      `).join('');
      const unknownHostRows = items => (items || []).map(item => `
        <div class="analytics-row provider-unknown-row">
          <div class="analytics-label">
            <span class="provider-name">${escapeHtml(item.host)}</span>
            <span class="provider-count">${item.count} 次</span>
          </div>
          <div class="provider-evidence">Claude Code CLI 未匹配模型服务商规则，可加入 provider-rules.json</div>
        </div>
      `).join('');
      const renderMiniMetric = (label, value, tone = '') => `
        <div class="claude-metric ${tone}">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(String(value))}</strong>
        </div>
      `;
      const renderClaudeCodePanel = panel => {
        if (!panel || !panel.total_requests) {
          return '<div class="table-empty">暂无 Claude Code CLI 流量</div>';
        }
        const topology = panel.process_topology || {};
        const topologyNodes = (topology.nodes || []).length
          ? topology.nodes.map((node, index) => `
              <div class="process-topology-node ${escapeHtml(node.role || '')}">
                <strong>${escapeHtml(node.label || 'unknown')}</strong>
                ${node.pid ? `<span>PID ${escapeHtml(String(node.pid))}</span>` : '<span>parent</span>'}
              </div>
              ${index < topology.nodes.length - 1 ? '<div class="process-topology-edge">→</div>' : ''}
            `).join('')
          : '<div class="table-empty">等待进程链证据</div>';
        const providerRows = (panel.provider_mix || []).slice(0, 5).map(item => `
          <div class="claude-provider-row">
            <span>${escapeHtml(item.provider)}</span>
            <strong>${fmt.format(item.count || 0)} 次 · ${escapeHtml(item.ratio || '0.0%')}</strong>
            <small title="${escapeHtml(item.provider_evidence || '')}">
              失败 ${escapeHtml(item.failure_rate || '0.0%')} · 慢 ${fmt.format(item.slow_requests || 0)} · ${escapeHtml(item.provider_evidence || '规则匹配')}
            </small>
          </div>
        `).join('');
        const unknownRows = (panel.unknown_hosts || []).slice(0, 3).map(item => `
          <div class="claude-alert-row">
            <strong>${escapeHtml(item.host)}</strong>
            <span>${fmt.format(item.count || 0)} 次 · 建议加入 provider-rules.json</span>
          </div>
        `).join('');
        const errorRows = (panel.recent_errors || []).slice(0, 3).map(item => `
          <div class="claude-alert-row">
            <strong>${escapeHtml(item.provider)} · ${escapeHtml(item.host)}</strong>
            <span>${escapeHtml(item.stage || 'failed')} · ${escapeHtml(item.error || 'request failed')}</span>
          </div>
        `).join('');
        const switchText = panel.last_provider_switch
          ? `${new Date(panel.last_provider_switch.started_at).toLocaleTimeString()} · ${panel.last_provider_switch.from} → ${panel.last_provider_switch.to}`
          : '今日暂无切换';
        return `
          <div class="claude-card">
            <div class="claude-card-title">当前 CLI 进程链</div>
            <div class="process-topology">${topologyNodes}</div>
            <p class="provider-evidence">${escapeHtml(topology.evidence || '等待进程链证据')}</p>
          </div>
          <div class="claude-card">
            <div class="claude-card-title">今日概览</div>
            <div class="claude-metrics">
              ${renderMiniMetric('请求', fmt.format(panel.total_requests || 0))}
              ${renderMiniMetric('失败率', panel.failure_rate || '0.0%', (panel.failed_requests || 0) ? 'bad' : 'good')}
              ${renderMiniMetric('慢请求', fmt.format(panel.slow_requests || 0))}
            </div>
            <p class="provider-evidence">最近切换：${escapeHtml(switchText)}</p>
            ${panel.ignored_noise_requests ? `<p class="provider-evidence">已忽略无 Host 断连噪声 ${fmt.format(panel.ignored_noise_requests)} 条</p>` : ''}
          </div>
          <div class="claude-card wide">
            <div class="claude-card-title">服务商占比</div>
            <div class="claude-provider-list">${providerRows || '<div class="table-empty">暂无服务商数据</div>'}</div>
          </div>
          <div class="claude-card">
            <div class="claude-card-title">未知 Host 建议</div>
            ${unknownRows || '<div class="table-empty">暂无未识别模型服务商 Host</div>'}
          </div>
          <div class="claude-card">
            <div class="claude-card-title">最近错误</div>
            ${errorRows || '<div class="table-empty">暂无错误</div>'}
          </div>
          <div class="claude-card wide">
            <div class="claude-card-title">能力边界</div>
            <p class="provider-evidence">${escapeHtml(panel.capability_boundary || '')}</p>
          </div>
        `;
      };

      try {
        const res = await fetch(`/api/traffic-analytics?range=${analyticsRange}`, { cache: 'no-store' });
        if (!res.ok) throw new Error('API returns ' + res.status);
        const data = await res.json();
        claudeCodePanel.innerHTML = renderClaudeCodePanel(data.claude_code_panel);

        // 渲染软件活跃排行榜
        if (!data.software_ranking || !data.software_ranking.length) {
          softwareList.innerHTML = '<div class="table-empty">暂无软件流量数据</div>';
        } else {
          softwareList.innerHTML = data.software_ranking.map(item => `
            <div class="analytics-row">
              <div class="analytics-label">
                <span class="process-name">${escapeHtml(item.process)}</span>
                <span class="process-count">${item.count} 次 (${item.ratio})</span>
              </div>
              <div class="analytics-progress-bg">
                <div class="analytics-progress-bar software-bar" style="width: ${item.ratio}"></div>
              </div>
            </div>
          `).join('');
        }

        // 渲染厂商占比
        if (!data.provider_ranking || !data.provider_ranking.length) {
          providerList.innerHTML = '<div class="table-empty">暂无模型厂商流量数据</div>';
        } else {
          const claudeSummary = data.claude_code_summary || {};
          const summaryText = data.claude_code_provider_ranking && data.claude_code_provider_ranking.length
            ? `<div class="provider-evidence">Claude Code CLI: ${fmt.format(claudeSummary.total_requests || 0)} 次请求，模型服务商 ${fmt.format(claudeSummary.model_provider_requests || 0)} 次，未识别 ${fmt.format(claudeSummary.unknown_provider_requests || 0)} 次</div>`
            : '';
          const claudeRows = data.claude_code_provider_ranking && data.claude_code_provider_ranking.length
            ? `
              <div class="analytics-section-label">Claude Code CLI</div>
              ${summaryText}
              ${providerRows(data.claude_code_provider_ranking)}
              ${data.claude_code_unknown_hosts && data.claude_code_unknown_hosts.length
                ? `<div class="analytics-section-label">Claude Code 未识别 Host</div>${unknownHostRows(data.claude_code_unknown_hosts)}`
                : ''}
              <div class="analytics-section-label">全部流量</div>
            `
            : '';
          providerList.innerHTML = `${claudeRows}${providerRows(data.provider_ranking)}`;
        }

        // 更新 Meta 说明
        const timeDesc = analyticsRange === 'today' ? '今日 00:00 至今' : '最新 3 天长期趋势';
        text('analyticsSoftwareMeta', `${timeDesc}的活跃开发软件进程流量排行 (TOP 10)`);
        text('analyticsProviderMeta', `${timeDesc}的大模型厂商调用占比分布（已过滤噪点并归入 Other）`);

        analyticsLoaded = true;
      } catch (error) {
        console.error('Failed to load traffic analytics:', error);
        claudeCodePanel.innerHTML = `<div class="table-empty" style="color: #dc2626">加载失败: ${escapeHtml(error.message)}</div>`;
        softwareList.innerHTML = `<div class="table-empty" style="color: #dc2626">加载失败: ${escapeHtml(error.message)}</div>`;
        providerList.innerHTML = `<div class="table-empty" style="color: #dc2626">加载失败: ${escapeHtml(error.message)}</div>`;
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
      if (textValue.length >= 10) return '1.5rem';
      if (textValue.length >= 8) return '1.625rem';
      if (textValue.length >= 7) return '1.75rem';
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
    const setMetricParts = (id, parts) => {
      const element = document.getElementById(id);
      element.classList.add('metric-parts');
      element.innerHTML = parts.map(part => `<span>${escapeHtml(part)}</span>`).join('');
      element.title = parts.join(' · ');
    };
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
      host_failures: '失败率异常',
      disabled_service_host: '禁用服务仍请求'
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
      if (alert.kind === 'disabled_service_host') {
        return `${label} 禁用后仍请求 ${fmt.format(alert.value || 0)} 次`;
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
      if (alert.kind === 'disabled_service_host') {
        return `${alert.host || '未知 Host'} 命中已禁用服务 Host 列表，仍有 ${fmt.format(alert.value || 0)} 次请求`;
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
      if (alert.kind === 'disabled_service_host') {
        return '按来源筛选最近请求，定位残留进程后关闭对应插件或服务';
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
          if (alert.kind === 'disabled_service_host') return true;
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
      panel.classList.toggle('critical', critical);
      panel.classList.toggle('warning', warning);
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
        alerts.length
          ? `${alerts.length} 条 · 严重 ${counts.critical || 0} / 提醒 ${counts.warning || 0}`
          : '无告警';
    };
    const hostRows = hosts => {
      if (!hosts.length) {
        return '<div class="telemetry-empty">暂无实时链路数据</div>';
      }

      // 按服务商 (Provider) 进行智能分组归类
      const groups = new Map();
      hosts.forEach(host => {
        const meta = providerMeta(host.host);
        if (!groups.has(meta.key)) {
          groups.set(meta.key, {
            name: meta.name,
            color: meta.color,
            logo: meta.logo,
            hosts: []
          });
        }
        groups.get(meta.key).hosts.push(host);
      });

      // 对分组按照该组的总请求量进行排序，大请求量的服务商排在前面
      const sortedGroups = [...groups.values()].sort((a, b) => {
        const aSum = a.hosts.reduce((sum, h) => sum + (h.total_requests || 0), 0);
        const bSum = b.hosts.reduce((sum, h) => sum + (h.total_requests || 0), 0);
        return bSum - aSum;
      });

      return sortedGroups.map(group => {
        const groupTotalRequests = group.hosts.reduce((sum, h) => sum + (h.total_requests || 0), 0);

        // 渲染表头
        const tableHeaderHtml = `
          <div class="flat-table-row header">
            <div class="flat-col-host">ENDPOINT / ROUTE</div>
            <div class="flat-col-load">REQUESTS</div>
            <div class="flat-col-success">SUCCESS RATE</div>
            <div class="flat-col-latency">T3 CONNECT</div>
            <div class="flat-col-duration">T5 TOTAL</div>
          </div>
        `;

        const rowsHtml = group.hosts.map(host => {
          const routeInfo = Object.entries(host.routes || {})
            .map(([route, count]) => `${routeText(route)} ${fmt.format(count)}`)
            .join(' / ');
          const isWarning = host.health === 'warning';
          const isCritical = host.health === 'critical';
          const healthClass = isCritical ? 'critical' : (isWarning ? 'warning' : 'ok');

          // 成功率的纯数字警示
          const failureRate = host.failure_rate || 0;
          const successRate = 1 - failureRate;
          const successText = `${(successRate * 100).toFixed(2)}%`;
          const successClass = successRate < 0.95 ? 'bad' : 'good';

          // 建连耗时与持续耗时的警示
          const latency = host.average_connect_latency_ms || host.average_latency_ms || 0;
          const duration = host.average_duration_ms || 0;

          // 仅当异常时点亮相应的数字颜色
          const latencyClass = latency > 800 ? 'bad' : 'normal';
          const durationClass = duration > 2000 ? 'bad' : 'normal';

          return `
            <div class="flat-table-row data-row ${healthClass}">
              <!-- 1. 域名与路由 -->
              <div class="flat-col-host">
                <span class="flat-status-dot ${healthClass}"></span>
                <strong class="flat-host-name" title="${escapeHtml(host.host || '-')}">${escapeHtml(host.host || '-')}</strong>
                <span class="flat-route-info" title="${escapeHtml(routeInfo)}">${escapeHtml(routeInfo || '无路由记录')}</span>
              </div>

              <!-- 2. 请求负载 -->
              <div class="flat-col-load">
                <span class="flat-val-num">${fmt.format(host.total_requests)}</span>
              </div>

              <!-- 3. 健康成功率 -->
              <div class="flat-col-success ${successClass}">
                <span class="flat-val-num">${successText}</span>
              </div>

              <!-- 4. 平均建连时延 -->
              <div class="flat-col-latency ${latencyClass}">
                <span class="flat-val-num">${fmt.format(latency)}<span class="ms-unit">ms</span></span>
              </div>

              <!-- 5. T5总持续时延 -->
              <div class="flat-col-duration ${durationClass}">
                <span class="flat-val-num">${fmt.format(duration)}<span class="ms-unit">ms</span></span>
              </div>
            </div>
          `;
        }).join('');

        return `
          <div class="flat-provider-section">
            <!-- 极为优雅清秀的组头部 -->
            <div class="flat-provider-header">
              <span class="flat-provider-logo" style="color: ${escapeHtml(group.color)}">${escapeHtml(group.logo)}</span>
              <div class="flat-provider-details">
                <h3 class="flat-provider-title">${escapeHtml(group.name.toUpperCase())}</h3>
                <span class="flat-provider-summary">流量负载: ${fmt.format(groupTotalRequests)} 次请求 · ${group.hosts.length} 个活跃节点</span>
              </div>
            </div>

            <!-- 精准对齐的瓷感扁平网格数据表 -->
            <div class="flat-provider-table">
              ${tableHeaderHtml}
              ${rowsHtml}
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
    const clientSourceKey = client => {
      const label = client?.client_label || 'Unknown';
      const process = client?.client_process || 'unknown';
      return `${label} / ${process}`.toLowerCase();
    };
    const renderRequestSourceFilter = clients => {
      const select = document.getElementById('requestSourceFilter');
      if (!select) return;
      const current = requestSourceFilterValue;
      const options = ['<option value="">全部来源</option>'];
      (clients || []).forEach(client => {
        const label = clientLabel(client);
        const process = clientProcessText(client);
        const key = clientSourceKey(client);
        const selected = key === current ? ' selected' : '';
        options.push(`<option value="${escapeHtml(key)}"${selected}>${escapeHtml(label)} / ${escapeHtml(process)}</option>`);
      });
      select.innerHTML = options.join('');
      if (current && ![...select.options].some(option => option.value === current)) {
        requestSourceFilterValue = '';
        select.value = '';
      }
    };
    const requestClientText = request => {
      const label = request?.client_label || 'Unknown';
      const process = request?.client_process || '';
      const pid = request?.client_pid ? ` PID ${request.client_pid}` : '';
      const evidence = request?.client_evidence ? ` · ${request.client_evidence}` : '';
      const src = request?.client_port ? ` · SrcPort ${request.client_port}` : '';
      return process ? `${label} / ${process}${pid}${src}${evidence}` : `${label}${src}${evidence}`;
    };
    const requestProviderText = request => request?.provider || 'Unknown Provider';
    const requestProviderEvidence = request => request?.provider_evidence || '暂无识别依据';
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
              <span class="pill" title="${escapeHtml(requestProviderEvidence(request))}">${escapeHtml(requestProviderText(request))}</span>
              <span class="pill client-source" title="${escapeHtml(requestClientText(request))}">${escapeHtml(requestClientText(request))}</span>
              <span class="pill">建连 ${fmt.format(request.connect_latency_ms || 0)}ms</span>
              <span class="pill">持续 ${fmt.format(request.duration_ms || request.latency_ms || 0)}ms</span>
              <span class="request-time">${escapeHtml(when)}</span>
            </div>
          </div>
        `;
      }).join('');
    };
    const isRequestAnomaly = request => (
      (request.alertable !== false && !request.success) || request.slow
    );
    const anomalyRows = requests => {
      const anomalies = (requests || []).filter(isRequestAnomaly).slice(0, 5);
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
            <td title="${escapeHtml(requestProviderEvidence(request))}">${escapeHtml(requestProviderText(request))}</td>
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
          <thead><tr><th>时间</th><th>Host</th><th>服务商</th><th>Client</th><th>方法</th><th>路由</th><th>状态</th><th>耗时</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      `;
    };
    const anomalyTableRows = (requests, alerts) => {
      const requestAnomalies = (requests || []).filter(isRequestAnomaly).slice(0, 5);
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
      const proxyText = status.proxy_enabled === true ? 'ACTIVE' : 'DISABLED';
      const proxyClass = status.proxy_enabled === true ? 'good' : 'warn';
      const upstream = status.upstream_proxy || 'DIRECT CONNECTION';
      const whitelistLoadedAt = status.whitelist_loaded_at
        ? new Date(status.whitelist_loaded_at).toLocaleString()
        : 'NOT LOADED';

      const slots = [
        { label: 'PROXY STATE', val: proxyText, cls: proxyClass },
        { label: 'UPSTREAM GATEWAY', val: upstream, cls: 'gray' },
        { label: 'WHITELIST RULES', val: `${fmt.format(status.whitelist_count || 0)} ACTIVE`, cls: 'gray' },
        { label: 'METRIC SYNCHRONIZED', val: whitelistLoadedAt, cls: 'gray' }
      ];

      let slotsHtml = slots.map(slot => `
        <div class="flat-sled-cell ${slot.cls}">
          <span class="sled-label">${escapeHtml(slot.label)}</span>
          <strong class="sled-val">${escapeHtml(slot.val)}</strong>
        </div>
      `).join('');

      // 追加：智能路由分流状态面板
      let adaptiveSection = '';
      if (status.active_adaptive_routes && Object.keys(status.active_adaptive_routes).length > 0) {
        const routeItems = Object.entries(status.active_adaptive_routes).map(([host, route]) => {
          const isDemoted = route.status === 'DEMOTED';
          const badgeClass = isDemoted ? 'warn' : 'good';
          const label = isDemoted ? 'DEMOTED' : 'DIRECT';
          const desc = isDemoted ? 'recovery stream active' : 'routing optimization active';
          return `
            <div class="flat-adaptive-slot ${badgeClass}">
              <span class="adaptive-dot"></span>
              <strong class="adaptive-host-text" title="${escapeHtml(host)}">${escapeHtml(host)}</strong>
              <span class="adaptive-status-badge">${label}</span>
              <span class="adaptive-desc-text">${desc}</span>
              <span class="adaptive-expires">${route.expires_in}s</span>
            </div>
          `;
        }).join('');
        adaptiveSection = `
          <div class="flat-adaptive-board">
            <div class="adaptive-board-title">ACTIVE ADAPTIVE ROUTING STREAM</div>
            <div class="adaptive-mini-list">
              ${routeItems}
            </div>
          </div>
        `;
      }
      return `<div class="flat-slots-container">${slotsHtml}</div>` + adaptiveSection;
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
    const renderWhitelistEntries = (filterKeyword = '') => {
      const container = document.getElementById('whitelistEntries');
      const kw = String(filterKeyword).trim().toLowerCase();
      const filtered = kw
        ? whitelistEntriesState.filter(entry => entry.toLowerCase().includes(kw))
        : whitelistEntriesState;
      if (!filtered.length) {
        container.innerHTML = kw
          ? '<div class="table-empty">无匹配规则</div>'
          : '<div class="table-empty">白名单为空，保存后会自动创建 whitelist.txt</div>';
        return;
      }
      container.innerHTML = filtered.map(entry => `
        <div class="entry-item" style="display: flex; justify-content: space-between; align-items: center; padding: 6px 0; border-top: 1px solid #edf1f7;">
          <div style="display: flex; align-items: center; gap: 8px; min-width: 0;">
            <strong style="font-size: 13px; font-weight: 800; color: #07172f; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${escapeHtml(entry)}">${escapeHtml(entry)}</strong>
            <span class="status-badge" style="font-size: 10px; padding: 2px 6px; font-weight: normal; margin-top: 0; background: ${entry.includes('*') ? '#e8f5e9' : '#f3f4f6'}; color: ${entry.includes('*') ? '#2e7d32' : '#4b5563'}; border-radius: 4px;">${entry.includes('*') ? '通配' : '精确'}</span>
          </div>
          <button class="mini-danger" data-remove-whitelist="${escapeHtml(entry)}" style="padding: 3px 8px; font-size: 11px;">删除</button>
        </div>
      `).join('');
      container.querySelectorAll('[data-remove-whitelist]').forEach(button => {
        button.addEventListener('click', () => {
          whitelistEntriesState = whitelistEntriesState.filter(entry => entry !== button.dataset.removeWhitelist);
          renderWhitelistEntries(document.getElementById('whitelistInput').value);
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
    const blocklistMatchesHost = host => {
      const value = String(host || '').trim();
      if (!value) return false;
      return blocklistEntriesState.some(entry => {
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
      container.innerHTML = candidates.map(candidate => {
        const host = candidate.host || '';
        const isUpgrade = candidate.suggestion_type === 'wildcard_upgrade';
        const isCovered = candidate.is_covered;

        let actionButtons = '';
        if (isCovered || whitelistMatchesHost(host)) {
          actionButtons = `<button class="secondary-action" disabled style="padding: 5px 10px; font-size: 12px;">已直连</button>`;
        } else if (blocklistMatchesHost(host)) {
          actionButtons = `<button class="secondary-action" disabled style="padding: 5px 10px; font-size: 12px; color: #a855f7; border-color: #e9d5ff; background: #faf5ff;">已屏蔽</button>`;
        } else {
          actionButtons = `
            <div style="display: flex; gap: 4px;">
              <button class="mini-primary" data-add-whitelist-candidate="${escapeHtml(host)}" style="padding: 5px 10px; font-size: 12px;">直连</button>
              <button class="mini-danger" data-add-blocklist-candidate="${escapeHtml(host)}" style="padding: 5px 10px; font-size: 12px; background: #faf5ff; border: 1px solid #e9d5ff; color: #7c3aed;">屏蔽</button>
            </div>
          `;
        }

        const badgeHtml = isUpgrade
          ? `<span class="status-badge" style="font-size: 10px; padding: 2px 6px; background: #eff6ff; color: #3b82f6; font-weight: bold; margin-left: 6px; margin-top: 0; display: inline-block; vertical-align: middle; border-radius: 4px;">💡 智能通配建议</span>`
          : '';

        const reasonHtml = isUpgrade
          ? `<div style="font-size: 11px; color: #3b82f6; margin-top: 4px;">建议升级通配，自动合并已有的精确规则: <strong>${escapeHtml(candidate.reason)}</strong></div>`
          : `<span style="font-size: 11px;">代理 ${fmt.format(candidate.proxy_requests || 0)} 次 · 慢建连 ${fmt.format(candidate.slow_requests || 0)} · 平均 ${fmt.format(candidate.average_connect_latency_ms || 0)}ms</span>`;

        return `
          <div class="candidate-item" style="padding: 8px 0; border-top: 1px solid #edf1f7;">
            <div>
              <div style="display: flex; align-items: center; gap: 4px;">
                <strong style="font-size: 13px; font-weight: 800; color: #07172f;">${escapeHtml(host || '-')}</strong>
                ${badgeHtml}
              </div>
              ${reasonHtml}
            </div>
            ${actionButtons}
          </div>
        `;
      }).join('');

      container.querySelectorAll('[data-add-whitelist-candidate]').forEach(button => {
        button.addEventListener('click', async () => {
          if (button.disabled) return;
          await addWhitelistCandidate(button.dataset.addWhitelistCandidate, button);
        });
      });
      container.querySelectorAll('[data-add-blocklist-candidate]').forEach(button => {
        button.addEventListener('click', async () => {
          if (button.disabled) return;
          await addBlocklistCandidate(button.dataset.addBlocklistCandidate, button);
        });
      });
    };
    const refreshWhitelist = async () => {
      const response = await fetch('/api/whitelist', { cache: 'no-store' });
      const data = await response.json();
      whitelistEntriesState = data.entries || [];
      whitelistEntriesBackupState = [...whitelistEntriesState];
      const loadedAt = data.loaded_at ? new Date(data.loaded_at).toLocaleString() : '尚未加载';
      text('whitelistMeta', `${data.path || 'whitelist.txt'} · ${fmt.format(data.count || 0)} 条 · ${loadedAt}`);
      renderWhitelistEntries(document.getElementById('whitelistInput').value);
      renderWhitelistCandidates(data.candidates || []);
    };
    const addWhitelistEntry = entry => {
      const value = String(entry || '').trim();
      if (!value) {
        return { ok: false, reason: 'empty' };
      }
      if (whitelistEntriesState.includes(value)) {
        return { ok: false, reason: 'exists' };
      }
      whitelistEntriesState = [...whitelistEntriesState, value].sort();
      document.getElementById('whitelistInput').value = '';
      renderWhitelistEntries();
      return { ok: true };
    };
    const saveWhitelistEntries = async (successMessage = '白名单保存成功') => {
      // 1. 自动帮用户添加输入框中的有效未添加规则
      const inputEl = document.getElementById('whitelistInput');
      const inputValue = String(inputEl.value || '').trim();
      let hasAutoAdded = false;
      if (inputValue) {
        if (!whitelistEntriesState.includes(inputValue)) {
          const addRes = addWhitelistEntry(inputValue);
          if (addRes && addRes.ok) {
            hasAutoAdded = true;
          }
        }
      }

      // 2. 脏数据/无变更检查
      const isDirty = JSON.stringify(whitelistEntriesState) !== JSON.stringify(whitelistEntriesBackupState);
      if (!isDirty && !hasAutoAdded) {
        showWhitelistFeedback('配置未发生变更，无需保存', 'info');
        return;
      }

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
      whitelistEntriesBackupState = [...whitelistEntriesState];
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
    const showBlocklistFeedback = (message, type = 'info') => {
      const feedback = document.getElementById('blocklistFeedback');
      feedback.textContent = message;
      feedback.className = `feedback show ${type}`;
    };
    const renderBlocklistEntries = (filterKeyword = '') => {
      const container = document.getElementById('blocklistEntries');
      const kw = String(filterKeyword).trim().toLowerCase();
      const filtered = kw
        ? blocklistEntriesState.filter(entry => entry.toLowerCase().includes(kw))
        : blocklistEntriesState;
      if (!filtered.length) {
        container.innerHTML = kw
          ? '<div class="table-empty">无匹配规则</div>'
          : '<div class="table-empty">屏蔽名单为空，保存后会自动创建 blocklist.txt</div>';
        return;
      }
      container.innerHTML = filtered.map(entry => `
        <div class="entry-item" style="display: flex; justify-content: space-between; align-items: center; padding: 6px 0; border-top: 1px solid #edf1f7;">
          <div style="display: flex; align-items: center; gap: 8px; min-width: 0;">
            <strong style="font-size: 13px; font-weight: 800; color: #07172f; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="${escapeHtml(entry)}">${escapeHtml(entry)}</strong>
            <span class="status-badge" style="font-size: 10px; padding: 2px 6px; font-weight: normal; margin-top: 0; background: ${entry.includes('*') ? '#e8f5e9' : '#f3f4f6'}; color: ${entry.includes('*') ? '#2e7d32' : '#4b5563'}; border-radius: 4px;">${entry.includes('*') ? '通配' : '精确'}</span>
          </div>
          <button class="mini-danger" data-remove-blocklist="${escapeHtml(entry)}" style="padding: 3px 8px; font-size: 11px;">删除</button>
        </div>
      `).join('');
      container.querySelectorAll('[data-remove-blocklist]').forEach(button => {
        button.addEventListener('click', () => {
          blocklistEntriesState = blocklistEntriesState.filter(entry => entry !== button.dataset.removeBlocklist);
          renderBlocklistEntries(document.getElementById('blocklistInput').value);
        });
      });
    };
    const refreshBlocklist = async () => {
      const response = await fetch('/api/blocklist', { cache: 'no-store' });
      const data = await response.json();
      blocklistEntriesState = data.entries || [];
      blocklistEntriesBackupState = [...blocklistEntriesState];
      const loadedAt = data.loaded_at ? new Date(data.loaded_at).toLocaleString() : '尚未加载';
      text('blocklistMeta', `${data.path || 'blocklist.txt'} · ${fmt.format(data.count || 0)} 条 · ${loadedAt}`);
      renderBlocklistEntries(document.getElementById('blocklistInput').value);
    };
    const addBlocklistCandidate = async (entry, button) => {
      const value = String(entry || '').trim();
      if (!value) return;
      if (blocklistEntriesState.includes(value)) {
        showBlocklistFeedback(`${value} 已在屏蔽名单中`, 'info');
        if (button) button.textContent = '已屏蔽';
        return;
      }
      blocklistEntriesState = [...blocklistEntriesState, value].sort();
      renderBlocklistEntries(document.getElementById('blocklistInput').value);
      if (button) {
        button.disabled = true;
        button.textContent = '屏蔽中';
      }
      try {
        await saveBlocklistEntries(`${value} 已加入屏蔽名单并保存`);
        if (button) {
          button.disabled = true;
          button.textContent = '已屏蔽';
          button.style.color = '#a855f7';
          button.style.borderColor = '#e9d5ff';
          button.style.background = '#faf5ff';
        }
      } catch (error) {
        showBlocklistFeedback(`${value} 屏蔽失败：${error.message || error}`, 'error');
        blocklistEntriesState = blocklistEntriesState.filter(item => item !== value);
        renderBlocklistEntries(document.getElementById('blocklistInput').value);
        if (button) {
          button.disabled = false;
          button.textContent = '屏蔽';
        }
      }
    };
    const addBlocklistEntry = entry => {
      const value = String(entry || '').trim();
      if (!value) {
        return { ok: false, reason: 'empty' };
      }
      if (blocklistEntriesState.includes(value)) {
        return { ok: false, reason: 'exists' };
      }
      blocklistEntriesState = [...blocklistEntriesState, value].sort();
      document.getElementById('blocklistInput').value = '';
      renderBlocklistEntries();
      return { ok: true };
    };
    const saveBlocklistEntries = async (successMessage = '屏蔽名单保存成功') => {
      // 1. 自动帮用户添加输入框中的有效未添加规则
      const inputEl = document.getElementById('blocklistInput');
      const inputValue = String(inputEl.value || '').trim();
      let hasAutoAdded = false;
      if (inputValue) {
        if (!blocklistEntriesState.includes(inputValue)) {
          const addRes = addBlocklistEntry(inputValue);
          if (addRes && addRes.ok) {
            hasAutoAdded = true;
          }
        }
      }

      // 2. 脏数据/无变更检查
      const isDirty = JSON.stringify(blocklistEntriesState) !== JSON.stringify(blocklistEntriesBackupState);
      if (!isDirty && !hasAutoAdded) {
        showBlocklistFeedback('配置未发生变更，无需保存', 'info');
        return;
      }

      const response = await fetch('/api/blocklist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ entries: blocklistEntriesState }),
      });
      const data = await response.json();
      if (!response.ok) {
        showBlocklistFeedback(`保存失败：${data.error || response.status}`, 'error');
        throw new Error(data.error || `HTTP ${response.status}`);
      }
      blocklistEntriesState = data.entries || [];
      blocklistEntriesBackupState = [...blocklistEntriesState];
      const loadedAt = data.loaded_at ? new Date(data.loaded_at).toLocaleString() : '刚刚';
      text('blocklistMeta', `${data.path || 'blocklist.txt'} · ${fmt.format(data.count || 0)} 条 · ${loadedAt}`);
      showBlocklistFeedback(successMessage, 'ok');
      renderBlocklistEntries();
      await refreshBlocklist();
    };
    const getCheckIcon = key => {
      const icons = {
        proxy_port: `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2" width="20" height="8" rx="2" ry="2"></rect><rect x="2" y="14" width="20" height="8" rx="2" ry="2"></rect><line x1="6" y1="6" x2="6.01" y2="6"></line><line x1="6" y1="18" x2="6.01" y2="18"></line></svg>`,
        dashboard_port: `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><line x1="9" y1="3" x2="9" y2="21"></line></svg>`,
        python: `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 18l6-6-6-6M8 6L2 12l6 6"></path></svg>`,
        token_capture: `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h16"></path><path d="M4 12h16"></path><path d="M4 17h10"></path><circle cx="18" cy="17" r="2"></circle></svg>`,
        whitelist: `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>`,
        blocklist: `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"></line></svg>`,
        upstream: `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"></path></svg>`,
        net_baidu: `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z"></path></svg>`,
        net_anthropic: `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2zm18 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"></path></svg>`,
        net_openai: `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><path d="M8 12h8M12 8v8"></path></svg>`,
        database: `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"></ellipse><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"></path><path d="M3 12c0 1.66 4 3 9 3s9-1.34 9-3"></path></svg>`,
        env_proxy: `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>`,
        resources: `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"></line><line x1="12" y1="20" x2="12" y2="4"></line><line x1="6" y1="20" x2="6" y2="14"></line></svg>`
      };
      return icons[key] || `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>`;
    };

    const renderDoctor = doctor => {
      const checks = (doctor.checks || [])
        .filter(check => check.key !== 'provider_health');
      const totalCount = checks.length;
      const okCount = checks.filter(check => check.status === 'ok').length;
      const warningCount = checks.filter(check => check.status === 'warning').length;
      const errorCount = checks.filter(check => check.status === 'error').length;

      // 1. 计算系统健康分
      let score = 100;
      if (totalCount > 0) {
        score = Math.round(((okCount * 100) + (warningCount * 70) + (errorCount * 0)) / totalCount);
      }

      // 2. 更新 SVG 环形进度条和看板头部 (周长为 2 * Math.PI * 40 ≈ 251.2)
      const ring = document.getElementById('doctorHealthRing');
      if (ring) {
        const offset = 251.2 * (1 - score / 100);
        ring.style.strokeDashoffset = offset;

        // 动态配色渐变
        if (score >= 95) {
          ring.style.stroke = 'url(#scoreGrad)';
        } else if (score >= 80) {
          ring.style.stroke = '#f59e0b';
        } else {
          ring.style.stroke = '#ef4444';
        }
      }

      const scoreVal = document.getElementById('doctorHealthScore');
      if (scoreVal) {
        scoreVal.innerText = score;
      }

      // 智能生成诊断评价文案
      const descEl = document.getElementById('doctorHealthDesc');
      if (descEl) {
        if (score >= 95) {
          descEl.innerHTML = '✨ <strong>运行完美</strong>：所有路由链路、核心安全资产及环境参数表现优异，代理正处于巅峰工作状态！';
        } else if (score >= 80) {
          descEl.innerHTML = '⚠️ <strong>需关注</strong>：系统整体运行正常，但部分链路或本地资产检测到隐患，建议查阅相应警告项。';
        } else {
          descEl.innerHTML = '🚨 <strong>存在故障</strong>：检测到核心网关端口、安全配置文件或主路由脱轨，系统存在安全或功能缺陷！';
        }
      }

      const generatedAt = doctor.generated_at ? new Date(doctor.generated_at).toLocaleString() : '刚刚';
      text('doctorMeta', `${okCount}/${totalCount} 项通过 · 报告生成时间：${generatedAt}`);

      window.lastDoctorChecks = checks;

      // 3. 渲染单个卡片网格
      const grid = document.getElementById('doctorGrid');
      if (!grid) return;

      if (!checks.length) {
        grid.innerHTML = '<div class="table-empty">暂无自检结果</div>';
        return;
      }

      grid.innerHTML = checks.map(check => {
        const status = check.status || 'warning';
        let dotHtml = '';
        let badgeHtml = '';
        if (status === 'ok') {
          dotHtml = `<span class="modern-status-dot dot-ok"></span>`;
          badgeHtml = `<span class="single-card-badge badge-ok">正常</span>`;
        } else if (status === 'warning') {
          dotHtml = `<span class="modern-status-dot dot-warn"><span class="pulse-ring"></span></span>`;
          badgeHtml = `<span class="single-card-badge badge-warn">关注</span>`;
        } else {
          dotHtml = `<span class="modern-status-dot dot-error"><span class="pulse-ring-fast"></span></span>`;
          badgeHtml = `<span class="single-card-badge badge-error">异常</span>`;
        }

        const icon = getCheckIcon(check.key);

        return `
          <div class="doctor-single-card status-${status}" data-check-key="${check.key}" style="cursor: pointer;">
            <div class="card-top-row">
              <div class="status-indicator-group">
                ${dotHtml}
                <span class="card-icon-mini">${icon}</span>
              </div>
              ${badgeHtml}
            </div>
            <h4 class="card-item-title">${escapeHtml(check.label || check.key || '-')}</h4>
            <p class="card-item-detail">${escapeHtml(check.detail || '-')}</p>
            ${check.status === 'ok' ? '' : `
              <div class="card-item-fix-box">
                <span class="fix-pin">📌</span>
                <span class="fix-text">${escapeHtml(check.fix || '请检查配置')}</span>
              </div>
            `}
          </div>
        `;
      }).join('');
    };

    // 二级卡片弹出详情模态框逻辑
    const showDoctorDetailModal = check => {
      const overlay = document.createElement('div');
      overlay.className = 'doctor-modal-overlay';

      const status = check.status || 'warning';
      let dotHtml = '';
      let badgeHtml = '';
      let statusLabelText = '';
      if (status === 'ok') {
        dotHtml = `<span class="modern-status-dot dot-ok"></span>`;
        badgeHtml = `<span class="single-card-badge badge-ok">正常</span>`;
        statusLabelText = '该组件健康度优良，工作指标完全符合预期。';
      } else if (status === 'warning') {
        dotHtml = `<span class="modern-status-dot dot-warn"><span class="pulse-ring"></span></span>`;
        badgeHtml = `<span class="single-card-badge badge-warn">关注</span>`;
        statusLabelText = '该组件检测到潜在隐患或配置微调建议，请予以关注。';
      } else {
        dotHtml = `<span class="modern-status-dot dot-error"><span class="pulse-ring-fast"></span></span>`;
        badgeHtml = `<span class="single-card-badge badge-error">异常</span>`;
        statusLabelText = '核心指标异常或连接脱线！可能导致代理服务部分功能受阻。';
      }

      const icon = getCheckIcon(check.key);

      // --- 核心二级卡片专属业务模块划分 ---
      let customSpecsHtml = '';

      // 1. 白名单/屏蔽名单配置规则快照与路径提取
      if (check.key === 'whitelist' || check.key === 'blocklist') {
        const isWhite = check.key === 'whitelist';
        const rules = isWhite ? (whitelistEntriesState || []) : (blocklistEntriesState || []);

        let rulesPreviewHtml = '';
        if (rules.length === 0) {
          rulesPreviewHtml = '<div class="modal-empty-tip">📭 当前尚未加载任何规则过滤条目</div>';
        } else {
          // 只展示前 20 条，溢出显示统计胶囊
          const previewRules = rules.slice(0, 20);
          const tagsHtml = previewRules.map(r => `<span class="modal-rule-tag">${escapeHtml(r)}</span>`).join('');
          const moreCount = rules.length - previewRules.length;
          rulesPreviewHtml = `
            <div class="modal-rules-grid">
              ${tagsHtml}
              ${moreCount > 0 ? `<span class="modal-rule-tag tag-more">等共 ${rules.length} 条规则...</span>` : ''}
            </div>
          `;
        }

        // 提取物理路径一键复制
        const pathMatched = check.detail.match(/([a-zA-Z]:\\[^\s,，·]+)/);
        const pathStr = pathMatched ? pathMatched[1] : '';

        customSpecsHtml = `
          <div class="modal-specs-block">
            <div class="specs-header-row">
              <span class="specs-block-title">📜 过滤规则快照 (前 20 条预览)</span>
              <span class="specs-block-meta">共 ${rules.length} 条已加载</span>
            </div>
            ${rulesPreviewHtml}
            ${pathStr ? `
              <div class="specs-action-row">
                <span class="specs-path-text select-text" title="${escapeHtml(pathStr)}">${escapeHtml(pathStr)}</span>
                <button class="mini-primary copy-path-btn" id="modalCopyPathBtn" data-path="${escapeHtml(pathStr)}">
                  📋 复制绝对路径
                </button>
              </div>
            ` : ''}
          </div>
        `;
      }
      // 2. 路由链路高保真测速探针
      else if (['upstream', 'net_baidu', 'net_anthropic', 'net_openai'].includes(check.key)) {
        // 匹配出延迟数字
        const latencyMatched = check.detail.match(/(\d+)\s*ms/);
        const latencyVal = latencyMatched ? `${latencyMatched[1]}ms` : check.detail;

        customSpecsHtml = `
          <div class="modal-specs-block">
            <span class="specs-block-title">⚡ 链路即时高频重测 (RTT Probe)</span>
            <div class="latency-telemetry-row">
              <div class="latency-number-box">
                <span class="modal-latency-number select-text">${escapeHtml(latencyVal)}</span>
                <span class="modal-latency-unit">当前时延</span>
              </div>
              <button class="primary-action ping-test-btn" id="modalPingTestBtn">
                ⚡ 立即重新测速
              </button>
            </div>
          </div>
        `;
      }
      // 3. SQLite 数据库物理健康度拆分
      else if (check.key === 'database') {
        const data = check.data || {};
        const action = (check.actions || []).find(item => item.id === 'prune_proxy_stats');
        const sizeStr = Number.isFinite(data.size_mb) ? `${data.size_mb} MB` : '未知';
        const ioStr = Number.isFinite(data.io_ms) ? `${data.io_ms}ms` : '正常';
        const countStr = Number.isFinite(data.total_requests) ? String(data.total_requests) : '0';
        const retainedStr = Number.isFinite(data.retained_requests_7d) ? String(data.retained_requests_7d) : '0';
        const prunableStr = Number.isFinite(data.prunable_requests_7d) ? String(data.prunable_requests_7d) : '0';
        const integrityStr = data.integrity || 'unknown';

        customSpecsHtml = `
          <div class="modal-specs-block">
            <span class="specs-block-title">💾 SQLite 遥测数据库物理参数</span>
            <div class="telemetry-table">
              <div class="telemetry-row">
                <span class="tel-label">数据库体积</span>
                <span class="tel-value select-text">${escapeHtml(sizeStr)}</span>
              </div>
              <div class="telemetry-row">
                <span class="tel-label">I/O 物理响应时延</span>
                <span class="tel-value select-text">${escapeHtml(ioStr)}</span>
              </div>
              <div class="telemetry-row">
                <span class="tel-label">累计入库审计请求笔数</span>
                <span class="tel-value select-text">${escapeHtml(countStr)} 条</span>
              </div>
              <div class="telemetry-row">
                <span class="tel-label">逻辑结构健康度 (PRAGMA)</span>
                <span class="tel-value select-text text-green" style="color:#10b981;font-weight:900;">${escapeHtml(integrityStr)}</span>
              </div>
              <div class="telemetry-row">
                <span class="tel-label">最近 7 天保留量</span>
                <span class="tel-value select-text">${escapeHtml(retainedStr)} 条</span>
              </div>
              <div class="telemetry-row">
                <span class="tel-label">可治理历史量</span>
                <span class="tel-value select-text">${escapeHtml(prunableStr)} 条</span>
              </div>
            </div>
            ${action ? `
              <div class="specs-action-row">
                <span class="specs-path-text select-text">保留最近 7 天并执行 VACUUM 压缩</span>
                <button class="mini-primary doctor-prune-db-btn" id="modalPruneProxyStatsBtn" data-url="${escapeHtml(action.url || '/api/prune-proxy-stats')}">
                  ${escapeHtml(action.label || '保留最近7天并压缩')}
                </button>
              </div>
              <div class="modal-empty-tip" id="modalPruneProxyStatsResult" hidden></div>
            ` : ''}
          </div>
        `;
      }
      // 4. Python 协程与系统资源利用率
      else if (check.key === 'resources') {
        const timeMatched = check.detail.match(/在线时长\s*(\d+)\s*秒/i);
        const ramMatched = check.detail.match(/内存占用\s*([\d\.]+)\s*(MB|KB|B)/i);
        const coMatched = check.detail.match(/活跃协程\s*(\d+)\s*个/i);

        const timeStr = timeMatched ? `${timeMatched[1]} 秒` : '刚刚启动';
        const ramStr = ramMatched ? ramMatched[0] : '未知';
        const coStr = coMatched ? coMatched[1] : '0';

        // 计算协程条比例
        const coCount = coMatched ? parseInt(coMatched[1]) : 0;
        const coPercent = Math.min(100, Math.max(10, Math.round((coCount / 100) * 100)));

        customSpecsHtml = `
          <div class="modal-specs-block">
            <span class="specs-block-title">⚙️ Python 运行时 & 系统资源利用率</span>
            <div class="telemetry-table">
              <div class="telemetry-row">
                <span class="tel-label">代理进程在线时长</span>
                <span class="tel-value select-text">${escapeHtml(timeStr)}</span>
              </div>
              <div class="telemetry-row">
                <span class="tel-label">宿主机物理内存开销 (WorkSet)</span>
                <span class="tel-value select-text">${escapeHtml(ramStr)}</span>
              </div>
              <div class="telemetry-row">
                <span class="tel-label">活跃 asyncio 异步协程数</span>
                <span class="tel-value select-text">${escapeHtml(coStr)} 个</span>
              </div>
            </div>
            <div class="progress-bar-container">
              <div class="progress-bar-label">
                <span>协程池消耗配额 (当前建议上限 100)</span>
                <span style="font-weight:900;color:var(--blue);">${coCount}%</span>
              </div>
              <div class="progress-bar-track">
                <div class="progress-bar-fill" style="width: ${coPercent}%;"></div>
              </div>
            </div>
          </div>
        `;
      }
      // 5. Proxy 与 Dashboard 端口的极速服务绑定诊断
      else if (check.key === 'proxy_port' || check.key === 'dashboard_port') {
        const isProxy = check.key === 'proxy_port';
        const port = isProxy ? '8889' : '8890';
        const serviceUrl = `http://127.0.0.1:${port}`;

        // 匹配连接延迟
        const connMatched = check.detail.match(/连接\s*(\d+)\s*ms/i);
        const connStr = connMatched ? `${connMatched[1]}ms` : '未响应';

        customSpecsHtml = `
          <div class="modal-specs-block">
            <span class="specs-block-title">🌐 ${isProxy ? 'HTTP 代理代理服务端口' : 'HTML 极客管理面板端口'}</span>
            <div class="telemetry-table">
              <div class="telemetry-row">
                <span class="tel-label">内网监听服务地址</span>
                <span class="tel-value select-text">${escapeHtml(serviceUrl)}</span>
              </div>
              <div class="telemetry-row">
                <span class="tel-label">端口监听协议</span>
                <span class="tel-value select-text">TCP (IPv4)</span>
              </div>
              <div class="telemetry-row">
                <span class="tel-label">本端建连握手时延</span>
                <span class="tel-value select-text" style="color:#10b981;font-weight:900;">⚡ ${escapeHtml(connStr)}</span>
              </div>
            </div>
            <div class="specs-action-row">
              <span class="specs-path-text select-text" title="${escapeHtml(serviceUrl)}">${escapeHtml(serviceUrl)}</span>
              <button class="mini-primary copy-path-btn" id="modalCopyUrlBtn" data-url="${escapeHtml(serviceUrl)}">
                📋 复制服务 URL
              </button>
            </div>
          </div>
        `;
      }
      // 6. Python 运行环境及编译器绝对路径提取
      else if (check.key === 'python') {
        const pyPath = check.detail;

        customSpecsHtml = `
          <div class="modal-specs-block">
            <span class="specs-block-title">🐍 Python 运行时编译器环境</span>
            <div class="telemetry-table">
              <div class="telemetry-row">
                <span class="tel-label">物理执行文件路径</span>
                <span class="tel-value select-text" style="word-break: break-all; font-size: 11px;">${escapeHtml(pyPath)}</span>
              </div>
              <div class="telemetry-row">
                <span class="tel-label">运行架构</span>
                <span class="tel-value select-text">${navigator.userAgent.includes('Win64') || navigator.userAgent.includes('x64') ? 'AMD64 (64-Bit)' : 'x86 (32-Bit)'}</span>
              </div>
            </div>
            <div class="specs-action-row">
              <span class="specs-path-text select-text" title="${escapeHtml(pyPath)}">${escapeHtml(pyPath)}</span>
              <button class="mini-primary copy-path-btn" id="modalCopyPathBtn" data-path="${escapeHtml(pyPath)}">
                📋 复制物理路径
              </button>
            </div>
          </div>
        `;
      }
      // 7. MITM Token Capture 文件检测
      else if (check.key === 'token_capture') {
        const capture = check.data || {};
        const captureDir = capture.capture_dir || '';
        const fileCount = String(capture.file_count || 0);
        const latestFile = capture.latest_file || '暂无';
        const latestModified = capture.latest_modified
          ? new Date(capture.latest_modified).toLocaleString()
          : '暂无';
        const latestSize = capture.latest_size_kb ? `${capture.latest_size_kb} KB` : '0 KB';

        customSpecsHtml = `
          <div class="modal-specs-block">
            <span class="specs-block-title">MITM Token Capture 状态</span>
            <div class="telemetry-table">
              <div class="telemetry-row">
                <span class="tel-label">捕获目录</span>
                <span class="tel-value select-text" style="word-break: break-all; font-size: 11px;">${escapeHtml(captureDir || '未检测到目录')}</span>
              </div>
              <div class="telemetry-row">
                <span class="tel-label">捕获文件</span>
                <span class="tel-value select-text" style="font-weight:900;color:var(--blue);">${escapeHtml(fileCount)} 个文件</span>
              </div>
              <div class="telemetry-row">
                <span class="tel-label">最新文件</span>
                <span class="tel-value select-text">${escapeHtml(latestFile)} · ${escapeHtml(latestSize)}</span>
              </div>
              <div class="telemetry-row">
                <span class="tel-label">最近写入</span>
                <span class="tel-value select-text">${escapeHtml(latestModified)}</span>
              </div>
            </div>
            ${captureDir ? `
              <div class="specs-action-row">
                <span class="specs-path-text select-text" title="${escapeHtml(captureDir)}">${escapeHtml(captureDir)}</span>
                <button class="mini-primary copy-path-btn" id="modalCopyPathBtn" data-path="${escapeHtml(captureDir)}">
                  📋 复制绝对路径
                </button>
              </div>
            ` : ''}
          </div>
        `;
      }
      // 8. 系统代理冲突对齐矩阵
      else if (check.key === 'env_proxy') {
        const regEnabled = check.detail.includes('注册表全局代理: 已启用');
        const regServerMatched = check.detail.match(/地址:\s*([^\s\)]+)/);
        const regServer = regServerMatched ? regServerMatched[1] : '';

        const httpMatched = check.detail.match(/HTTP_PROXY:\s*([^\s|]+)/);
        const httpVal = httpMatched ? httpMatched[1] : '未配置';

        const httpsMatched = check.detail.match(/HTTPS_PROXY:\s*([^\s|]+)/);
        const httpsVal = httpsMatched ? httpsMatched[1] : '未配置';

        const hasConflict = check.detail.includes('⚠️ 潜在冲突') || check.detail.includes('环路风险');

        customSpecsHtml = `
          <div class="modal-specs-block">
            <span class="specs-block-title">🚦 Windows 注册表与环境变量冲突对照表</span>
            <div class="telemetry-table">
              <div class="telemetry-row">
                <span class="tel-label">Windows 注册表全局代理</span>
                <span class="tel-value select-text">
                  ${regEnabled ? `🔴 已开启 (${escapeHtml(regServer)})` : '🟢 已关闭'}
                </span>
              </div>
              <div class="telemetry-row">
                <span class="tel-label">HTTP_PROXY 环境变量</span>
                <span class="tel-value select-text ${httpVal !== '未配置' ? 'text-warn' : ''}">${escapeHtml(httpVal)}</span>
              </div>
              <div class="telemetry-row">
                <span class="tel-label">HTTPS_PROXY 环境变量</span>
                <span class="tel-value select-text ${httpsVal !== '未配置' ? 'text-warn' : ''}">${escapeHtml(httpsVal)}</span>
              </div>
              <div class="telemetry-row">
                <span class="tel-label">物理环路风险评估</span>
                <span class="tel-value select-text" style="font-weight:900;color:${hasConflict ? '#ef4444' : '#10b981'};">
                  ${hasConflict ? '❌ 检测到潜在冲突/死循环风险' : '🟢 未检测到冲突环路'}
                </span>
              </div>
            </div>
          </div>
        `;
      }

      overlay.innerHTML = `
        <div class="doctor-modal-card">
          <div class="modal-header">
            <div class="modal-title-group">
              <div class="status-indicator-group">
                ${dotHtml}
                <span class="card-icon-mini text-active">${icon}</span>
              </div>
              <h3>${escapeHtml(check.label || check.key || '-')}</h3>
            </div>
            <button class="modal-close-btn" id="modalCloseX">
              <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <line x1="18" y1="6" x2="6" y2="18"></line>
                <line x1="6" y1="6" x2="18" y2="18"></line>
              </svg>
            </button>
          </div>

          <div class="modal-body">
            <div class="detail-label-row">
              <span class="detail-sec-title">诊断详情与当前指标</span>
              ${badgeHtml}
            </div>
            <div class="detail-text-area select-text">
              ${escapeHtml(check.detail || '暂无诊断数据详情。')}
            </div>

            <!-- 插入高逼格二级自举技术 specs 块 -->
            ${customSpecsHtml}

            <div class="detail-summary select-text">
              <strong>💡 状态评估：</strong> ${statusLabelText}
            </div>

            ${check.status === 'ok' ? '' : `
              <div class="modal-fix-section select-text">
                <div class="fix-header">
                  <span>📌</span>
                  <strong>官方修复建议及故障排查计划</strong>
                </div>
                <div class="fix-body">
                  ${escapeHtml(check.fix || '暂无详细排查建议，请检查对应本地文件的读写权限及语法格式。')}
                </div>
              </div>
            `}
          </div>

          <div class="modal-footer">
            <button class="primary-action close-action-btn" id="modalCloseOk">我知道了</button>
          </div>
        </div>
      `;

      document.body.appendChild(overlay);
      document.body.style.overflow = 'hidden';

      // --- 高级事件绑定 ---
      // 1. 复制物理路径或 URL 逻辑
      overlay.querySelectorAll('.copy-path-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          const path = btn.getAttribute('data-path');
          const url = btn.getAttribute('data-url');
          const textToCopy = path || url;
          if (textToCopy) {
            navigator.clipboard.writeText(textToCopy).then(() => {
              const originalText = btn.innerHTML;
              btn.innerHTML = '✅ 复制成功！';
              setTimeout(() => btn.innerHTML = originalText, 1500);
            });
          }
        });
      });

      // 2. 探针即时重测拨测逻辑
      const testBtn = overlay.querySelector('#modalPingTestBtn');
      if (testBtn) {
        testBtn.addEventListener('click', async () => {
          testBtn.disabled = true;
          const originalText = testBtn.innerHTML;
          testBtn.innerHTML = `<span class="loading-spin" style="display:inline-block;margin-right:6px;">⚡</span>拨测中...`;

          const latencyNumEl = overlay.querySelector('.modal-latency-number');
          if (latencyNumEl) latencyNumEl.innerText = 'PINGING...';

          try {
            const res = await fetch('/api/doctor', { cache: 'no-store' });
            const data = await res.json();
            const freshChecks = (data.checks || []).filter(c => c.key !== 'provider_health');
            const freshCheck = freshChecks.find(c => c.key === check.key);
            if (freshCheck) {
              const latencyMatched = freshCheck.detail.match(/(\d+)\s*ms/);
              if (latencyMatched && latencyNumEl) {
                latencyNumEl.innerText = `${latencyMatched[1]}ms`;
              } else if (latencyNumEl) {
                latencyNumEl.innerText = freshCheck.detail;
              }
              // 同步更新外部诊断大页面！
              renderDoctor(data);
            }
          } catch (e) {
            if (latencyNumEl) latencyNumEl.innerText = 'ERROR';
          } finally {
            testBtn.disabled = false;
            testBtn.innerHTML = originalText;
          }
        });
      }

      const pruneBtn = overlay.querySelector('#modalPruneProxyStatsBtn');
      if (pruneBtn) {
        pruneBtn.addEventListener('click', async () => {
          pruneBtn.disabled = true;
          const originalText = pruneBtn.innerHTML;
          const resultEl = overlay.querySelector('#modalPruneProxyStatsResult');
          pruneBtn.innerHTML = '治理中...';
          if (resultEl) {
            resultEl.hidden = false;
            resultEl.textContent = '正在删除 7 天前统计并压缩数据库...';
          }

          try {
            const url = pruneBtn.getAttribute('data-url') || '/api/prune-proxy-stats';
            const res = await fetch(url, { method: 'POST', cache: 'no-store' });
            const data = await res.json();
            if (!res.ok || data.error) {
              throw new Error(data.error || `HTTP ${res.status}`);
            }
            const beforeMb = (data.size_before_bytes / 1024 / 1024).toFixed(2);
            const afterMb = (data.size_after_bytes / 1024 / 1024).toFixed(2);
            if (resultEl) {
              resultEl.textContent = `已删除 ${data.deleted_count} 条，剩余 ${data.remaining_count} 条，体积 ${beforeMb} MB -> ${afterMb} MB`;
            }
            await Promise.allSettled([
              refresh(),
              refreshDoctor({ force: true }),
            ]);
          } catch (error) {
            if (resultEl) {
              resultEl.textContent = `治理失败：${error.message || error}`;
            }
          } finally {
            pruneBtn.disabled = false;
            pruneBtn.innerHTML = originalText;
          }
        });
      }

      const closeModal = () => {
        overlay.classList.add('modal-fade-out');
        const modalCard = overlay.querySelector('.doctor-modal-card');
        if (modalCard) modalCard.classList.add('modal-zoom-out');

        setTimeout(() => {
          overlay.remove();
          if (!document.querySelector('.doctor-modal-overlay')) {
            document.body.style.overflow = '';
          }
        }, 220);
      };

      overlay.addEventListener('click', event => {
        if (event.target === overlay) {
          closeModal();
        }
      });

      overlay.querySelector('#modalCloseX').addEventListener('click', closeModal);
      overlay.querySelector('#modalCloseOk').addEventListener('click', closeModal);
    };

    const refreshDoctor = async ({ force = false } = {}) => {
      if (doctorLoading || (doctorLoaded && !force)) return;
      doctorLoading = true;
      const btn = document.getElementById('runDoctor');
      const icon = btn ? btn.querySelector('.icon-refresh') : null;
      if (icon) icon.classList.add('loading-spin');
      try {
        const response = await fetch('/api/doctor', { cache: 'no-store' });
        renderDoctor(await response.json());
        doctorLoaded = true;
      } finally {
        doctorLoading = false;
        if (icon) icon.classList.remove('loading-spin');
      }
    };
    const renderProviderQuotaHealth = report => {
      const container = document.getElementById('providerQuotaHealth');
      if (!container) return;
      const check = report?.check;
      const generatedAt = report?.generated_at
        ? new Date(report.generated_at).toLocaleString()
        : '刚刚';
      text('providerQuotaMeta', `服务商额度与限流检测 · ${generatedAt}`);
      if (!check) {
        container.innerHTML = '<div class="telemetry-empty">暂无检测数据</div>';
        return;
      }
      const ok = check.status === 'ok';
      container.innerHTML = `
        <div class="flat-telem-row ${ok ? 'ok' : 'warn'}">
          <div class="flat-telem-status">
            <span class="flat-telem-dot"></span>
            <span class="flat-telem-badge-text">${ok ? 'SYSTEM ONLINE' : 'ATTENTION REQUIRED'}</span>
          </div>
          <strong class="flat-telem-title">${escapeHtml(check.label || '服务商状态检测')}</strong>
          <p class="flat-telem-desc">${escapeHtml(check.detail || '-')}</p>
          ${ok ? '' : `
            <div class="flat-telem-fix">
              <span class="fix-label">建议：</span>${escapeHtml(check.fix || '请检查模型服务商额度或限流状态')}
            </div>
          `}
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
          <text x="${pad}" y="18" fill="#137f6d" font-size="13" font-weight="800">今日 Token ${compactNumber(maxTokens)}</text>
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
      const recentQuery = requestSourceFilterValue
        ? `limit=20&source=${encodeURIComponent(requestSourceFilterValue)}`
        : 'limit=20';
      const [res, trendRes, recentRes, runtimeRes] = await Promise.all([
        fetch(`/api/summary?range=${currentRange}`, { cache: 'no-store' }),
        fetch(`/api/trends?${trendQuery}`, { cache: 'no-store' }),
        fetch(`/api/recent-requests?${recentQuery}`, { cache: 'no-store' }),
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
      setMetricParts('tokenSub', [
        comparisonText(comparison, u.total_tokens, previousUsage.total_tokens),
        `输入 ${compactNumber(u.input_tokens)}`,
        `输出 ${compactNumber(u.output_tokens)}`,
      ]);
      setMetric(
        'cacheTokens',
        compactNumber(u.cache_read_input_tokens + u.cache_creation_input_tokens),
        fmt.format(u.cache_read_input_tokens + u.cache_creation_input_tokens)
      );
      text('cacheSub', `读 ${compactNumber(u.cache_read_input_tokens)} / 写 ${compactNumber(u.cache_creation_input_tokens)}`);
      setMetric('blockedRequests', fmt.format(p.blocked_requests || 0));
      setMetric('avgLatency', `${fmt.format(p.average_connect_latency_ms || p.average_latency_ms || 0)}ms`);
      setMetric('successRateKpi', percent(p.success_rate));
      text('successRate', `${pointComparisonText(comparison, p.success_rate, previousProxy.success_rate)} · 持续 ${fmt.format(p.average_duration_ms || 0)}ms`);
      text('latencySub', `${deltaComparisonText(comparison, p.average_connect_latency_ms || p.average_latency_ms || 0, previousProxy.average_connect_latency_ms || previousProxy.average_latency_ms, 'ms')} · 慢建连 ${fmt.format(p.slow_requests || 0)}`);
      setMetric('estimatedCost', money(u.cost.total), `${money(u.cost.total)} CNY`);
      text('costSub', `${comparisonText(comparison, u.cost.total, previousUsage.cost?.total)} · API ${u.cost.billable_models} / 套餐 ${u.cost.token_plan_models} / 未计价 ${u.cost.unknown_models}`);
      setMetric('peakConnections', fmt.format(runtimeData.peak_connections_today || 0));
      text('peakConnectionsSub', `当前 ${fmt.format(runtimeData.active_connections || 0)} · 日期 ${runtimeData.peak_connections_date || '-'}`);
      setMetric('anomalyRequests', fmt.format((p.failed_requests || 0) + (p.slow_requests || 0)));
      text('anomalyRequestsSub', `失败 ${fmt.format(p.failed_requests || 0)} / 慢建连 ${fmt.format(p.slow_requests || 0)} · 告警 ${fmt.format((p.alerts || []).length)}`);
      renderAlerts(p);
      document.getElementById('routes').innerHTML = rows(Object.entries(p.routes));
      document.getElementById('models').innerHTML = modelRows(u.models);
      document.getElementById('hosts').innerHTML = hostRows(p.hosts || []);
      document.getElementById('providerSummary').innerHTML = providerSummaryRows(p.hosts || []);
      document.getElementById('providerHealth').innerHTML = providerHealthTable(p.hosts || []);
      renderRequestSourceFilter(p.clients || []);
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
      await Promise.allSettled([refreshWhitelist(), refreshBlocklist()]);
      const refreshedAt = new Date().toLocaleTimeString();
      text('status', `最后刷新 ${refreshedAt}`);
      text('lastRefreshAt', `最后刷新 ${refreshedAt}`);
    }

    initMetricCardFlow();
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
    document.getElementById('requestSourceFilter').addEventListener('change', event => {
      requestSourceFilterValue = event.target.value || '';
      refresh();
    });
    document.getElementById('themeToggle').addEventListener('click', () => {
      document.body.classList.toggle('dark-mode');
    });
    const handleAddWhitelist = () => {
      const inputEl = document.getElementById('whitelistInput');
      const result = addWhitelistEntry(inputEl.value);
      if (!result.ok) {
        inputEl.classList.remove('shake-anim');
        void inputEl.offsetWidth; // 触发 reflow
        inputEl.classList.add('shake-anim');
        setTimeout(() => inputEl.classList.remove('shake-anim'), 300);

        if (result.reason === 'empty') {
          showWhitelistFeedback('请输入有效的白名单规则 (例如 *.github.com)', 'error');
        } else if (result.reason === 'exists') {
          showWhitelistFeedback('该规则已存在于白名单中', 'warning');
        }
      } else {
        showWhitelistFeedback('已加入本地列表，点击保存后写入 whitelist.txt', 'info');
      }
    };

    const handleAddBlocklist = () => {
      const inputEl = document.getElementById('blocklistInput');
      const result = addBlocklistEntry(inputEl.value);
      if (!result.ok) {
        inputEl.classList.remove('shake-anim');
        void inputEl.offsetWidth; // 触发 reflow
        inputEl.classList.add('shake-anim');
        setTimeout(() => inputEl.classList.remove('shake-anim'), 300);

        if (result.reason === 'empty') {
          showBlocklistFeedback('请输入有效的屏蔽规则 (例如 *.gvt2.com)', 'error');
        } else if (result.reason === 'exists') {
          showBlocklistFeedback('该规则已存在于屏蔽名单中', 'warning');
        }
      } else {
        showBlocklistFeedback('已加入本地列表，点击保存后写入 blocklist.txt', 'info');
      }
    };

    document.getElementById('addWhitelistEntry').addEventListener('click', handleAddWhitelist);
    document.getElementById('whitelistInput').addEventListener('keydown', event => {
      if (event.key === 'Enter') {
        handleAddWhitelist();
      }
    });
    document.getElementById('saveWhitelist').addEventListener('click', () => {
      saveWhitelistEntries();
    });
    document.getElementById('whitelistInput').addEventListener('input', event => {
      renderWhitelistEntries(event.target.value);
    });
    document.getElementById('addBlocklistEntry').addEventListener('click', handleAddBlocklist);
    document.getElementById('blocklistInput').addEventListener('keydown', event => {
      if (event.key === 'Enter') {
        handleAddBlocklist();
      }
    });
    document.getElementById('blocklistInput').addEventListener('input', event => {
      renderBlocklistEntries(event.target.value);
    });
    document.getElementById('saveBlocklist').addEventListener('click', () => {
      saveBlocklistEntries();
    });
    document.getElementById('runDoctor').addEventListener(
      'click',
      () => refreshDoctor({ force: true }),
    );
    // 绑定自检卡片点击弹出二级卡片模态框事件
    const doctorGridEl = document.getElementById('doctorGrid');
    if (doctorGridEl) {
      doctorGridEl.addEventListener('click', event => {
        const card = event.target.closest('.doctor-single-card');
        if (!card) return;

        const key = card.getAttribute('data-check-key');
        if (!key || !window.lastDoctorChecks) return;

        const check = window.lastDoctorChecks.find(c => c.key === key);
        if (check) {
          showDoctorDetailModal(check);
        }
      });
    }

    document.getElementById('refreshProviderHealth').addEventListener(
      'click',
      () => refreshProviderQuotaHealth({ force: true }),
    );

    // 绑定流量大屏时间切换按钮
    ['analyticsSoftwareRange', 'analyticsProviderRange'].forEach(id => {
      const el = document.getElementById(id);
      if (el) {
        el.querySelectorAll('button[data-range]').forEach(btn => {
          btn.addEventListener('click', () => {
            const range = btn.dataset.range;
            analyticsRange = range;
            // 同步所有的流量范围切换按钮状态
            document.querySelectorAll('#analyticsSoftwareRange button, #analyticsProviderRange button').forEach(b => {
              b.classList.toggle('active', b.dataset.range === range);
            });
            refreshTrafficAnalytics();
          });
        });
      }
    });

    scheduleRefresh();
