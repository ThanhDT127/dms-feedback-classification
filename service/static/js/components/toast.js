/* ============================================================
   Toast Notification Component
   ============================================================ */

window.Toast = (() => {
  const ICONS = {
    info:    'ℹ️',
    success: '✅',
    error:   '❌',
    warning: '⚠️'
  };

  const DURATION = 5000;
  let queue = [];

  function show(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
      <span class="toast-icon">${ICONS[type] || ICONS.info}</span>
      <span class="toast-msg">${escapeHtml(message)}</span>
      <button class="toast-close" onclick="Toast.dismiss(this)" aria-label="Đóng">×</button>
    `;

    container.appendChild(toast);
    queue.push(toast);

    // Limit visible toasts
    while (queue.length > 5) {
      dismiss(queue[0].querySelector('.toast-close'));
    }

    // Auto-dismiss
    toast._timer = setTimeout(() => {
      removeToast(toast);
    }, DURATION);
  }

  function dismiss(btn) {
    const toast = btn.closest ? btn.closest('.toast') : btn.parentElement;
    if (toast) removeToast(toast);
  }

  function removeToast(toast) {
    clearTimeout(toast._timer);
    toast.classList.add('removing');
    setTimeout(() => {
      toast.remove();
      queue = queue.filter(t => t !== toast);
    }, 300);
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function success(msg) { show(msg, 'success'); }
  function error(msg)   { show(msg, 'error'); }
  function warning(msg) { show(msg, 'warning'); }
  function info(msg)    { show(msg, 'info'); }

  return { show, dismiss, success, error, warning, info };
})();
