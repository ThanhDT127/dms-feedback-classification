/* ============================================================
   Confirm Dialog — hộp xác nhận trong trang, thay window.confirm
   Thanh xác nhận của trình duyệt khoá cả tab, không theo được
   giao diện tối/sáng và không đổi được câu chữ nút.
   ============================================================ */

window.Confirm = (() => {
  let open = null; // { overlay, resolve, lastFocus }

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text == null ? '' : String(text);
    return div.innerHTML;
  }

  function close(result) {
    if (!open) return;
    const { overlay, resolve, lastFocus } = open;
    open = null;
    document.removeEventListener('keydown', onKeydown, true);
    overlay.remove();
    // Trả tiêu điểm về đúng nút vừa mở hộp thoại, để người dùng bàn phím không bị lạc.
    if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
    resolve(result);
  }

  function onKeydown(event) {
    if (!open) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      close(false);
      return;
    }
    if (event.key !== 'Tab') return;
    // Giữ tiêu điểm quanh hai nút: hộp thoại là modal nên không cho tab ra nền.
    const focusable = open.overlay.querySelectorAll('button');
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || !open.overlay.contains(active))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  }

  /**
   * Hỏi xác nhận; trả Promise<boolean>.
   * @param {{title?: string, message?: string, confirmText?: string,
   *          cancelText?: string, danger?: boolean}} options
   */
  function ask(options) {
    const opts = options || {};
    // Đang mở một hộp khác thì coi như người dùng bỏ qua hộp cũ.
    if (open) close(false);

    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay confirm-overlay';
    overlay.innerHTML = `
      <div class="modal-content confirm-box" role="alertdialog" aria-modal="true"
           aria-labelledby="confirm-title" aria-describedby="confirm-message">
        <div class="modal-header">
          <h3 class="modal-title" id="confirm-title">${escapeHtml(opts.title || 'Xác nhận')}</h3>
        </div>
        <p class="confirm-message" id="confirm-message">${escapeHtml(opts.message || '')}</p>
        <div class="confirm-actions">
          <button type="button" class="btn btn-secondary btn-sm" data-confirm="no">${escapeHtml(opts.cancelText || 'Huỷ')}</button>
          <button type="button" class="btn ${opts.danger ? 'btn-danger' : 'btn-primary'} btn-sm" data-confirm="yes">${escapeHtml(opts.confirmText || 'Đồng ý')}</button>
        </div>
      </div>`;

    overlay.addEventListener('click', event => {
      if (event.target === overlay) close(false);
    });
    overlay.querySelector('[data-confirm="no"]').addEventListener('click', () => close(false));
    overlay.querySelector('[data-confirm="yes"]').addEventListener('click', () => close(true));

    document.body.appendChild(overlay);
    document.addEventListener('keydown', onKeydown, true);

    return new Promise(resolve => {
      open = { overlay, resolve, lastFocus: document.activeElement };
      // Việc xoá không hoàn tác được nên tiêu điểm đặt ở nút Huỷ: gõ Enter theo phản xạ
      // sẽ huỷ, không phải xoá.
      const initial = opts.danger ? '[data-confirm="no"]' : '[data-confirm="yes"]';
      overlay.querySelector(initial).focus();
    });
  }

  return { ask };
})();
