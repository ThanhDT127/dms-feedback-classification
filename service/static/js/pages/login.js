/* ============================================================
   Login Page - Authentication UI
   ============================================================ */

window.LoginPage = (() => {
  let _loginInProgress = false;
  let _mounted = false;

  const THEME_OPTIONS = [
    { id: 'dark', icon: '🌙', label: 'Tối' },
    { id: 'soft', icon: '🌿', label: 'Dịu' },
    { id: 'light', icon: '☀️', label: 'Sáng' },
  ];

  function formatVersion(raw) {
    if (!raw) return '';
    const trimmed = String(raw).trim();
    if (!trimmed) return '';
    return trimmed.startsWith('v') ? trimmed : `v${trimmed}`;
  }

  function getActiveTheme() {
    return document.documentElement.getAttribute('data-theme') || App?.state?.theme || 'dark';
  }

  function updateThemeControl() {
    const container = document.getElementById('login-theme-segmented');
    if (!container) return;
    const activeTheme = getActiveTheme();
    container.querySelectorAll('[data-theme-option]').forEach((btn) => {
      const isChecked = btn.getAttribute('data-theme-option') === activeTheme;
      btn.setAttribute('aria-checked', isChecked ? 'true' : 'false');
      btn.classList.toggle('active', isChecked);
    });
  }

  function applyPublicStatus(variant, statusText, versionText) {
    const pill = document.getElementById('login-status-pill');
    const dot = document.getElementById('login-status-dot');
    const verEl = document.getElementById('login-status-version');
    const sepEl = document.getElementById('login-status-sep');
    const textEl = document.getElementById('login-status-text');
    if (!pill || !dot || !textEl) return;

    pill.className = `brand-status-pill ${variant}`;
    dot.className = `status-dot ${variant}`;
    textEl.textContent = statusText;

    if (verEl && sepEl) {
      if (versionText) {
        verEl.textContent = versionText;
        verEl.style.display = '';
        sepEl.style.display = '';
      } else {
        verEl.textContent = '';
        verEl.style.display = 'none';
        sepEl.style.display = 'none';
      }
    }
  }

  async function fetchPublicStatus() {
    if (!window.API || typeof API.getPublicHealth !== 'function') return;
    try {
      const data = await API.getPublicHealth({ silent: true });
      if (!_mounted) return;
      const rawStatus = String(data?.status || 'ok').toLowerCase();
      const versionStr = formatVersion(data?.version);
      if (rawStatus === 'ok') {
        applyPublicStatus('online', 'Sẵn sàng', versionStr);
      } else if (rawStatus === 'error') {
        applyPublicStatus('offline', 'Lỗi dịch vụ', versionStr);
      } else {
        applyPublicStatus('warning', 'Cảnh báo', versionStr);
      }
    } catch {
      if (!_mounted) return;
      applyPublicStatus('offline', 'Mất kết nối', '');
    }
  }

  function render() {
    const app = document.getElementById('app');
    document.body.classList.add('login-page');
    _mounted = true;

    const currentTheme = getActiveTheme();

    app.innerHTML = `
      <div class="login-container">
        <div class="login-card">
          <div class="login-top-actions">
            <div class="theme-segmented theme-segmented-compact" id="login-theme-segmented" role="radiogroup" aria-label="Chế độ màu giao diện">
              ${THEME_OPTIONS.map((opt) => {
                const active = currentTheme === opt.id;
                return `<button type="button" class="theme-segmented-btn${active ? ' active' : ''}" role="radio" data-theme-option="${opt.id}" aria-checked="${active ? 'true' : 'false'}" onclick="App.setTheme('${opt.id}')"><span>${opt.icon}</span><span>${opt.label}</span></button>`;
              }).join('')}
            </div>
          </div>
          <div class="login-header">
            <img class="sidebar-brand-avatar" src="assets/avatar.png" alt="Hệ thống phân loại phản hồi Rạng Đông"
                 style="margin:0 auto 14px;width:60px;height:60px;display:block;border-radius:50%;box-shadow:0 0 14px rgba(32,155,36,0.35);">
            <h1 class="login-brand-title" aria-label="Hệ thống phân loại phản hồi Rạng Đông">Hệ thống phân loại<br>phản hồi Rạng Đông</h1>
            <div class="login-status-wrap">
              <div class="brand-status-pill" id="login-status-pill" role="status" aria-live="polite">
                <span class="status-dot" id="login-status-dot"></span>
                <span class="brand-status-version" id="login-status-version" style="display:none;"></span>
                <span class="brand-status-sep" id="login-status-sep" style="display:none;">·</span>
                <span class="brand-status-text" id="login-status-text">Đang kiểm tra...</span>
              </div>
            </div>
          </div>
          <form id="login-form" onsubmit="LoginPage.handleLogin(event)">
            <div class="form-group" style="margin-bottom:16px;">
              <label class="form-label" style="font-size:11px;text-transform:uppercase;letter-spacing:0.5px;color:var(--text-muted);">Tên đăng nhập</label>
              <input type="text" id="login-username" class="form-input" placeholder="Nhập tên đăng nhập" required autofocus
                     autocomplete="username" style="font-size:14px;padding:10px 12px;border-radius:var(--radius-md);">
            </div>
            <div class="form-group" style="margin-bottom:20px;">
              <label class="form-label" style="font-size:11px;text-transform:uppercase;letter-spacing:0.5px;color:var(--text-muted);">Mật khẩu</label>
              ${PasswordControls.renderInput({
                id: 'login-password',
                toggleId: 'login-password-toggle',
                hintId: 'login-password-ascii-hint',
                placeholder: 'Nhập mật khẩu',
                autocomplete: 'current-password',
                required: true,
              })}
            </div>
            <div id="login-error" class="hidden"
                 style="color:var(--accent-red);font-size:12px;margin-bottom:12px;padding:8px;background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.2);border-radius:var(--radius-sm);"></div>
            <button type="submit" class="btn btn-primary" id="login-btn"
                    style="width:100%;padding:11px;font-size:14px;font-weight:600;border-radius:var(--radius-md);box-shadow:var(--shadow-glow-blue);">
              🔑 Đăng nhập
            </button>
          </form>
        </div>
      </div>
    `;

    fetchPublicStatus();
  }

  function normalizePasswordInput(input) {
    return PasswordControls.normalizeInput(input, 'login-password-ascii-hint');
  }

  function togglePasswordVisibility() {
    PasswordControls.toggleVisibility('login-password', 'login-password-toggle');
    document.getElementById('login-password')?.focus();
  }

  function loginErrorMessage(err) {
    const raw = String(err?.message || '').toLowerCase();
    if (raw.includes('invalid credentials') || raw.includes('không đúng')) {
      return 'Tên đăng nhập hoặc mật khẩu không đúng.';
    }
    if (raw.includes('username and password required')) {
      return 'Vui lòng nhập tên đăng nhập và mật khẩu.';
    }
    if (raw.includes('inactive')) {
      return 'Tài khoản đã bị vô hiệu hóa. Vui lòng liên hệ admin.';
    }
    return err?.message || 'Đăng nhập thất bại';
  }

  async function handleLogin(e) {
    e.preventDefault();
    if (_loginInProgress) return;

    const username = document.getElementById('login-username')?.value?.trim();
    const password = PasswordControls.normalizeInput('login-password', 'login-password-ascii-hint');
    const errorEl = document.getElementById('login-error');
    const btn = document.getElementById('login-btn');

    if (!username || !password) {
      if (errorEl) {
        errorEl.textContent = 'Vui lòng nhập tên đăng nhập và mật khẩu.';
        errorEl.classList.remove('hidden');
      }
      return;
    }

    _loginInProgress = true;
    btn.disabled = true;
    btn.textContent = '⏳ Đang đăng nhập...';
    if (errorEl) {
      errorEl.classList.add('hidden');
      errorEl.textContent = '';
    }

    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      const data = await res.json();

      if (!res.ok) {
        throw new Error(data.detail || 'Đăng nhập thất bại');
      }

      API.setTokens(data.access_token, data.refresh_token);
      App.setUser(data.user);

      if (typeof ClassifyPage !== 'undefined' && ClassifyPage.reset) {
        ClassifyPage.reset();
      }

      const sidebar = document.getElementById('sidebar');
      const topbar = document.querySelector('.topbar');
      const overlay = document.querySelector('.sidebar-overlay');
      if (sidebar) sidebar.style.display = '';
      if (topbar) topbar.style.display = '';
      if (overlay) overlay.style.display = '';

      document.body.classList.remove('login-page');
      window.location.hash = '#classify';
      App.renderPage('classify');
      Sidebar.render();
    } catch (err) {
      if (errorEl) {
        errorEl.textContent = loginErrorMessage(err);
        errorEl.classList.remove('hidden');
      }
    } finally {
      _loginInProgress = false;
      btn.disabled = false;
      btn.textContent = '🔑 Đăng nhập';
    }
  }

  function destroy() {
    _mounted = false;
    document.body.classList.remove('login-page');
  }

  return {
    render,
    destroy,
    handleLogin,
    normalizePasswordInput,
    togglePasswordVisibility,
    updateThemeControl,
  };
})();
