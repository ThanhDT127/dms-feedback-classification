/* ============================================================
   Metrics & Logs Page — Health, metrics, live logs
   ============================================================ */

window.MetricsPage = (() => {
  let _refreshInterval = null;
  let _logRefreshInterval = null;
  let _logFilter = 'all';
  let _autoScroll = true;
  let _searchTerm = '';
  let _allLogs = [];
  let _usagePeriod = 'week';
  let _usageData = null;

  function render() {
    const app = document.getElementById('app');
    app.innerHTML = `
      <div class="page-header">
        <h2>📈 Thống kê & Nhật ký</h2>
        <p>Giám sát hiệu suất và theo dõi nhật ký hệ thống</p>
      </div>

      <!-- Health & Summary Cards -->
      <div class="stat-grid" id="metrics-stats">
        ${renderStatsSkeleton()}
      </div>

      <!-- Daily Metrics -->
      <div class="card animate-in animate-in-delay-2 mb-6">
        <div class="card-header">
          <span class="card-title"><span class="icon">📊</span> Thống kê theo ngày</span>
          <button class="btn btn-ghost btn-sm" onclick="MetricsPage.loadMetrics()">🔄</button>
        </div>
        <div style="height:260px;position:relative;" id="metrics-daily-chart-wrap">
          <canvas id="metrics-daily-chart"></canvas>
        </div>
      </div>

      <!-- Admin: Usage Analytics -->
      <div id="usage-analytics-section"></div>

      <!-- Live Logs -->
      <div class="card animate-in animate-in-delay-3">
        <div class="card-header">
          <span class="card-title"><span class="icon">📋</span> Nhật ký hệ thống</span>
          <div style="display:flex;align-items:center;gap:8px;">
            <button class="toggle ${_autoScroll ? 'active' : ''}" id="log-autoscroll"
                    onclick="MetricsPage.toggleAutoScroll()" title="Tự động cuộn"></button>
            <span class="text-muted" style="font-size:11px;">Tự động cuộn</span>
          </div>
        </div>

        <!-- Filter bar -->
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;flex-wrap:wrap;">
          <div class="pill-tabs" style="padding:2px;">
            <button class="pill-tab ${_logFilter === 'all' ? 'active' : ''}" style="padding:5px 12px;font-size:12px;" onclick="MetricsPage.setLogFilter('all')">TẤT CẢ</button>
            <button class="pill-tab ${_logFilter === 'info' ? 'active' : ''}" style="padding:5px 12px;font-size:12px;" onclick="MetricsPage.setLogFilter('info')">INFO</button>
            <button class="pill-tab ${_logFilter === 'warning' ? 'active' : ''}" style="padding:5px 12px;font-size:12px;" onclick="MetricsPage.setLogFilter('warning')">WARN</button>
            <button class="pill-tab ${_logFilter === 'error' ? 'active' : ''}" style="padding:5px 12px;font-size:12px;" onclick="MetricsPage.setLogFilter('error')">ERROR</button>
          </div>
          <div style="flex:1;min-width:150px;">
            <input type="text" class="form-input" placeholder="🔍 Tìm kiếm nhật ký..."
                   style="padding:6px 12px;font-size:12px;" id="log-search"
                   oninput="MetricsPage.onSearchLog(this.value)">
          </div>
          <button class="btn btn-ghost btn-sm" onclick="MetricsPage.clearLogs()">🗑️ Xóa</button>
          <span class="text-muted" style="font-size:11px;" id="log-count"></span>
        </div>

        <!-- Log panel -->
        <div class="log-panel" id="log-panel">
          <div class="text-center text-muted" style="padding:40px;">
            <span class="spinner"></span>
            <div class="mt-4">Đang tải nhật ký...</div>
          </div>
        </div>
      </div>
    `;

    loadMetrics();
    loadLogs();
    startRefresh();
  }

  function renderStatsSkeleton() {
    return Array(4).fill(0).map((_, i) => `
      <div class="stat-card animate-in animate-in-delay-${i + 1}">
        <div class="skeleton skeleton-text xs" style="margin-bottom:12px;"></div>
        <div class="skeleton skeleton-text short" style="height:28px;margin-bottom:8px;"></div>
        <div class="skeleton skeleton-text xs"></div>
      </div>
    `).join('');
  }

  async function loadMetrics() {
    try {
      const [health, metrics, daily] = await Promise.allSettled([
        API.getHealth(),
        API.getMetrics(),
        API.getMetricsDaily()
      ]);

      const h = health.status === 'fulfilled' ? health.value : null;
      const m = metrics.status === 'fulfilled' ? metrics.value : {};
      const d = daily.status === 'fulfilled' ? daily.value : null;

      renderHealthStats(h, m);
      renderDailyChart(d);

      // Admin-only: load usage analytics
      if (window.App?.state?.user?.role === 'admin') {
        await loadUsageData();
      }
    } catch (e) {
      console.error('Metrics load error:', e);
    }
  }

  function renderHealthStats(health, metrics) {
    const el = document.getElementById('metrics-stats');
    if (!el) return;

    const online = !!health;
    const uptimeStr = health?.uptime || '—';
    const total = metrics?.total_files || 0;
    const success = metrics?.success_files || 0;
    const pollCycle = health?.poll_interval || 0;

    el.innerHTML = `
      <div class="stat-card ${online ? 'green' : 'red'} animate-in">
        <div class="stat-card-top">
          <div>
            <div style="display:flex;align-items:center;gap:8px;">
              <span class="status-dot ${online ? 'online' : 'offline'}"></span>
              <span class="stat-card-value" style="font-size:18px;">${online ? 'Hoạt động' : 'Ngắt kết nối'}</span>
            </div>
            <div class="stat-card-label">Trạng thái dịch vụ</div>
          </div>
          <div class="stat-card-icon">${online ? '🟢' : '🔴'}</div>
        </div>
      </div>
      <div class="stat-card blue animate-in animate-in-delay-1">
        <div class="stat-card-top">
          <div>
            <div class="stat-card-value">${uptimeStr}</div>
            <div class="stat-card-label">Uptime</div>
          </div>
          <div class="stat-card-icon">⏱️</div>
        </div>
      </div>
      <div class="stat-card amber animate-in animate-in-delay-2">
        <div class="stat-card-top">
          <div>
            <div class="stat-card-value">${pollCycle}s</div>
            <div class="stat-card-label">Chu kỳ quét</div>
          </div>
          <div class="stat-card-icon">🔄</div>
        </div>
      </div>
      <div class="stat-card purple animate-in animate-in-delay-3">
        <div class="stat-card-top">
          <div>
            <div class="stat-card-value">${total.toLocaleString()}</div>
            <div class="stat-card-label">Tổng file (${success} thành công)</div>
          </div>
          <div class="stat-card-icon">📊</div>
        </div>
      </div>
    `;
  }

  function renderDailyChart(data) {
    const wrap = document.getElementById('metrics-daily-chart-wrap');
    if (!data || !data.dates || data.dates.length === 0) {
      if (wrap) {
        wrap.innerHTML = '<div class="empty-state"><div class="empty-state-icon">📊</div><p class="empty-state-text">Chưa có dữ liệu</p></div>';
      }
      return;
    }
    if (wrap && !document.getElementById('metrics-daily-chart')) {
      wrap.innerHTML = '<canvas id="metrics-daily-chart"></canvas>';
    }
    Charts.createBarChart('metrics-daily-chart', data.dates, data.counts, {
      label: 'Số file xử lý'
    });
  }

  /* ---- Usage Analytics (admin only) ---- */

  function formatTokens(n) {
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
    if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
    return n.toLocaleString();
  }

  async function loadUsageData() {
    const section = document.getElementById('usage-analytics-section');
    if (!section || window.App?.state?.user?.role !== 'admin') return;

    try {
      _usageData = await API.getUsageMetrics(_usagePeriod);
      renderUsageSection(_usageData);
    } catch (e) {
      console.warn('Usage metrics not available:', e);
      section.innerHTML = '';
    }
  }

  function setUsagePeriod(period) {
    _usagePeriod = period;

    // Toggle custom date row visibility
    const customRow = document.getElementById('usage-custom-dates');
    if (customRow) customRow.style.display = period === 'custom' ? 'flex' : 'none';

    // Update active pill
    document.querySelectorAll('#usage-period-pills .pill-tab').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.period === period);
    });

    if (period === 'custom') return; // wait for user to pick dates
    loadUsageData();
  }

  function applyCustomUsageDates() {
    const from = document.getElementById('usage-date-from')?.value;
    const to = document.getElementById('usage-date-to')?.value;
    if (!from || !to) { if (window.Toast) Toast.show('Chọn cả ngày bắt đầu và kết thúc', 'error'); return; }
    _usagePeriod = 'custom';
    const section = document.getElementById('usage-analytics-section');
    if (!section) return;
    API.getUsageMetrics('custom', from, to).then(data => {
      _usageData = data;
      renderUsageSection(data);
    }).catch(e => console.warn('Custom usage load error:', e));
  }

  function renderUsageSection(data) {
    const section = document.getElementById('usage-analytics-section');
    if (!section || !data) return;

    section.innerHTML = `
      <div class="card animate-in mb-6">
        <div class="card-header">
          <span class="card-title"><span class="icon">🤖</span> Giám sát Gemini API</span>
          <button class="btn btn-ghost btn-sm" onclick="MetricsPage.loadUsageData()">🔄</button>
        </div>

        <!-- Period Picker -->
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px;flex-wrap:wrap;">
          <div class="pill-tabs" id="usage-period-pills" style="padding:2px;">
            <button class="pill-tab ${_usagePeriod === 'day' ? 'active' : ''}" data-period="day" style="padding:5px 12px;font-size:12px;" onclick="MetricsPage.setUsagePeriod('day')">Hôm nay</button>
            <button class="pill-tab ${_usagePeriod === 'week' ? 'active' : ''}" data-period="week" style="padding:5px 12px;font-size:12px;" onclick="MetricsPage.setUsagePeriod('week')">7 ngày</button>
            <button class="pill-tab ${_usagePeriod === 'month' ? 'active' : ''}" data-period="month" style="padding:5px 12px;font-size:12px;" onclick="MetricsPage.setUsagePeriod('month')">30 ngày</button>
            <button class="pill-tab ${_usagePeriod === 'custom' ? 'active' : ''}" data-period="custom" style="padding:5px 12px;font-size:12px;" onclick="MetricsPage.setUsagePeriod('custom')">Tuỳ chọn</button>
          </div>
          <div id="usage-custom-dates" style="display:${_usagePeriod === 'custom' ? 'flex' : 'none'};align-items:center;gap:8px;">
            <input type="date" id="usage-date-from" class="form-input" style="padding:5px 8px;font-size:12px;">
            <span style="color:var(--text-muted);font-size:12px;">–</span>
            <input type="date" id="usage-date-to" class="form-input" style="padding:5px 8px;font-size:12px;">
            <button class="btn btn-primary btn-sm" onclick="MetricsPage.applyCustomUsageDates()">Áp dụng</button>
          </div>
        </div>

        <!-- Usage Stat Cards -->
        <div id="usage-stat-cards"></div>

        <!-- Token Chart (full width) -->
        <div style="margin-top:16px;">
          <div style="font-size:13px;font-weight:600;color:var(--text-secondary);margin-bottom:8px;">📊 Token sử dụng theo ngày</div>
          <div style="height:280px;position:relative;" id="usage-token-chart-wrap">
            <canvas id="usage-token-chart"></canvas>
          </div>
        </div>

        <!-- Top Jobs Table -->
        <div style="margin-top:20px;">
          <div style="font-size:13px;font-weight:600;color:var(--text-secondary);margin-bottom:8px;">🏆 Top 10 file tốn token nhất</div>
          <div id="usage-top-jobs" style="overflow-x:auto;"></div>
        </div>
      </div>
    `;

    renderUsageStats(data);
    renderUsageTokenChart(data);
    renderUsageTopJobs(data);
  }

  function renderUsageStats(data) {
    const el = document.getElementById('usage-stat-cards');
    if (!el || !data) return;

    const totalRequests = data.total_requests || 0;
    const totalTokens = data.total_tokens || 0;
    const inputTokens = data.total_input_tokens || 0;
    const outputTokens = data.total_output_tokens || 0;
    const avgPerReq = totalRequests > 0 ? Math.round(totalTokens / totalRequests) : 0;
    const totalCost = data.total_cost || 0;

    el.innerHTML = `
      <div class="stat-grid">
        <div class="stat-card blue animate-in">
          <div class="stat-card-top">
            <div>
              <div class="stat-card-value">${totalRequests.toLocaleString()}</div>
              <div class="stat-card-label">Số lần gọi API</div>
            </div>
            <div class="stat-card-icon">📡</div>
          </div>
        </div>
        <div class="stat-card green animate-in animate-in-delay-1">
          <div class="stat-card-top">
            <div>
              <div class="stat-card-value">${formatTokens(inputTokens)} / ${formatTokens(outputTokens)}</div>
              <div class="stat-card-label">Token đầu vào / đầu ra</div>
            </div>
            <div class="stat-card-icon">🔤</div>
          </div>
        </div>
        <div class="stat-card amber animate-in animate-in-delay-2">
          <div class="stat-card-top">
            <div>
              <div class="stat-card-value">${formatTokens(avgPerReq)}</div>
              <div class="stat-card-label">TB token / lần gọi</div>
            </div>
            <div class="stat-card-icon">📏</div>
          </div>
        </div>
        <div class="stat-card purple animate-in animate-in-delay-3">
          <div class="stat-card-top">
            <div>
              <div class="stat-card-value">$${totalCost.toFixed(4)}</div>
              <div class="stat-card-label">Chi phí ước tính</div>
            </div>
            <div class="stat-card-icon">💲</div>
          </div>
        </div>
      </div>
    `;
  }

  function renderUsageTokenChart(data) {
    const wrap = document.getElementById('usage-token-chart-wrap');
    if (!wrap) return;
    const daily = data?.daily || [];
    if (daily.length === 0) {
      wrap.innerHTML = '<div class="empty-state"><div class="empty-state-icon">📊</div><p class="empty-state-text">Chưa có dữ liệu</p></div>';
      return;
    }
    if (!document.getElementById('usage-token-chart')) {
      wrap.innerHTML = '<canvas id="usage-token-chart"></canvas>';
    }

    const labels = daily.map(d => d.date);
    const inputData = daily.map(d => d.input_tokens || 0);
    const outputData = daily.map(d => d.output_tokens || 0);

    Charts.destroy('usage-token-chart');
    const ctx = document.getElementById('usage-token-chart')?.getContext('2d');
    if (!ctx) return;

    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
    const gridColor = isLight ? 'rgba(0,0,0,0.06)' : 'rgba(255,255,255,0.06)';

    new Chart(ctx, {
      type: 'bar',
      data: {
        labels,
        datasets: [
          {
            label: 'Token đầu vào',
            data: inputData,
            backgroundColor: 'rgba(59, 130, 246, 0.8)',
            borderColor: '#3b82f6',
            borderWidth: 1,
            borderRadius: 4,
            borderSkipped: false
          },
          {
            label: 'Token đầu ra',
            data: outputData,
            backgroundColor: 'rgba(34, 197, 94, 0.8)',
            borderColor: '#22c55e',
            borderWidth: 1,
            borderRadius: 4,
            borderSkipped: false
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 800, easing: 'easeOutQuart' },
        plugins: { legend: { display: true } },
        scales: {
          x: { stacked: true, grid: { display: false }, ticks: { maxRotation: 45 } },
          y: { stacked: true, beginAtZero: true, grid: { color: gridColor, drawBorder: false }, ticks: { precision: 0 } }
        }
      }
    });
  }

  function renderUsageTopJobs(data) {
    const el = document.getElementById('usage-top-jobs');
    if (!el) return;
    const jobs = data?.top_jobs || [];
    if (jobs.length === 0) {
      el.innerHTML = '<div class="text-muted" style="padding:12px;font-size:12px;">Chưa có dữ liệu</div>';
      return;
    }
    el.innerHTML = `
      <table style="width:100%;font-size:12px;border-collapse:collapse;">
        <thead>
          <tr style="border-bottom:1px solid var(--border);">
            <th style="text-align:left;padding:8px 6px;color:var(--text-muted);font-weight:600;">#</th>
            <th style="text-align:left;padding:8px 6px;color:var(--text-muted);font-weight:600;">File</th>
            <th style="text-align:right;padding:8px 6px;color:var(--text-muted);font-weight:600;">Tokens</th>
            <th style="text-align:right;padding:8px 6px;color:var(--text-muted);font-weight:600;">Chi phí</th>
            <th style="text-align:right;padding:8px 6px;color:var(--text-muted);font-weight:600;">Ngày</th>
          </tr>
        </thead>
        <tbody>
          ${jobs.slice(0, 10).map((job, i) => `
            <tr style="border-bottom:1px solid var(--border);">
              <td style="padding:6px;color:var(--text-muted);">${i + 1}</td>
              <td style="padding:6px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${esc(job.filename || job.file || '')}">${esc(job.filename || job.file || 'Unknown')}</td>
              <td style="padding:6px;text-align:right;font-weight:500;">${formatTokens(job.total_tokens || 0)}</td>
              <td style="padding:6px;text-align:right;color:var(--accent-green);">$${(job.cost || 0).toFixed(4)}</td>
              <td style="padding:6px;text-align:right;color:var(--text-muted);">${esc(job.date || '')}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
  }

  /* ---- Logs ---- */
  async function loadLogs() {
    try {
      const data = await API.getLogs();
      _allLogs = Array.isArray(data) ? data : (data.logs || data.entries || []);
      renderLogs();
    } catch (e) {
      const panel = document.getElementById('log-panel');
      if (panel) panel.innerHTML = '<p class="text-muted text-center" style="padding:20px;">Không thể tải nhật ký</p>';
    }
  }

  function renderLogs() {
    const panel = document.getElementById('log-panel');
    const countEl = document.getElementById('log-count');
    if (!panel) return;

    let filtered = _allLogs;

    // Filter by level
    if (_logFilter !== 'all') {
      filtered = filtered.filter(l => {
        const level = (l.level || l.levelname || '').toLowerCase();
        return level === _logFilter || level.startsWith(_logFilter);
      });
    }

    // Filter by search
    if (_searchTerm) {
      const term = _searchTerm.toLowerCase();
      filtered = filtered.filter(l => {
        const msg = (l.message || l.msg || '').toLowerCase();
        return msg.includes(term);
      });
    }

    if (countEl) countEl.textContent = `${filtered.length} dòng`;

    if (filtered.length === 0) {
      panel.innerHTML = `
        <div class="empty-state" style="padding:40px;">
          <div class="empty-state-icon">📋</div>
          <p class="empty-state-text">Không có nhật ký</p>
          <p class="empty-state-hint">${_logFilter !== 'all' ? 'Thử bỏ bộ lọc' : 'Nhật ký sẽ xuất hiện khi hệ thống hoạt động'}</p>
        </div>
      `;
      return;
    }

    panel.innerHTML = filtered.slice(-200).map(l => {
      const time = l.timestamp || l.time || l.asctime || '';
      const level = (l.level || l.levelname || 'INFO').toLowerCase();
      const msg = l.message || l.msg || '';
      const shortTime = time.includes('T') ? time.split('T')[1]?.substring(0, 8) : time.substring(11, 19);

      return `
        <div class="log-line">
          <span class="log-time">${esc(shortTime)}</span>
          <span class="log-level ${level}">${level.toUpperCase()}</span>
          <span class="log-msg">${esc(msg)}</span>
        </div>
      `;
    }).join('');

    if (_autoScroll) {
      panel.scrollTop = panel.scrollHeight;
    }
  }

  function setLogFilter(filter) {
    _logFilter = filter;
    // Update active pill
    document.querySelectorAll('#app .pill-tab').forEach(t => {
      const text = t.textContent.trim().toLowerCase();
      const isAll = text === 'tất cả';
      t.classList.toggle('active',
        (filter === 'all' && isAll) ||
        (text === filter) ||
        (filter === 'warning' && text === 'warn')
      );
    });
    renderLogs();
  }

  function onSearchLog(value) {
    _searchTerm = value;
    renderLogs();
  }

  function toggleAutoScroll() {
    _autoScroll = !_autoScroll;
    const toggle = document.getElementById('log-autoscroll');
    if (toggle) toggle.classList.toggle('active', _autoScroll);
  }

  function clearLogs() {
    _allLogs = [];
    renderLogs();
  }

  function startRefresh() {
    stopRefresh();
    _refreshInterval = setInterval(loadMetrics, 30000);
    _logRefreshInterval = setInterval(loadLogs, 10000);
  }

  function stopRefresh() {
    if (_refreshInterval) { clearInterval(_refreshInterval); _refreshInterval = null; }
    if (_logRefreshInterval) { clearInterval(_logRefreshInterval); _logRefreshInterval = null; }
  }

  function esc(s) {
    if (s == null) return '';
    const d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
  }

  function destroy() {
    stopRefresh();
    Charts.destroy('metrics-daily-chart');
    Charts.destroy('usage-token-chart');
  }

  return { render, destroy, loadMetrics, loadLogs, setLogFilter, onSearchLog, toggleAutoScroll, clearLogs, setUsagePeriod, loadUsageData, applyCustomUsageDates };
})();
