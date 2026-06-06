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
  };

  /* ---- Page Registry ---- */
  const PAGES = {
    dashboard: { module: () => window.DashboardPage, title: 'Tổng quan' },
    files:     { module: () => window.FilesPage,     title: 'Quản lý file' },
    classify:  { module: () => window.ClassifyPage,  title: 'Phân loại' },
    settings:  { module: () => window.SettingsPage,  title: 'Cài đặt' },
    pipeline:  { module: () => window.PipelinePage,  title: 'Pipeline' },
    metrics:   { module: () => window.MetricsPage,   title: 'Thống kê' },
    qa:        { module: () => window.QAPage,        title: 'Visual QA (OpenDesign)' },
  };

  const DEFAULT_PAGE = 'dashboard';

  /* ---- Router ---- */
  function getHash() {
    const hash = window.location.hash.replace('#', '').split('?')[0];
    return hash || DEFAULT_PAGE;
  }

  function navigate(page) {
    window.location.hash = page;
  }

  function onHashChange() {
    const page = getHash();
    if (PAGES[page]) {
      renderPage(page);
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
    document.title = `Phân loại phản hồi vấn đề — ${def.title}`;

    // Update sidebar active
    if (window.Sidebar) {
      window.Sidebar.updateActive(pageName);
    }

    // Close mobile sidebar
    closeSidebar();

    // Transition: fade out, render, fade in
    const app = document.getElementById('app');
    if (!app) return;

    app.style.opacity = '0';
    app.style.transform = 'translateY(8px)';

    setTimeout(() => {
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

      // Fade in
      requestAnimationFrame(() => {
        app.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
        app.style.opacity = '1';
        app.style.transform = 'translateY(0)';
      });
    }, 150);
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
  function init() {
    // Render sidebar
    if (window.Sidebar) {
      window.Sidebar.render();
    }

    // Setup router
    window.addEventListener('hashchange', onHashChange);

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

  return {
    state, navigate, renderPage,
    toggleSidebar, closeSidebar,
    showModal, closeModal
  };
})();
