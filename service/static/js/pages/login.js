/* ============================================================
   Login Page - Authentication UI
   ============================================================ */

window.LoginPage = (() => {
  let _loginInProgress = false;

  function render() {
    const app = document.getElementById('app');
    document.body.classList.add('login-page');

    app.innerHTML = `
      <div class="login-container">
        <div class="login-card">
          <div class="login-header">
            <img class="sidebar-brand-avatar" src="assets/avatar.png" alt="Logo"
                 style="margin:0 auto 16px;width:64px;height:64px;display:block;border-radius:50%;box-shadow:0 0 15px rgba(32,155,36,0.5);">
            <h1 style="font-size:22px;margin:0 0 4px;font-weight:700;color:var(--text-primary);">ĐĂNG NHẬP</h1>
            <p style="color:var(--text-muted);font-size:12px;margin:0;text-transform:uppercase;letter-spacing:1px;">Phân loại phản hồi tiếp thị</p>
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
    document.body.classList.remove('login-page');
  }

  return { render, destroy, handleLogin, normalizePasswordInput, togglePasswordVisibility };
})();
