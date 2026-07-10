/* ============================================================
   Dashboard Page — Overview with stats, charts, activity
   ============================================================ */

window.DashboardPage = (() => {
  let _refreshInterval = null;
  let _metricsData = null;
  let _healthData = null;
  let _usageData = null;

  function render() {
    const app = document.getElementById('app');
    app.innerHTML = `
      <div class="page-header">
        <h2>📊 Tổng quan</h2>
        <p>Theo dõi hoạt động phân loại phản hồi khách hàng</p>
      </div>

      <!-- Stat Cards -->
      <div class="stat-grid" id="dash-stats">
        ${renderStatsSkeleton()}
      </div>

      <!-- Admin Usage Stats -->
      <div id="dash-usage-stats"></div>

      <!-- Charts & Activity -->
      <div class="grid-2-1">
        <div class="flex flex-col" style="gap:20px;">
          <div class="card animate-in animate-in-delay-2">
            <div class="card-header">
              <span class="card-title"><span class="icon">📊</span> Số file theo ngày</span>
            </div>
            <div style="height:280px;position:relative;" id="daily-chart-wrap">
              <canvas id="chart-daily"></canvas>
            </div>
          </div>
          <div class="card animate-in animate-in-delay-3">
            <div class="card-header">
              <span class="card-title"><span class="icon">🏷️</span> Phân bổ nhãn</span>
            </div>
            <div style="height:300px;position:relative;" id="label-chart-wrap">
              <canvas id="chart-labels"></canvas>
            </div>
          </div>

        </div>

        <div class="flex flex-col" style="gap:20px;">
          <!-- Recent Activity -->
          <div class="card animate-in animate-in-delay-3">
            <div class="card-header">
              <span class="card-title"><span class="icon">🕐</span> Hoạt động gần đây</span>
              <button class="btn btn-ghost btn-sm" onclick="DashboardPage.loadData()">🔄</button>
            </div>
            <div id="dash-activity">
              <div class="skeleton skeleton-text"></div>
              <div class="skeleton skeleton-text"></div>
              <div class="skeleton skeleton-text short"></div>
            </div>
          </div>

          <!-- System Status -->
          <div class="card animate-in animate-in-delay-4">
            <div class="card-header">
              <span class="card-title"><span class="icon">🖥️</span> Trạng thái hệ thống</span>
            </div>
            <div id="dash-system">
              <div class="skeleton skeleton-text"></div>
              <div class="skeleton skeleton-text short"></div>
            </div>
          </div>

          <!-- Quick Actions -->
          <div class="card animate-in animate-in-delay-5">
            <div class="card-header">
              <span class="card-title"><span class="icon">⚡</span> Thao tác nhanh</span>
            </div>
            <div style="display:flex;flex-direction:column;gap:8px;">
              <button class="btn btn-primary w-full" onclick="location.hash='classify'">
                ⚡ Phân loại ngay
              </button>
              <button class="btn btn-secondary w-full" onclick="location.hash='files'">
                📂 Quản lý file
              </button>
              <button class="btn btn-secondary w-full" onclick="location.hash='settings'">
                ⚙️ Cài đặt hệ thống
              </button>
            </div>
          </div>
        </div>
      </div>
    `;

    loadData();
    startRefresh();
  }

  function renderStatsSkeleton() {
    return Array(5).fill(0).map((_, i) => `
      <div class="stat-card animate-in animate-in-delay-${i + 1}">
        <div class="skeleton skeleton-text xs" style="margin-bottom:12px;"></div>
        <div class="skeleton skeleton-text short" style="height:28px;margin-bottom:8px;"></div>
        <div class="skeleton skeleton-text xs"></div>
      </div>
    `).join('');
  }

  async function loadData() {
    try {
      const [metrics, health, daily] = await Promise.allSettled([
        API.getMetrics(),
        API.getHealth(),
        API.getMetricsDaily()
      ]);

      _metricsData = metrics.status === 'fulfilled' ? metrics.value : null;
      _healthData = health.status === 'fulfilled' ? health.value : null;
      const dailyData = daily.status === 'fulfilled' ? daily.value : null;

      renderStats(_metricsData);
      renderActivity(_metricsData);
      renderSystemStatus(_healthData, _metricsData);
      renderDailyChart(dailyData);
      renderLabelChart(_metricsData);

      // Admin-only: load Gemini usage metrics
      if (window.App?.state?.user?.role === 'admin') {
        try {
          _usageData = await API.getUsageMetrics('today');
          renderUsageStats(_usageData);
        } catch (e) {
          console.warn('Usage metrics not available:', e);
        }
      }
    } catch (e) {
      console.error('Dashboard load error:', e);
    }
  }

  function renderStats(data) {
    const el = document.getElementById('dash-stats');
    if (!el) return;

    const total = data?.total_files || 0;
    const success = data?.success_files || 0;
    const failed = data?.failed_files || 0;
    const rate = total > 0 ? ((success / total) * 100).toFixed(1) : '0.0';
    const avgTime = data?.avg_processing_time || 0;

    el.innerHTML = `
      <div class="stat-card blue animate-in">
        <div class="stat-card-top">
          <div>
            <div class="stat-card-value" data-count="${total}">${animateNum(total)}</div>
            <div class="stat-card-label">Tổng file đã xử lý</div>
          </div>
          <div class="stat-card-icon">📄</div>
        </div>
      </div>
      <div class="stat-card green animate-in animate-in-delay-1">
        <div class="stat-card-top">
          <div>
            <div class="stat-card-value" data-count="${success}">${animateNum(success)}</div>
            <div class="stat-card-label">Thành công</div>
          </div>
          <div class="stat-card-icon">✅</div>
        </div>
      </div>
      <div class="stat-card red animate-in animate-in-delay-2">
        <div class="stat-card-top">
          <div>
            <div class="stat-card-value" data-count="${failed}">${animateNum(failed)}</div>
            <div class="stat-card-label">Thất bại</div>
          </div>
          <div class="stat-card-icon">❌</div>
        </div>
      </div>
      <div class="stat-card amber animate-in animate-in-delay-3">
        <div class="stat-card-top">
          <div>
            <div class="stat-card-value">${rate}%</div>
            <div class="stat-card-label">Tỷ lệ thành công</div>
          </div>
          <div class="stat-card-icon">📈</div>
        </div>
      </div>
      <div class="stat-card purple animate-in animate-in-delay-4">
        <div class="stat-card-top">
          <div>
            <div class="stat-card-value">${avgTime.toFixed(1)}s</div>
            <div class="stat-card-label">TB thời gian/file</div>
          </div>
          <div class="stat-card-icon">⏱️</div>
        </div>
      </div>
    `;
  }

  function animateNum(num) {
    return `<span style="animation: countUp 0.5s ease both;">${num.toLocaleString()}</span>`;
  }

  function renderDailyChart(data) {
    const wrap = document.getElementById('daily-chart-wrap');
    if (!data || !data.dates || data.dates.length === 0) {
      if (wrap) wrap.innerHTML = '<div class="empty-state"><div class="empty-state-icon">📊</div><p class="empty-state-text">Chưa có dữ liệu</p></div>';
      return;
    }
    if (wrap && !document.getElementById('chart-daily')) {
      wrap.innerHTML = '<canvas id="chart-daily"></canvas>';
    }
    Charts.createBarChart('chart-daily', data.dates, data.counts, {
      label: 'Số file'
    });
  }

  function renderLabelChart(data) {
    const wrap = document.getElementById('label-chart-wrap');
    if (!data || !data.label_distribution || Object.keys(data.label_distribution).length === 0) {
      if (wrap) wrap.innerHTML = '<div class="empty-state"><div class="empty-state-icon">🏷️</div><p class="empty-state-text">Chưa có dữ liệu</p></div>';
      return;
    }

    if (wrap && !document.getElementById('chart-labels')) {
      wrap.innerHTML = '<canvas id="chart-labels"></canvas>';
    }

    const dist = data.label_distribution;
    const labels = Object.keys(dist);
    const values = Object.values(dist);

    Charts.createDoughnutChart('chart-labels', labels, values);
  }

  function formatTokens(n) {
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
    if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
    return n.toLocaleString();
  }

  function renderUsageStats(data) {
    const el = document.getElementById('dash-usage-stats');
    if (!el) return;
    if (!window.App?.state?.user?.role === 'admin' || !data) {
      el.innerHTML = '';
      return;
    }

    const todayTokens = data.today_tokens || 0;
    const todayCost = data.today_cost || 0;
    const todayRequests = data.today_requests || 0;

    el.innerHTML = `
      <div style="margin-bottom:8px;margin-top:4px;">
        <span style="font-size:13px;font-weight:600;color:var(--text-secondary);">🤖 Gemini Usage (hôm nay)</span>
      </div>
      <div class="stat-grid" style="margin-bottom:20px;">
        <div class="stat-card blue animate-in">
          <div class="stat-card-top">
            <div>
              <div class="stat-card-value">${todayTokens.toLocaleString()}</div>
              <div class="stat-card-label">Tổng Token (hôm nay)</div>
            </div>
            <div class="stat-card-icon">🔤</div>
          </div>
        </div>
        <div class="stat-card green animate-in animate-in-delay-1">
          <div class="stat-card-top">
            <div>
              <div class="stat-card-value">$${todayCost.toFixed(2)}</div>
              <div class="stat-card-label">Chi phí ước tính</div>
            </div>
            <div class="stat-card-icon">💰</div>
          </div>
        </div>
        <div class="stat-card amber animate-in animate-in-delay-2">
          <div class="stat-card-top">
            <div>
              <div class="stat-card-value">${todayRequests.toLocaleString()}</div>
              <div class="stat-card-label">Requests Gemini</div>
            </div>
            <div class="stat-card-icon">📡</div>
          </div>
        </div>
      </div>
    `;
  }


  function renderActivity(data) {
    const el = document.getElementById('dash-activity');
    if (!el) return;

    const files = data?.recent_files || [];
    if (files.length === 0) {
      el.innerHTML = `
        <div class="empty-state" style="padding:24px;">
          <div class="empty-state-icon">🕐</div>
          <p class="empty-state-text">Chưa có hoạt động</p>
          <p class="empty-state-hint">File được xử lý sẽ hiển thị ở đây</p>
        </div>
      `;
      return;
    }

    el.innerHTML = files.slice(0, 10).map(f => {
      const statusMap = {
        done: { badge: 'badge-green', icon: '✅', text: 'Hoàn thành' },
        processing: { badge: 'badge-blue', icon: '🔄', text: 'Đang xử lý' },
        failed: { badge: 'badge-red', icon: '❌', text: 'Thất bại' },
        new: { badge: 'badge-amber', icon: '🆕', text: 'Mới' },
        retry: { badge: 'badge-purple', icon: '🔁', text: 'Thử lại' }
      };
      const st = statusMap[f.status] || statusMap.new;

      return `
        <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border);">
          <span style="font-size:16px;">${st.icon}</span>
          <div style="flex:1;min-width:0;">
            <div style="font-size:13px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escHtml(f.filename || f.name || 'Unknown')}</div>
            <div style="font-size:11px;color:var(--text-muted);">${f.timestamp || f.date || ''}</div>
          </div>
          <span class="badge ${st.badge}">${st.text}</span>
        </div>
      `;
    }).join('');
  }

  function renderSystemStatus(health, metrics) {
    const el = document.getElementById('dash-system');
    if (!el) return;

    const online = !!health;
    // uptime comes as pre-formatted string like "1h 36m"
    const uptimeStr = health?.uptime || '—';
    const cycle = health?.current_cycle || 0;
    const model = health?.model || metrics?.model || '—';

    el.innerHTML = `
      <div class="result-row">
        <span class="result-key">Trạng thái</span>
        <span style="display:flex;align-items:center;gap:6px;">
          <span class="status-dot ${online ? 'online' : 'offline'}"></span>
          <span style="color:${online ? 'var(--accent-green)' : 'var(--accent-red)'}; font-weight:600;">
            ${online ? 'Hoạt động' : 'Ngắt kết nối'}
          </span>
        </span>
      </div>
      <div class="result-row">
        <span class="result-key">Uptime</span>
        <span class="result-value">${escHtml(String(uptimeStr))}</span>
      </div>
      <div class="result-row">
        <span class="result-key">Chu kỳ quét</span>
        <span class="result-value">${cycle}</span>
      </div>
      <div class="result-row">
        <span class="result-key">Model</span>
        <span class="result-value text-mono" style="font-size:11px;">${escHtml(model)}</span>
      </div>
    `;
  }

  function startRefresh() {
    stopRefresh();
    _refreshInterval = setInterval(loadData, 30000);
  }

  function stopRefresh() {
    if (_refreshInterval) {
      clearInterval(_refreshInterval);
      _refreshInterval = null;
    }
  }

  function destroy() {
    stopRefresh();
    Charts.destroy('chart-daily');
    Charts.destroy('chart-labels');
  }

  function escHtml(str) {
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
  }

  return { render, loadData, destroy };
})();
