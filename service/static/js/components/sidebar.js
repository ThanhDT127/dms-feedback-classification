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
    { id: 'qa',        icon: '🛡️', label: 'Visual QA' },
    { id: 'settings',  icon: '⚙️', label: 'Cài đặt' },
  ];

  let _statusInterval = null;

  function render() {
    const sidebar = document.getElementById('sidebar');
    if (!sidebar) return;

    sidebar.innerHTML = `
      <div class="sidebar-brand">
        <div class="sidebar-brand-logo">
          <img class="sidebar-brand-avatar" src="assets/avatar.png" alt="Avatar">
          <div>
            <h1>Phân loại phản hồi</h1>
            <p>vấn đề khách hàng</p>
          </div>
        </div>
      </div>
      <nav class="sidebar-nav">
        ${NAV_ITEMS.map(item => `
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

  async function fetchStatus() {
    const dot = document.getElementById('status-dot');
    const text = document.getElementById('status-text');
    const uptime = document.getElementById('status-uptime');
    const version = document.getElementById('status-version');

    if (!dot) return;

    try {
      const data = await API.getHealth();
      dot.className = 'status-dot online';
      text.textContent = 'Hoạt động';
      text.style.color = 'var(--accent-green)';

      // uptime comes as a pre-formatted string like "1h 36m" from health.json
      uptime.textContent = data.uptime || '—';
      version.textContent = data.version || 'v2.0';
    } catch (e) {
      dot.className = 'status-dot offline';
      text.textContent = 'Ngắt kết nối';
      text.style.color = 'var(--accent-red)';
      uptime.textContent = '—';
      version.textContent = '—';
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

  return { render, navigate, updateActive, fetchStatus, stopStatusPolling };
})();
