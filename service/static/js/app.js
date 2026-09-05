/* ============================================================
   App — SPA Router + State Management + Initialization
   ============================================================ */

window.App = (() => {
  /* ---- State ---- */
  const state = {
    currentPage: null,
    settings: {},
    jobs: [],
    metrics: {},
    sidebarOpen: false,
    theme: 'dark',
    user: null,
    isAuthenticated: false,
  };

  /* ---- Page Registry ---- */
  const PAGES = {
    login:     { module: () => window.LoginPage,     title: 'Đăng nhập' },
    analytics:  { module: () => window.AnalyticsPage, title: 'Dashboard' },
    classify:  { module: () => window.ClassifyPage,  title: 'Phân loại' },
    files:     { module: () => window.FilesPage,     title: 'Quản lý file' },
    dashboard: { module: () => window.DashboardPage, title: 'Tiến trình Job' },
    settings:  { module: () => window.SettingsPage,  title: 'Cài đặt' },
    pipeline:  { module: () => window.PipelinePage,  title: 'Pipeline' },
    metrics:   { module: () => window.MetricsPage,   title: 'Thống kê' },
    qa:        { module: () => window.QAPage,        title: 'Hướng dẫn sử dụng' },
  };

  const DEFAULT_PAGE = 'analytics';

  /* ---- Router ---- */
  function getHash() {
    const hash = window.location.hash.replace('#', '').split('?')[0];
    return hash || DEFAULT_PAGE;
  }

  function navigate(page) {
    window.location.hash = page;
  }

  function onHashChange() {
    const pageName = getHash();

    if (!state.isAuthenticated) {
      renderPage('login');
      return;
    }

    // Redirect away from login if already authenticated
    if (pageName === 'login') {
      window.location.hash = DEFAULT_PAGE;
      return;
    }

    // Admin-only pages
    if (['settings', 'pipeline'].includes(pageName) && state.user?.role !== 'admin') {
      navigate('classify');
      return;
    }

    if (PAGES[pageName]) {
      renderPage(pageName);
    } else {
      // Unknown route → default
      window.location.hash = DEFAULT_PAGE;
    }
  }

  function renderPage(pageName) {
    // Cleanup previous page
    if (state.currentPage && state.currentPage !== pageName) {
      const prevDef = PAGES[state.currentPage];
      if (prevDef) {
        const prevMod = prevDef.module();
        if (prevMod && typeof prevMod.destroy === 'function') {
          prevMod.destroy();
        }
      }
    }

    state.currentPage = pageName;

    // Update document title
    const def = PAGES[pageName];
    document.title = `Phân loại phản hồi tiếp thị — ${def.title}`;

    // Update sidebar active
    if (window.Sidebar) {
      window.Sidebar.updateActive(pageName);
    }

    // Close mobile sidebar
    closeSidebar();

    // Render immediately without artificial delay
    const app = document.getElementById('app');
    if (!app) return;

    const mod = def.module();
    if (mod && typeof mod.render === 'function') {
      mod.render();
    } else {
      app.innerHTML = `
        <div class="empty-state" style="padding:80px 20px;">
          <div class="empty-state-icon">🚧</div>
          <p class="empty-state-text">Trang đang được xây dựng</p>
        </div>
      `;
    }
  }

  /* ---- Sidebar toggle (mobile) ---- */
  function toggleSidebar() {
    state.sidebarOpen = !state.sidebarOpen;
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebar-overlay');

    if (sidebar) sidebar.classList.toggle('open', state.sidebarOpen);
    if (overlay) overlay.classList.toggle('open', state.sidebarOpen);
  }

  function closeSidebar() {
    state.sidebarOpen = false;
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebar-overlay');
    if (sidebar) sidebar.classList.remove('open');
    if (overlay) overlay.classList.remove('open');
  }

  /* ---- Theme ---- */
  function initTheme() {
    let theme = localStorage.getItem('dms-theme');
    const hasManualPref = !!theme;
    if (!theme) {
      theme = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    }
    state.theme = theme;
    applyTheme(theme, true);

    // Listen for system preference changes (only when no manual pref)
    if (!hasManualPref) {
      window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', (e) => {
        if (!localStorage.getItem('dms-theme')) {
          const newTheme = e.matches ? 'light' : 'dark';
          state.theme = newTheme;
          applyTheme(newTheme);
          if (window.Sidebar) window.Sidebar.updateToggleIcon();
          if (window.Charts && typeof window.Charts.applyThemeColors === 'function') window.Charts.applyThemeColors();
        }
      });
    }
  }

  function toggleTheme() {
    const newTheme = state.theme === 'dark' ? 'light' : 'dark';
    state.theme = newTheme;
    localStorage.setItem('dms-theme', newTheme);
    applyTheme(newTheme);
    if (window.Sidebar) window.Sidebar.updateToggleIcon();
    if (window.Charts && typeof window.Charts.applyThemeColors === 'function') {
      requestAnimationFrame(() => window.Charts.applyThemeColors());
    }
  }

  function applyTheme(theme, isInitial) {
    if (isInitial) document.documentElement.classList.add('no-transition');
    document.documentElement.setAttribute('data-theme', theme);
    const meta = document.getElementById('meta-theme-color');
    if (meta) meta.content = theme === 'light' ? '#f8fafc' : '#0f1117';
    if (isInitial) {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          document.documentElement.classList.remove('no-transition');
        });
      });
    }
  }

  /* ---- Modal ---- */
  function showModal(content) {
    const overlay = document.getElementById('modal-overlay');
    const container = document.getElementById('modal-content');
    if (overlay && container) {
      container.innerHTML = content;
      overlay.style.display = 'flex';
    }
  }

  function closeModal() {
    const overlay = document.getElementById('modal-overlay');
    if (overlay) overlay.style.display = 'none';
  }

  /* ---- Init ---- */
  async function init() {
    initTheme();

    // Check auth state
    const token = API.getAccessToken();
    if (token) {
      try {
        const user = await API.get('/auth/me', { silent: true });
        state.user = user;
        state.isAuthenticated = true;
      } catch {
        API.clearTokens();
        state.user = null;
        state.isAuthenticated = false;
      }
    }

    // Setup router
    window.addEventListener('hashchange', onHashChange);

    // Listen for auth:expired
    window.addEventListener('auth:expired', () => logout());

    // Modal close on overlay click
    const overlay = document.getElementById('modal-overlay');
    if (overlay) {
      overlay.addEventListener('click', (e) => {
        if (e.target === overlay) closeModal();
      });
    }

    // Keyboard: Escape closes modal
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        closeModal();
        closeSidebar();
      }
    });

    if (!state.isAuthenticated) {
      renderPage('login');
      return;
    }

    // Render sidebar
    if (window.Sidebar) {
      window.Sidebar.render();
    }

    // Initial route
    if (!window.location.hash) {
      window.location.hash = DEFAULT_PAGE;
    } else {
      onHashChange();
    }
  }

  /* ---- Bootstrap ---- */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  function setUser(user) {
    state.user = user;
    state.isAuthenticated = true;
  }

  function logout() {
    if (state.isAuthenticated && API.logout) {
      API.logout({ silent: true }).catch(() => {});
    }
    API.clearTokens();
    state.user = null;
    state.isAuthenticated = false;
    renderPage('login');
  }

  return {
    state, navigate, renderPage,
    toggleSidebar, closeSidebar,
    showModal, closeModal,
    toggleTheme,
    setUser, logout
  };
})();
