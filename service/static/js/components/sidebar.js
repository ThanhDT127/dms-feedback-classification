/* ============================================================
   Sidebar Navigation Component
   ============================================================ */

window.Sidebar = (() => {
  const NAV_ITEMS = [
    { id: 'analytics', icon: '📊', label: 'Dashboard' },
    { id: 'classify',  icon: '⚡', label: 'Phân loại' },
    { id: 'files',     icon: '📂', label: 'Quản lý file' },
    { id: 'dashboard', icon: '📋', label: 'Tiến trình Job' },
    { id: 'chat',      icon: '🤖', label: 'Trợ lý dữ liệu' },
    { id: 'metrics',   icon: '📈', label: 'Thống kê' },
    { id: 'pipeline',  icon: '🔬', label: 'Pipeline' },
    { id: 'qa',        icon: '📖', label: 'Hướng dẫn' },
    { id: 'settings',  icon: '⚙️', label: 'Cài đặt' },
  ];

  const THEME_OPTIONS = [
    { id: 'dark', icon: '🌙', label: 'Tối' },
    { id: 'soft', icon: '🌿', label: 'Dịu' },
    { id: 'light', icon: '☀️', label: 'Sáng' },
  ];

  let _statusInterval = null;

  function formatVersion(raw) {
    if (!raw) return '';
    const trimmed = String(raw).trim();
    if (!trimmed) return '';
    return trimmed.startsWith('v') ? trimmed : `v${trimmed}`;
  }

  function getActiveTheme() {
    return document.documentElement.getAttribute('data-theme') || App?.state?.theme || 'dark';
  }

  function render() {
    const sidebar = document.getElementById('sidebar');
    if (!sidebar) return;

    const user = App.state?.user;
    const role = user?.role || 'user';
    const items = NAV_ITEMS.filter(item => {
      if (role !== 'admin' && ['settings', 'pipeline'].includes(item.id)) return false;
      if (item.id === 'chat' && !App.state?.chatConfig?.enabled) return false;
      return true;
    });
    const activeTheme = getActiveTheme();

    sidebar.innerHTML = `
      <div class="sidebar-brand">
        <div class="sidebar-brand-logo">
          <img class="sidebar-brand-avatar" src="assets/avatar.png" alt="Hệ thống phân loại phản hồi Rạng Đông">
          <div class="sidebar-brand-copy">
            <h1 aria-label="Hệ thống phân loại phản hồi Rạng Đông">Hệ thống phân loại<br>phản hồi Rạng Đông</h1>
            <div class="brand-status-pill" id="sidebar-status-pill" role="status" aria-live="polite" title="Trạng thái: Đang kiểm tra...">
              <span class="status-dot" id="status-dot"></span>
              <span class="brand-status-version" id="status-version" style="display:none;"></span>
              <span class="brand-status-sep" id="status-sep" style="display:none;">·</span>
              <span class="brand-status-text" id="status-text">Đang kiểm tra...</span>
            </div>
          </div>
        </div>
        <div class="theme-segmented" id="theme-toggle-btn" role="radiogroup" aria-label="Chế độ màu giao diện">
          ${THEME_OPTIONS.map(opt => {
            const active = activeTheme === opt.id;
            return `<button type="button" class="theme-segmented-btn${active ? ' active' : ''}" role="radio" data-theme-option="${opt.id}" aria-checked="${active ? 'true' : 'false'}" onclick="App.setTheme('${opt.id}')"><span>${opt.icon}</span><span>${opt.label}</span></button>`;
          }).join('')}
        </div>
      </div>
      <nav class="sidebar-nav">
        ${items.map(item => `
          <div class="sidebar-nav-item" data-page="${item.id}" onclick="Sidebar.navigate('${item.id}')">
            <span class="sidebar-nav-icon">${item.icon}</span>
            <span>${item.label}</span>
          </div>
        `).join('')}
      </nav>
      <div class="sidebar-status" id="sidebar-status">
        <div class="sidebar-status-row">
          <span class="sidebar-status-label">🕐 Uptime</span>
          <span id="status-uptime">—</span>
        </div>
        <div class="sidebar-status-row" id="config-sync-row" style="display:none;">
          <span class="sidebar-status-label">🔑 Cấu hình</span>
          <span id="config-sync-text">—</span>
        </div>
      </div>
      <div style="padding:12px 16px;border-top:1px solid var(--border);margin-top:auto;">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;">
          <span style="font-size:12px;color:var(--text-muted);">👤 ${user?.display_name || user?.username || 'User'}</span>
          <button class="btn btn-ghost btn-sm" style="font-size:11px;" onclick="App.logout()">🚪 Đăng xuất</button>
        </div>
      </div>
    `;

    updateActive(window.App ? window.App.state.currentPage : 'dashboard');
    startStatusPolling();
  }

  function navigate(page) {
    window.location.hash = page;
  }

  function updateActive(page) {
    document.querySelectorAll('.sidebar-nav-item').forEach(el => {
      el.classList.toggle('active', el.dataset.page === page);
    });
  }

  function updateThemeSwitch() {
    const group = document.getElementById('theme-toggle-btn');
    if (!group) return;
    const activeTheme = getActiveTheme();
    group.querySelectorAll('[data-theme-option]').forEach(btn => {
      const isChecked = btn.getAttribute('data-theme-option') === activeTheme;
      btn.setAttribute('aria-checked', isChecked ? 'true' : 'false');
      btn.classList.toggle('active', isChecked);
    });
  }

  function updateToggleIcon() {
    updateThemeSwitch();
  }

  function setStatusPillView(variant, statusLabel, versionStr, tooltipText) {
    const pill = document.getElementById('sidebar-status-pill');
    const dot = document.getElementById('status-dot');
    const versionEl = document.getElementById('status-version');
    const sepEl = document.getElementById('status-sep');
    const textEl = document.getElementById('status-text');
    if (!pill || !dot || !textEl) return;

    pill.className = `brand-status-pill ${variant}`;
    if (tooltipText) pill.title = tooltipText;
    dot.className = `status-dot ${variant}`;
    textEl.textContent = statusLabel;

    if (versionEl && sepEl) {
      if (versionStr) {
        versionEl.textContent = versionStr;
        versionEl.style.display = '';
        sepEl.style.display = '';
      } else {
        versionEl.textContent = '';
        versionEl.style.display = 'none';
        sepEl.style.display = 'none';
      }
    }
  }

  async function fetchStatus() {
    const dot = document.getElementById('status-dot');
    const uptime = document.getElementById('status-uptime');
    const configRow = document.getElementById('config-sync-row');
    const configText = document.getElementById('config-sync-text');

    if (!dot) return;

    try {
      const data = await API.getHealth({ silent: true });
      const rawStatus = String(data?.status || 'unknown').toLowerCase();
      const versionStr = formatVersion(data?.version);
      const uptimeDisplay = data?.uptime && data.uptime !== 'N/A' ? data.uptime : '—';

      let variant = 'warning';
      let statusLabel = 'Cảnh báo';
      if (rawStatus === 'ok') {
        variant = 'online';
        statusLabel = 'Hoạt động';
      } else if (rawStatus === 'error') {
        variant = 'offline';
        statusLabel = 'Lỗi dịch vụ';
      }

      if (uptime) uptime.textContent = uptimeDisplay;

      let configSummary = 'Chưa kết nối';
      if (configRow && configText && data.config_assets) {
        const ca = data.config_assets;
        let color;
        if (ca.status) {
          configSummary = ca.status === 'ok' ? 'Bình thường' : ca.status === 'error' ? 'Có lỗi' : ca.status;
          color = ca.status === 'ok' ? 'var(--accent-green)' : 'var(--accent-amber)';
        } else if (ca.errors && ca.errors.length > 0) {
          configSummary = 'Có lỗi';
          color = 'var(--accent-amber)';
        } else {
          configSummary = 'Bình thường';
          color = 'var(--accent-green)';
        }
        configRow.style.display = '';
        configText.textContent = configSummary;
        configText.style.color = color;
      } else if (configRow && configText) {
        configRow.style.display = '';
        configText.textContent = configSummary;
        configText.style.color = 'var(--text-muted)';
      }

      const extraHint = rawStatus === 'unknown' && data?.web_api
        ? ' (Web API sẵn sàng · Chờ đồng bộ Watcher)'
        : '';
      const tooltipParts = [
        `Trạng thái: ${statusLabel}${extraHint}`,
        `Phiên bản: ${versionStr || '—'}`,
        `Uptime: ${uptimeDisplay}`,
        `Cấu hình: ${configSummary}`,
      ];
      setStatusPillView(variant, statusLabel, versionStr, tooltipParts.join(' · '));
    } catch {
      setStatusPillView('offline', 'Ngắt kết nối', '', 'Trạng thái: Ngắt kết nối máy chủ');
      if (uptime) uptime.textContent = '—';
      if (configRow) configRow.style.display = 'none';
    }
  }

  function startStatusPolling() {
    stopStatusPolling();
    fetchStatus();
    _statusInterval = setInterval(fetchStatus, 30000);
  }

  function stopStatusPolling() {
    if (_statusInterval) {
      clearInterval(_statusInterval);
      _statusInterval = null;
    }
  }

  return { render, navigate, updateActive, updateToggleIcon, updateThemeSwitch, fetchStatus, stopStatusPolling };
})();
