/* ============================================================
   Shared Password Controls
   ============================================================ */

window.PasswordControls = (() => {
  function normalizeValue(value) {
    return String(value || '')
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .replace(/[^\x20-\x7E]/g, '');
  }

  function normalizeInput(inputOrId, hintId) {
    const input = typeof inputOrId === 'string' ? document.getElementById(inputOrId) : inputOrId;
    if (!input) return '';

    const before = input.value;
    const after = normalizeValue(before);
    if (before !== after) {
      const pos = input.selectionStart || after.length;
      input.value = after;
      try {
        input.setSelectionRange(Math.min(pos, after.length), Math.min(pos, after.length));
      } catch (e) {
        // Some input types do not allow selection APIs.
      }
      const hint = hintId ? document.getElementById(hintId) : null;
      if (hint) {
        hint.classList.remove('hidden');
        window.setTimeout(() => hint.classList.add('hidden'), 2400);
      }
    }
    return input.value;
  }

  function toggleVisibility(inputId, buttonId) {
    const input = document.getElementById(inputId);
    const button = document.getElementById(buttonId);
    if (!input) return;

    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    if (button) {
      button.textContent = show ? '🙈' : '👁️';
      button.title = show ? 'Ẩn mật khẩu' : 'Hiện mật khẩu';
      button.setAttribute('aria-label', button.title);
    }
  }

  function renderInput(options = {}) {
    const id = options.id || 'password';
    const toggleId = options.toggleId || `${id}-toggle`;
    const hintId = options.hintId || `${id}-ascii-hint`;
    const placeholder = options.placeholder || 'Mật khẩu';
    const autocomplete = options.autocomplete || 'new-password';
    const required = options.required ? 'required' : '';
    const valueAttr = options.value ? `value="${escAttr(normalizeValue(options.value))}"` : '';

    return `
      <div class="password-control">
        <input type="password" id="${escAttr(id)}" class="form-input password-input"
               placeholder="${escAttr(placeholder)}" ${required} ${valueAttr}
               autocomplete="${escAttr(autocomplete)}" autocapitalize="off" spellcheck="false" inputmode="latin"
               oninput="PasswordControls.normalizeInput(this, '${escAttr(hintId)}')">
        <button type="button" id="${escAttr(toggleId)}" class="btn btn-ghost btn-sm password-toggle"
                onclick="PasswordControls.toggleVisibility('${escAttr(id)}', '${escAttr(toggleId)}')"
                title="Hiện mật khẩu" aria-label="Hiện mật khẩu">👁️</button>
      </div>
      <div id="${escAttr(hintId)}" class="password-ascii-hint hidden">
        Mật khẩu chỉ dùng ký tự tiếng Anh, số và ký tự đặc biệt ASCII.
      </div>
    `;
  }

  function escAttr(value) {
    return String(value).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  return { normalizeValue, normalizeInput, toggleVisibility, renderInput };
})();
