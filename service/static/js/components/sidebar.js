/* ============================================================
   Sidebar Navigation Component
   ============================================================ */

window.Sidebar = (() => {
  const NAV_ITEMS = [
    { id: 'classify',  icon: '⚡', label: 'Phân loại' },
    { id: 'files',     icon: '📂', label: 'Quản lý file' },
    { id: 'dashboard', icon: '📊', label: 'Tổng quan' },
    { id: 'metrics',   icon: '📈', label: 'Thống kê' },
    { id: 'pipeline',  icon: '🔬', label: 'Pipeline' },
    { id: 'qa',        icon: '📖', label: 'Hướng dẫn' },
    { id: 'settings',  icon: '⚙️', label: 'Cài đặt' },
  ];

  let _statusInterval = null;

  function render() {
    const sidebar = document.getElementById('sidebar');
    if (!sidebar) return;

    const user = App.state?.user;
    const role = user?.role || 'user';
    const items = NAV_ITEMS.filter(item => {
      if (role !== 'admin' && ['settings', 'pipeline'].includes(item.id)) return false;
      return true;
    });
    const isLight = document.documentElement.getAttribute('data-theme') === 'light';

    sidebar.innerHTML = `
      <div class="sidebar-brand">
        <div class="sidebar-brand-logo">
          <img class="sidebar-brand-avatar" src="assets/avatar.png" alt="Avatar">
          <div>
            <h1>Phân loại phản hồi tiếp thị</h1>
          </div>
        </div>
        <button class="theme-switch" id="theme-toggle-btn" role="switch"
                aria-checked="${isLight ? 'true' : 'false'}"
                onclick="App.toggleTheme()" title="Chuyển đổi giao diện">
          <span class="theme-switch-track"><span class="theme-switch-thumb"></span></span>
          <span class="theme-switch-icon">${isLight ? '☀️' : '🌙'}</span>
          <span class="theme-switch-label">${isLight ? 'Chế độ sáng' : 'Chế độ tối'}</span>
        </button>
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
          <span class="sidebar-status-label">
            <span class="status-dot" id="status-dot"></span>
            Trạng thái
          </span>
          <span id="status-text">Đang kiểm tra...</span>
        </div>
        <div class="sidebar-status-row">
          <span class="sidebar-status-label">🕐 Uptime</span>
          <span id="status-uptime">—</span>
        </div>
        <div class="sidebar-status-row">
          <span class="sidebar-status-label">📡 Phiên bản</span>
          <span id="status-version">—</span>
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
    const btn = document.getElementById('theme-toggle-btn');
    if (!btn) return;
    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
    btn.setAttribute('aria-checked', isLight ? 'true' : 'false');
    const icon = btn.querySelector('.theme-switch-icon');
    if (icon) icon.textContent = isLight ? '☀️' : '🌙';
    const label = btn.querySelector('.theme-switch-label');
    if (label) label.textContent = isLight ? 'Chế độ sáng' : 'Chế độ tối';
  }

  function updateToggleIcon() {
    updateThemeSwitch();
  }

  async function fetchStatus() {
    const dot = document.getElementById('status-dot');
    const text = document.getElementById('status-text');
    const uptime = document.getElementById('status-uptime');
    const version = document.getElementById('status-version');
    const configRow = document.getElementById('config-sync-row');
    const configText = document.getElementById('config-sync-text');

    if (!dot) return;

    try {
      const data = await API.getHealth({ silent: true });
      dot.className = 'status-dot online';
      text.textContent = 'Hoạt động';
      text.style.color = 'var(--accent-green)';
      uptime.textContent = data.uptime || '—';
      version.textContent = data.version || 'v2.0';

      if (configRow && configText && data.config_assets) {
        const ca = data.config_assets;
        let status, color;
        if (ca.status) {
          status = ca.status === 'ok' ? 'Bình thường' : ca.status === 'error' ? 'Có lỗi' : ca.status;
          color = ca.status === 'ok' ? 'var(--accent-green)' : 'var(--accent-orange, orange)';
        } else if (ca.errors && ca.errors.length > 0) {
          status = 'Có lỗi';
          color = 'var(--accent-orange, orange)';
        } else {
          status = 'Bình thường';
          color = 'var(--accent-green)';
        }
        const lastSync = ca.last_sync || ca.checked_at || '';
        configRow.style.display = '';
        configText.textContent = lastSync ? `${status}` : status;
        configText.style.color = color;
      } else if (configRow && configText) {
        configRow.style.display = '';
        configText.textContent = 'Chưa kết nối';
        configText.style.color = 'var(--text-muted)';
      }
    } catch (e) {
      dot.className = 'status-dot offline';
      text.textContent = 'Ngắt kết nối';
      text.style.color = 'var(--accent-red)';
      uptime.textContent = '—';
      version.textContent = '—';
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
