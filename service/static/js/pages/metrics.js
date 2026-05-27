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
        <div style="height:260px;position:relative;">
          <canvas id="metrics-daily-chart"></canvas>
        </div>
      </div>

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
    } catch (e) {
      console.error('Metrics load error:', e);
    }
  }

  function renderHealthStats(health, metrics) {
    const el = document.getElementById('metrics-stats');
    if (!el) return;

    const online = !!health;
    const uptime = health?.uptime || 0;
    const hrs = Math.floor(uptime / 3600);
    const mins = Math.floor((uptime % 3600) / 60);
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
            <div class="stat-card-value">${hrs > 0 ? hrs + 'h ' : ''}${mins}m</div>
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
    if (!data || !data.dates) return;
    Charts.createBarChart('metrics-daily-chart', data.dates, data.counts, {
      label: 'Số file xử lý'
    });
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
  }

  return { render, destroy, loadMetrics, loadLogs, setLogFilter, onSearchLog, toggleAutoScroll, clearLogs };
})();
