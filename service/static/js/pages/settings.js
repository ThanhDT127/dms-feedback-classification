/* ============================================================
   Settings Page — Model, Prompt, Pipeline, SharePoint, Notifications
   ============================================================ */

window.SettingsPage = (() => {
  let _activeTab = 'model';
  let _activeSubTab = 'prompt_text';
  let _activeProductSheet = 'Lọc lần 1';
  let _settings = null;
  let _models = [];
  let _prompt = '';
  let _rawKeywords = null;
  let _productsData = null; // Map of sheet names to product lists
  let _productsColumns = {}; // Map of sheet names to columns
  let _productsSheetNames = ['Lọc lần 1', 'Lọc lần 2', 'Lọc lần 3'];

  let _editStates = {
    prompt: false,
    keywords: false,
    products: false
  };

  let _backups = {
    prompt: '',
    keywords: null,
    products: {}
  };

  const TABS = [
    { id: 'model',    icon: '🤖', label: 'Model' },
    { id: 'prompt',   icon: '📝', label: 'Prompt / Dữ liệu' },
    { id: 'pipeline', icon: '🔧', label: 'Pipeline' },
    { id: 'labels',   icon: '🏷️', label: 'Nhãn' },
    { id: 'sharepoint', icon: '🔑', label: 'SharePoint' },
    { id: 'notify',   icon: '🔔', label: 'Thông báo' },
  ];

  // Label history state
  let _labelHistory = [];
  let _labelHistoryOffset = 0;
  let _labelHistoryHasMore = false;

  function render() {
    const app = document.getElementById('app');
    const isAdmin = App.state?.user?.role === 'admin';
    const tabs = isAdmin ? [...TABS, { id: 'users', icon: '👥', label: 'Người dùng' }] : TABS;
    app.innerHTML = `
      <div class="page-header">
        <h2>⚙️ Cài đặt</h2>
        <p>Cấu hình hệ thống phân loại phản hồi</p>
      </div>

      <!-- Tabs -->
      <div class="tabs" id="settings-tabs">
        ${tabs.map(t => `
          <div class="tab-item ${t.id === _activeTab ? 'active' : ''}"
               data-tab="${t.id}"
               onclick="SettingsPage.switchTab('${t.id}')">
            ${t.icon} ${t.label}
          </div>
        `).join('')}
      </div>

      <!-- Tab Content -->
      <div id="settings-content">
        <div class="text-center" style="padding:40px;"><span class="spinner lg"></span></div>
      </div>
    `;

    loadSettings();
  }

  async function loadSettings() {
    try {
      const [settings, models, prompt] = await Promise.allSettled([
        API.getSettings(),
        API.getModels(),
        API.getPrompt()
      ]);

      _settings = settings.status === 'fulfilled' ? settings.value : {};
      _models = models.status === 'fulfilled' ? (models.value.models || models.value || []) : [];
      _prompt = prompt.status === 'fulfilled' ? (prompt.value?.raw_template || prompt.value?.prompt_template || '') : '';

      renderTab();
    } catch (e) {
      document.getElementById('settings-content').innerHTML = `
        <div class="empty-state"><div class="empty-state-icon">⚠️</div>
          <p class="empty-state-text">Không thể tải cài đặt</p>
          <p class="empty-state-hint">${esc(e.message)}</p>
        </div>
      `;
    }
  }

  function switchTab(tab) {
    _activeTab = tab;
    document.querySelectorAll('#settings-tabs .tab-item').forEach(t => {
      t.classList.toggle('active', t.dataset.tab === tab);
    });
    renderTab();
  }

  function renderTab() {
    const el = document.getElementById('settings-content');
    if (!el) return;

    switch (_activeTab) {
      case 'model':      el.innerHTML = renderModelTab(); break;
      case 'prompt':     
        el.innerHTML = renderPromptTab(); 
        switchSubTab(_activeSubTab);
        break;
      case 'pipeline':   el.innerHTML = renderPipelineTab(); break;
      case 'labels':     el.innerHTML = renderLabelsTab(); loadLabelHistory(); break;
      case 'sharepoint': el.innerHTML = renderSharePointTab(); break;
      case 'notify':     
        el.innerHTML = renderNotifyTab(); 
        updateEmailCount();
        break;
      case 'users':      el.innerHTML = renderUsersTab(); loadUsers(); break;
    }
  }

  /* ---- Model Tab ---- */
  function renderModelTab() {
    const s = _settings || {};
    const backend = s.gemini_backend || s.backend || s.llm_backend || 'vertex';
    const model = s.gemini_model || s.model || s.llm_model || '';

    // Normalize backend value for radio buttons
    const isVertex = backend === 'vertex' || backend === 'vertex_ai';
    const isApiKey = backend === 'apikey' || backend === 'api_key';

    return `
      <div class="card animate-in" style="max-width:700px;">
        <div class="card-header">
          <span class="card-title"><span class="icon">🤖</span> Cấu hình Model AI</span>
        </div>

        <!-- Backend selection -->
        <div class="form-group">
          <label class="form-label">Backend</label>
          <div class="radio-group" style="display:flex;gap:20px;margin-top:8px;">
            <label class="radio-item" style="display:flex;align-items:center;gap:6px;cursor:pointer;">
              <input type="radio" name="backend" value="vertex" ${isVertex ? 'checked' : ''} onchange="SettingsPage.onBackendChange()">
              Vertex AI
            </label>
            <label class="radio-item" style="display:flex;align-items:center;gap:6px;cursor:pointer;">
              <input type="radio" name="backend" value="apikey" ${isApiKey ? 'checked' : ''} onchange="SettingsPage.onBackendChange()">
              API Key
            </label>
          </div>
        </div>

        <!-- Model selection -->
        <div class="form-group">
          <label class="form-label">Model</label>
          <select class="form-select" id="set-model">
            ${_models.length > 0
              ? _models.map(m => {
                  const mId = typeof m === 'string' ? m : m.id || m.name;
                  const mName = typeof m === 'string' ? m : m.name || m.id;
                  return `<option value="${esc(mId)}" ${mId === model ? 'selected' : ''}>${esc(mName)}</option>`;
                }).join('')
              : `<option value="${esc(model)}">${esc(model || 'Không có model')}</option>`
            }
          </select>
        </div>

        <!-- API Key fields -->
        <div id="apikey-fields" ${!isApiKey ? 'class="hidden"' : ''}>
          <div class="form-group">
            <label class="form-label">API Key</label>
            <div style="display:flex;gap:8px;align-items:center;">
              <input type="password" class="form-input" id="set-apikey" value="${esc(s.gemini_api_key || s.api_key || '')}" placeholder="••••••••••••">
              <button type="button" class="btn btn-secondary btn-sm" id="btn-reveal-apikey" onclick="SettingsPage.revealApiKey()" title="Hiện/ẩn API key" style="white-space:nowrap;">👁️ Hiện</button>
            </div>
            <span class="form-hint" style="font-size:11px;color:var(--text-muted);">Key được ẩn trên giao diện; file .env cần được bảo vệ quyền truy cập</span>
          </div>
        </div>

        <!-- Test & Save -->
        <div style="display:flex;gap:12px;margin-top:24px;">
          <button class="btn btn-secondary" id="btn-test-conn" onclick="SettingsPage.testConnection()">
            🔌 Kiểm tra kết nối
          </button>
          <button class="btn btn-primary" onclick="SettingsPage.saveModelSettings()">
            💾 Lưu cài đặt Model
          </button>
        </div>

        <!-- Test result -->
        <div id="test-result" class="hidden" style="margin-top:16px;"></div>
      </div>
    `;
  }

  function onBackendChange() {
    const backend = document.querySelector('input[name="backend"]:checked')?.value || 'vertex';
    const apikeyFields = document.getElementById('apikey-fields');
    if (apikeyFields) {
      apikeyFields.classList.toggle('hidden', backend !== 'apikey');
    }
  }

  async function testConnection() {
    const btn = document.getElementById('btn-test-conn');
    const result = document.getElementById('test-result');
    if (!btn) return;

    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Đang kiểm tra...';

    const data = gatherModelSettings();

    try {
      const res = await API.testConnection(data);
      result.classList.remove('hidden');
      if (res.success) {
        result.innerHTML = `
          <div style="padding:12px;border-radius:var(--radius-md);background:rgba(34,197,94,0.1);border:1px solid rgba(34,197,94,0.3);color:var(--accent-green);font-size:13px;">
            ✅ ${res.message || 'Kết nối thành công!'} (${res.response_time_ms}ms)
          </div>
        `;
      } else {
        result.innerHTML = `
          <div style="padding:12px;border-radius:var(--radius-md);background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);color:var(--accent-red);font-size:13px;">
            ❌ Kết nối thất bại: ${esc(res.message)}
          </div>
        `;
      }
    } catch (e) {
      result.classList.remove('hidden');
      result.innerHTML = `
        <div style="padding:12px;border-radius:var(--radius-md);background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);color:var(--accent-red);font-size:13px;">
          ❌ Lỗi hệ thống: ${esc(e.message)}
        </div>
      `;
    } finally {
      btn.disabled = false;
      btn.innerHTML = '🔌 Kiểm tra kết nối';
    }
  }

  function gatherModelSettings() {
    const backend = document.querySelector('input[name="backend"]:checked')?.value || 'vertex';
    const data = {
      backend,
      model: document.getElementById('set-model')?.value,
    };
    const apiKeyEl = document.getElementById('set-apikey');
    if (apiKeyEl) {
      data.api_key = apiKeyEl.value;
    }
    return data;
  }

  async function revealApiKey() {
    const input = document.getElementById('set-apikey');
    const btn = document.getElementById('btn-reveal-apikey');
    if (!input || !btn) return;

    if (input.type === 'text') {
      input.type = 'password';
      btn.textContent = '👁️ Hiện';
      return;
    }

    try {
      btn.disabled = true;
      btn.textContent = 'Đang tải...';
      const res = await API.getSecret('gemini_api_key');
      input.value = res.value || '';
      input.type = 'text';
      btn.textContent = '🙈 Ẩn';
      if (!res.value) Toast.info('Chưa có API key được lưu');
    } catch (e) {
      Toast.error('Không thể hiện API key: ' + e.message);
      btn.textContent = '👁️ Hiện';
    } finally {
      btn.disabled = false;
    }
  }

  async function saveModelSettings() {
    const data = gatherModelSettings();
    try {
      const res = await API.putSettings(data);
      Object.assign(_settings, data);
      Toast.success('Đã lưu cấu hình Model AI thành công');
      // Reload setting in background
      loadSettings();
    } catch (e) {
      Toast.error('Lỗi lưu cấu hình: ' + e.message);
    }
  }

  /* ---- Edit Banner Helper ---- */
  function renderEditBanner(section) {
    if (!_editStates[section]) return '';
    return `
      <div class="animate-in" style="margin-bottom:16px;padding:10px 14px;border-radius:6px;background:rgba(217,119,6,0.12);border:1px solid rgba(217,119,6,0.3);color:#fbbf24;font-size:12px;font-weight:500;display:flex;align-items:center;gap:8px;">
        ⚠️ Đang ở chế độ chỉnh sửa — Các thay đổi chưa được lưu vào file hệ thống.
      </div>
    `;
  }

  /* ---- Prompt Tab & Sub-tabs ---- */
  function renderPromptTab() {
    return `
      <div class="card animate-in" style="max-width:900px; padding: 24px;">
        <!-- Sub-tabs bar -->
        <div class="sub-tabs animate-in" style="display:flex;gap:12px;margin-bottom:20px;border-bottom:1px solid var(--border);padding-bottom:12px;">
          <div class="sub-tab-item ${_activeSubTab === 'prompt_text' ? 'active' : ''}" 
               style="cursor:pointer;padding:6px 16px;font-size:13px;font-weight:600;border-radius:4px;transition:all 0.2s;"
               data-subtab="prompt_text" onclick="SettingsPage.switchSubTab('prompt_text')">
            📝 System Prompt
          </div>
          <div class="sub-tab-item ${_activeSubTab === 'keywords' ? 'active' : ''}" 
               style="cursor:pointer;padding:6px 16px;font-size:13px;font-weight:600;border-radius:4px;transition:all 0.2s;"
               data-subtab="keywords" onclick="SettingsPage.switchSubTab('keywords')">
            🔑 Từ khóa gợi ý (Keywords)
          </div>
          <div class="sub-tab-item ${_activeSubTab === 'products' ? 'active' : ''}" 
               style="cursor:pointer;padding:6px 16px;font-size:13px;font-weight:600;border-radius:4px;transition:all 0.2s;"
               data-subtab="products" onclick="SettingsPage.switchSubTab('products')">
            📦 Danh mục sản phẩm (Excel)
          </div>
        </div>

        <div id="sub-tab-content" style="margin-top:16px;">
          <!-- Dynamically populated -->
        </div>
      </div>
    `;
  }

  function switchSubTab(subtab) {
    _activeSubTab = subtab;
    document.querySelectorAll('.sub-tab-item').forEach(t => {
      const active = t.dataset.subtab === subtab;
      t.classList.toggle('active', active);
      t.style.color = active ? 'var(--accent-blue)' : 'var(--text-muted)';
      t.style.background = active ? 'rgba(59,130,246,0.1)' : 'transparent';
    });
    renderSubTabContent();
  }

  function renderSubTabContent() {
    const container = document.getElementById('sub-tab-content');
    if (!container) return;

    if (_activeSubTab === 'prompt_text') {
      container.innerHTML = renderPromptTextSubTab();
    } else if (_activeSubTab === 'keywords') {
      container.innerHTML = renderKeywordsSubTab();
    } else if (_activeSubTab === 'products') {
      container.innerHTML = renderProductsSubTab();
    }
  }

  function renderPromptTextSubTab() {
    const isEditing = _editStates.prompt;
    const wordCount = _prompt ? _prompt.split(/\s+/).length : 0;
    const tokenEstimate = Math.round(wordCount * 1.3);

    return `
      <div class="animate-in">
        ${renderEditBanner('prompt')}
        
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
          <span style="font-size:13px;font-weight:600;color:var(--text-muted);">
            Mẫu Prompt của Classifier ${isEditing ? '<span style="color:#fbbf24;font-size:11px;font-weight:normal;margin-left:8px;">(Chế độ chỉnh sửa)</span>' : ''}
          </span>
          <div class="btn-group" style="display:flex;gap:8px;">
            <button class="btn btn-ghost btn-sm" onclick="SettingsPage.copyPrompt()">📋 Sao chép</button>
            ${!isEditing 
              ? `<button class="btn btn-secondary btn-sm" onclick="SettingsPage.toggleEditPrompt()">✏️ Chỉnh sửa</button>`
              : `
                <button class="btn btn-secondary btn-sm" onclick="SettingsPage.cancelEditPrompt()">🔙 Quay lại</button>
                <button class="btn btn-primary btn-sm" onclick="SettingsPage.savePrompt()">💾 Lưu thay đổi</button>
              `
            }
          </div>
        </div>

        <textarea class="form-textarea code" id="prompt-textarea" ${!isEditing ? 'readonly' : ''}
                  style="min-height:380px;line-height:1.6;font-family:monospace;font-size:12px;width:100%;border-radius:6px;background:rgba(0,0,0,0.2);${isEditing ? 'border-color:var(--accent-blue);box-shadow:0 0 0 2px rgba(59,130,246,0.2);' : ''}">${esc(_prompt)}</textarea>

        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:12px;">
          <div style="display:flex;gap:16px;font-size:11px;color:var(--text-muted);">
            <span>📝 ${wordCount.toLocaleString()} từ</span>
            <span>🔤 ${tokenEstimate.toLocaleString()} tokens (ước tính)</span>
            <span>📏 ${_prompt.length.toLocaleString()} ký tự</span>
          </div>
        </div>
      </div>
    `;
  }

  function toggleEditPrompt() {
    _editStates.prompt = true;
    _backups.prompt = _prompt;
    renderSubTabContent();
  }

  function cancelEditPrompt() {
    _prompt = _backups.prompt;
    _editStates.prompt = false;
    renderSubTabContent();
  }

  function copyPrompt() {
    const textarea = document.getElementById('prompt-textarea');
    if (textarea) {
      navigator.clipboard.writeText(textarea.value)
        .then(() => Toast.success('Đã sao chép prompt'))
        .catch(() => Toast.error('Không thể sao chép'));
    }
  }

  async function savePrompt() {
    const textarea = document.getElementById('prompt-textarea');
    if (!textarea) return;

    try {
      await API.put('/settings/prompt', { prompt: textarea.value });
      _prompt = textarea.value;
      _editStates.prompt = false;
      Toast.success('Đã lưu System Prompt thành công');
      renderSubTabContent();
    } catch (e) {
      Toast.error('Lỗi lưu prompt: ' + e.message);
    }
  }

  /* ---- Keywords Sub-tab ---- */
  async function loadRawKeywords() {
    if (_rawKeywords) return;
    try {
      _rawKeywords = await API.get('/pipeline/keywords/raw');
      renderSubTabContent();
    } catch (e) {
      Toast.error('Không thể tải từ khóa gợi ý: ' + e.message);
    }
  }

  function renderKeywordsSubTab() {
    if (!_rawKeywords) {
      loadRawKeywords();
      return `<div class="text-center" style="padding:40px;"><span class="spinner"></span> Đang tải từ khóa...</div>`;
    }

    const isEditing = _editStates.keywords;
    const categories = Object.keys(_rawKeywords).filter(k => k !== 'manual_brand_alias');
    
    return `
      <div class="animate-in">
        ${renderEditBanner('keywords')}

        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
          <p class="form-hint" style="color:var(--text-muted); font-size:12px; margin:0;">
            Chỉnh sửa từ khóa gợi ý cho từng nhãn phân loại. Nhập các từ khóa cách nhau bằng dấu phẩy.
          </p>
          <div class="btn-group" style="display:flex;gap:8px;">
            ${!isEditing 
              ? `
                <button class="btn btn-secondary btn-sm" onclick="SettingsPage.toggleEditKeywords()">✏️ Chỉnh sửa</button>
                <button class="btn btn-sm" id="btn-sync-keywords" style="background:rgba(59,130,246,0.15);color:var(--accent-blue);border:1px solid rgba(59,130,246,0.3);" 
                        onclick="SettingsPage.syncKeywordsToSP()">☁️ Đồng bộ SharePoint</button>
              `
              : `
                <button class="btn btn-success btn-sm" onclick="SettingsPage.focusFirstKeywordInput()">➕ Thêm từ khóa</button>
                <button class="btn btn-secondary btn-sm" onclick="SettingsPage.addKeywordGroup()">➕ Nhóm mới</button>
                <button class="btn btn-secondary btn-sm" onclick="SettingsPage.cancelEditKeywords()">🔙 Quay lại</button>
                <button class="btn btn-primary btn-sm" onclick="SettingsPage.saveKeywords()">💾 Lưu thay đổi</button>
                <button class="btn btn-sm" id="btn-sync-keywords" style="background:rgba(59,130,246,0.15);color:var(--accent-blue);border:1px solid rgba(59,130,246,0.3);" 
                        onclick="SettingsPage.syncKeywordsToSP()">☁️ Đồng bộ SharePoint</button>
              `
            }
          </div>
        </div>

        <div style="margin-bottom:12px;">
          <input type="text" class="form-input" placeholder="🔍 Lọc nhóm hoặc từ khóa..."
                 oninput="SettingsPage.filterKeywordGroups(this.value)"
                 style="font-size:12px;padding:8px 10px;">
        </div>

        <div id="kw-groups-container" style="max-height: 400px; overflow-y: auto; padding-right: 8px;">
          ${categories.map(cat => {
            const keywords = _rawKeywords[cat] || [];
            return `
            <div class="form-group mb-4 kw-group" data-group="${esc(cat)}" 
                 ${isEditing ? 'draggable="true"' : ''}
                 style="border-bottom:1px solid rgba(255,255,255,0.05); padding-bottom:12px; margin-bottom:16px;"
                 ${isEditing ? `ondragstart="SettingsPage._kwDragStart(event)" ondragover="SettingsPage._kwDragOver(event)" ondrop="SettingsPage._kwDrop(event)" ondragend="SettingsPage._kwDragEnd(event)"` : ''}>
              <label class="form-label" style="font-weight:600; display:flex; justify-content:space-between; align-items:center; font-size:12px;">
                <span style="display:flex;align-items:center;gap:6px;">
                  ${isEditing ? '<span class="kw-drag-handle">☰</span>' : ''}
                  🏷️ <span class="kw-group-name" ${isEditing ? `ondblclick="SettingsPage.renameKeywordGroup('${esc(cat)}', this)"` : ''}>${esc(cat)}</span>
                </span>
                <span style="display:flex;align-items:center;gap:8px;">
                  <span style="font-size:11px; font-weight:normal; color:var(--text-muted);">(${keywords.length} từ khóa)</span>
                  ${isEditing ? `<button class="btn btn-ghost btn-sm" style="font-size:11px;color:var(--accent-red);padding:2px 6px;" onclick="SettingsPage.deleteKeywordGroup('${esc(cat)}')">🗑️</button>` : ''}
                </span>
              </label>
              <div style="position:relative;">
                <input type="text" class="form-input keyword-input" data-cat="${esc(cat)}" 
                       ${!isEditing ? 'disabled' : ''}
                       value="${esc(keywords.join(', '))}" placeholder="Nhập từ khóa gợi ý..." 
                       ${isEditing ? `oninput="SettingsPage._kwAutocomplete(event, '${esc(cat)}')"` : ''}
                       onkeydown="${isEditing ? `SettingsPage._kwDropdownNav(event, '${esc(cat)}')` : ''}"
                       style="font-size:12px; padding:6px 10px; ${isEditing ? 'border-color:var(--accent-blue);' : 'background:rgba(255,255,255,0.01);opacity:0.8;'}" />
                <div class="kw-autocomplete-dropdown" id="kw-ac-${esc(cat)}"></div>
              </div>
            </div>
          `;
          }).join('')}
        </div>
      </div>
    `;
  }

  function toggleEditKeywords() {
    _editStates.keywords = true;
    _backups.keywords = JSON.parse(JSON.stringify(_rawKeywords));
    renderSubTabContent();
  }

  function filterKeywordGroups(query) {
    const q = String(query || '').trim().toLowerCase();
    document.querySelectorAll('#kw-groups-container .kw-group').forEach(group => {
      const name = (group.dataset.group || '').toLowerCase();
      const input = group.querySelector('.keyword-input');
      const keywords = (input?.value || '').toLowerCase();
      group.style.display = !q || name.includes(q) || keywords.includes(q) ? '' : 'none';
    });
  }

  function focusFirstKeywordInput() {
    const input = document.querySelector('#kw-groups-container .keyword-input:not([disabled])');
    if (!input) {
      Toast.info('Bấm Chỉnh sửa trước khi thêm từ khóa');
      return;
    }
    input.focus();
    input.scrollIntoView({ behavior: 'smooth', block: 'center' });
    const suffix = input.value.trim() ? ', ' : '';
    input.value = input.value + suffix;
    input.setSelectionRange(input.value.length, input.value.length);
  }

  function cancelEditKeywords() {
    _rawKeywords = JSON.parse(JSON.stringify(_backups.keywords));
    _editStates.keywords = false;
    renderSubTabContent();
  }

  async function saveKeywords() {
    const inputs = document.querySelectorAll('.keyword-input');
    const data = { ..._rawKeywords };
    inputs.forEach(input => {
      const cat = input.dataset.cat;
      const val = input.value.split(',').map(v => v.trim()).filter(Boolean);
      data[cat] = val;
    });

    // Check for duplicates before saving
    const allDupes = _checkAllDuplicates(data);
    if (allDupes.size > 0) {
      const msg = `Có ${allDupes.size} từ khóa lặp giữa các nhóm. Hệ thống vẫn lưu dữ liệu.`;
      if (Toast.warning) Toast.warning(msg);
      else Toast.info(msg);
    }

    try {
      await API.put('/pipeline/keywords', data);
      _rawKeywords = data;
      _editStates.keywords = false;
      Toast.success('Đã lưu từ khóa gợi ý thành công');
      renderSubTabContent();
    } catch (e) {
      Toast.error('Lỗi lưu từ khóa: ' + e.message);
    }
  }

  // === Keyword Group Management (tasks 2-3) ===

  function addKeywordGroup() {
    const name = prompt('Nhập tên nhóm keyword mới:');
    if (!name || !name.trim()) return;
    const trimmed = name.trim();
    if (_rawKeywords[trimmed]) {
      Toast.error(`Nhóm "${trimmed}" đã tồn tại`);
      return;
    }
    _rawKeywords[trimmed] = [];
    renderSubTabContent();
    // Scroll to new group
    setTimeout(() => {
      const container = document.getElementById('kw-groups-container');
      if (container) container.scrollTop = container.scrollHeight;
    }, 100);
    Toast.success(`Đã tạo nhóm "${trimmed}"`);
  }

  function deleteKeywordGroup(cat) {
    const keywords = _rawKeywords[cat] || [];
    if (keywords.length > 0) {
      if (!confirm(`Nhóm "${cat}" có ${keywords.length} từ khóa. Bạn có chắc muốn xóa?`)) return;
    }
    delete _rawKeywords[cat];
    renderSubTabContent();
    Toast.info(`Đã xóa nhóm "${cat}"`);
  }

  function renameKeywordGroup(oldName, labelEl) {
    if (!_editStates.keywords) return;
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'kw-rename-input';
    input.value = oldName;
    labelEl.replaceWith(input);
    input.focus();
    input.select();

    const doRename = () => {
      const newName = input.value.trim();
      if (!newName || newName === oldName) {
        const span = document.createElement('span');
        span.className = 'kw-group-name';
        span.textContent = oldName;
        span.ondblclick = () => renameKeywordGroup(oldName, span);
        input.replaceWith(span);
        return;
      }
      if (_rawKeywords[newName]) {
        Toast.error(`Nhóm "${newName}" đã tồn tại`);
        input.focus();
        return;
      }
      // Copy data to new key, delete old
      _rawKeywords[newName] = _rawKeywords[oldName];
      delete _rawKeywords[oldName];
      renderSubTabContent();
      Toast.success(`Đã đổi tên "${oldName}" → "${newName}"`);
    };

    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); doRename(); }
      if (e.key === 'Escape') {
        const span = document.createElement('span');
        span.className = 'kw-group-name';
        span.textContent = oldName;
        span.ondblclick = () => renameKeywordGroup(oldName, span);
        input.replaceWith(span);
      }
    });
    input.addEventListener('blur', doRename);
  }

  // === Drag & Drop Reorder (tasks 4.1-4.2) ===

  let _kwDragSource = null;

  function _kwDragStart(e) {
    _kwDragSource = e.currentTarget;
    e.currentTarget.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', e.currentTarget.dataset.group);
  }

  function _kwDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    const target = e.currentTarget;
    if (target !== _kwDragSource) {
      target.classList.add('drag-over');
    }
  }

  function _kwDrop(e) {
    e.preventDefault();
    const target = e.currentTarget;
    target.classList.remove('drag-over');
    if (!_kwDragSource || target === _kwDragSource) return;

    const srcGroup = _kwDragSource.dataset.group;
    const tgtGroup = target.dataset.group;

    // Reorder keys in _rawKeywords
    const keys = Object.keys(_rawKeywords);
    const srcIdx = keys.indexOf(srcGroup);
    const tgtIdx = keys.indexOf(tgtGroup);
    if (srcIdx === -1 || tgtIdx === -1) return;

    keys.splice(srcIdx, 1);
    keys.splice(tgtIdx, 0, srcGroup);

    const reordered = {};
    keys.forEach(k => { reordered[k] = _rawKeywords[k]; });
    _rawKeywords = reordered;
    renderSubTabContent();
  }

  function _kwDragEnd(e) {
    e.currentTarget.classList.remove('dragging');
    document.querySelectorAll('.kw-group.drag-over').forEach(el => el.classList.remove('drag-over'));
    _kwDragSource = null;
  }

  // === Autocomplete (tasks 5.1-5.4) ===

  let _kwAcTimer = null;
  let _kwAcHighlight = -1;

  function _kwAutocomplete(e, cat) {
    const input = e.target;
    const val = input.value;
    // Get last keyword being typed (after last comma)
    const parts = val.split(',');
    const query = (parts[parts.length - 1] || '').trim();

    if (query.length < 2) {
      _kwHideDropdown(cat);
      return;
    }

    // Debounce 300ms
    if (_kwAcTimer) clearTimeout(_kwAcTimer);
    _kwAcTimer = setTimeout(async () => {
      try {
        const res = await API.get(`/pipeline/keywords/search?q=${encodeURIComponent(query)}`);
        const results = res.results || [];
        _kwShowDropdown(cat, results, input);
      } catch (e) {
        _kwHideDropdown(cat);
      }
    }, 300);
  }

  function _kwShowDropdown(cat, results, input) {
    const dd = document.getElementById(`kw-ac-${cat}`);
    if (!dd || results.length === 0) { _kwHideDropdown(cat); return; }

    _kwAcHighlight = -1;
    dd.innerHTML = results.map((r, i) => `
      <div class="kw-autocomplete-item" data-idx="${i}" data-keyword="${escAttr(String(r.keyword))}">
        <span>${esc(r.keyword)}</span>
        <span class="kw-ac-group">${esc(r.group)}</span>
      </div>
    `).join('');
    dd.querySelectorAll('.kw-autocomplete-item').forEach(item => {
      item.addEventListener('click', () => _kwSelectAc(cat, item.dataset.keyword || ''));
    });
    dd.classList.add('visible');
    dd._results = results;
  }

  function _kwHideDropdown(cat) {
    const dd = document.getElementById(`kw-ac-${cat}`);
    if (dd) { dd.classList.remove('visible'); dd.innerHTML = ''; }
    _kwAcHighlight = -1;
  }

  function _kwSelectAc(cat, keyword) {
    const input = document.querySelector(`.keyword-input[data-cat="${cat}"]`);
    if (!input) return;

    // Check for duplicate in same group
    const existing = input.value.split(',').map(v => v.trim().toLowerCase()).filter(Boolean);
    if (existing.includes(keyword.toLowerCase())) {
      Toast.error(`"${keyword}" từ khóa đã có trong nhóm này`);
      _kwHideDropdown(cat);
      return;
    }

    // Append keyword after last comma
    const parts = input.value.split(',').map(v => v.trim()).filter(Boolean);
    parts.push(keyword);
    input.value = parts.join(', ');
    _kwHideDropdown(cat);
    input.focus();

    Toast.success(`Đã thêm từ khóa "${keyword}"`);
  }

  function _kwDropdownNav(e, cat) {
    const dd = document.getElementById(`kw-ac-${cat}`);
    if (!dd || !dd.classList.contains('visible') || !dd._results) return;

    const items = dd.querySelectorAll('.kw-autocomplete-item');
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      _kwAcHighlight = Math.min(_kwAcHighlight + 1, items.length - 1);
      items.forEach((it, i) => it.classList.toggle('highlighted', i === _kwAcHighlight));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      _kwAcHighlight = Math.max(_kwAcHighlight - 1, 0);
      items.forEach((it, i) => it.classList.toggle('highlighted', i === _kwAcHighlight));
    } else if (e.key === 'Enter' && _kwAcHighlight >= 0) {
      e.preventDefault();
      const selected = dd._results[_kwAcHighlight];
      if (selected) _kwSelectAc(cat, selected.keyword);
    } else if (e.key === 'Escape') {
      _kwHideDropdown(cat);
    }
  }

  // === Duplicate Detection (tasks 6.1-6.3) ===

  function _checkDuplicatesForGroup(cat) {
    if (!_rawKeywords) return [];
    const myKeywords = (_rawKeywords[cat] || []).map(k => k.toLowerCase());
    const dupes = [];
    for (const [otherCat, otherKws] of Object.entries(_rawKeywords)) {
      if (otherCat === cat || otherCat === 'manual_brand_alias') continue;
      for (const kw of otherKws) {
        if (myKeywords.includes(kw.toLowerCase())) {
          dupes.push({ keyword: kw, group: otherCat });
        }
      }
    }
    return dupes;
  }

  function _checkAllDuplicates(data) {
    const seen = new Map(); // keyword -> [groups]
    const dupes = new Map();
    for (const [cat, keywords] of Object.entries(data)) {
      if (cat === 'manual_brand_alias') continue;
      for (const kw of keywords) {
        const lower = kw.toLowerCase();
        if (!seen.has(lower)) {
          seen.set(lower, [cat]);
        } else {
          seen.get(lower).push(cat);
          dupes.set(kw, seen.get(lower));
        }
      }
    }
    return dupes;
  }

  function _updateDuplicateWarnings() {
    // Re-render to show updated duplicate badges
    renderSubTabContent();
  }

  /* ---- Products Excel Sub-tab ---- */
  async function loadProductsData() {
    if (_productsData) return;
    try {
      const res = await API.get('/pipeline/products/list');
      // res.sheets maps sheet names to {columns, products}
      _productsData = res.sheets || {};
      _productsColumns = {};
      _productsSheetNames = res.sheet_names || ['Lọc lần 1', 'Lọc lần 2', 'Lọc lần 3'];

      for (const name of _productsSheetNames) {
        _productsColumns[name] = _productsData[name]?.columns || ['Sản phẩm', 'Dòng SP', 'Model'];
        _productsData[name] = _productsData[name]?.products || [];
      }

      _activeProductSheet = _productsSheetNames[0] || 'Lọc lần 1';
      renderSubTabContent();
    } catch (e) {
      Toast.error('Không thể tải danh mục sản phẩm: ' + e.message);
    }
  }

  function captureActiveSheetEdits() {
    const table = document.getElementById('products-edit-table');
    if (!table) return;
    const rows = table.querySelectorAll('tbody tr');
    const products = [];
    const cols = _productsColumns[_activeProductSheet] || ['Sản phẩm', 'Dòng SP', 'Model'];
    
    rows.forEach(row => {
      const cells = row.querySelectorAll('.prod-cell');
      if (cells.length === 0) return;
      
      const item = {};
      cells.forEach(cell => {
        const field = cell.dataset.field;
        item[field] = cell.textContent.trim();
      });
      
      // Keep only if at least one field is filled
      if (Object.values(item).some(v => v !== '')) {
        products.push(item);
      }
    });
    
    _productsData[_activeProductSheet] = products;
  }

  function switchProductSheet(sheetName) {
    if (_editStates.products) {
      captureActiveSheetEdits();
    }
    _activeProductSheet = sheetName;
    renderSubTabContent();
  }

  function renderProductsSubTab() {
    if (!_productsData) {
      loadProductsData();
      return `<div class="text-center" style="padding:40px;"><span class="spinner"></span> Đang tải danh mục sản phẩm...</div>`;
    }

    const isEditing = _editStates.products;
    const cols = _productsColumns[_activeProductSheet] || ['Sản phẩm', 'Dòng SP', 'Model'];
    const rows = _productsData[_activeProductSheet] || [];

    return `
      <div class="animate-in">
        ${renderEditBanner('products')}

        <!-- Sheet selector tabs -->
        <div style="display:flex;gap:8px;margin-bottom:16px;background:rgba(255,255,255,0.02);padding:6px;border-radius:6px;border:1px solid var(--border);width:fit-content;">
          ${_productsSheetNames.map(name => {
            const active = name === _activeProductSheet;
            return `
              <button class="btn btn-sm" 
                      style="padding:6px 16px; font-size:12px; border-radius:4px; font-weight:600; border:none; transition:all 0.2s;
                             background:${active ? 'var(--accent-blue)' : 'transparent'};
                             color:${active ? '#ffffff' : 'var(--text-muted)'};"
                      onclick="SettingsPage.switchProductSheet('${esc(name)}')">
                📄 ${esc(name)}
              </button>
            `;
          }).join('')}
        </div>

        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
          <p class="form-hint" style="color:var(--text-muted); margin:0; font-size:12px;">
            ${isEditing ? 'Nhấp đúp vào ô để chỉnh sửa trực tiếp thông tin sản phẩm.' : 'Danh mục sản phẩm (Chế độ xem). Bấm Chỉnh sửa để sửa đổi.'}
          </p>
          
          <div class="btn-group" style="display:flex; gap:8px;">
            ${isEditing 
              ? `
                <button class="btn btn-secondary btn-sm" style="padding:4px 10px; font-size:12px;" onclick="SettingsPage.addProductRow()">
                  ➕ Thêm dòng mới
                </button>
                <button class="btn btn-secondary btn-sm" style="padding:4px 10px; font-size:12px;" onclick="SettingsPage.cancelEditProducts()">
                  🔙 Quay lại
                </button>
                <button class="btn btn-primary btn-sm" style="padding:4px 10px; font-size:12px;" onclick="SettingsPage.saveProducts()">
                  💾 Lưu thay đổi
                </button>
                <button class="btn btn-sm" id="btn-sync-products" style="padding:4px 10px; font-size:12px; background:rgba(59,130,246,0.15);color:var(--accent-blue);border:1px solid rgba(59,130,246,0.3);" 
                        onclick="SettingsPage.syncProductsToSP()">☁️ Đồng bộ SharePoint</button>
              `
              : `
                <button class="btn btn-secondary btn-sm" style="padding:4px 10px; font-size:12px;" onclick="SettingsPage.toggleEditProducts()">✏️ Chỉnh sửa</button>
                <button class="btn btn-sm" id="btn-sync-products" style="padding:4px 10px; font-size:12px; background:rgba(59,130,246,0.15);color:var(--accent-blue);border:1px solid rgba(59,130,246,0.3);" 
                        onclick="SettingsPage.syncProductsToSP()">☁️ Đồng bộ SharePoint</button>
              `
            }
          </div>
        </div>

        <div style="max-height: 380px; overflow-y: auto; border: 1px solid var(--border); border-radius: var(--radius-md);">
          <table class="table" style="width:100%; border-collapse:collapse; text-align:left; font-size:12px;" id="products-edit-table">
            <thead>
              <tr style="background:rgba(255,255,255,0.02); border-bottom:1px solid var(--border);">
                ${cols.map(c => `<th style="padding:10px 12px; font-weight:600;">${esc(c)}</th>`).join('')}
                ${isEditing ? `<th style="padding:10px 12px; width:80px; text-align:center; font-weight:600;">Thao tác</th>` : ''}
              </tr>
            </thead>
            <tbody>
              ${rows.length === 0 
                ? `<tr><td colspan="${cols.length + (isEditing ? 1 : 0)}" style="padding:24px; text-align:center; color:var(--text-muted);">Không có sản phẩm nào trong sheet này.</td></tr>`
                : rows.map((p, idx) => `
                  <tr style="border-bottom:1px solid var(--border);" data-idx="${idx}">
                    ${cols.map(c => `
                      <td style="padding:8px 12px; ${isEditing ? 'border-bottom: 1px dashed rgba(59,130,246,0.15);' : ''}" 
                          contenteditable="${isEditing ? 'true' : 'false'}" 
                          class="prod-cell" data-field="${esc(c)}">${esc(p[c] || '')}</td>
                    `).join('')}
                    ${isEditing ? `
                      <td style="padding:8px 12px; text-align:center;">
                        <button class="btn btn-ghost btn-sm" style="color:var(--accent-red); padding:2px 6px; font-size:11px;" 
                                onclick="SettingsPage.deleteProductRow(${idx})">🗑️ Xóa</button>
                      </td>
                    ` : ''}
                  </tr>
                `).join('')}
            </tbody>
          </table>
        </div>
      </div>
    `;
  }

  function toggleEditProducts() {
    _editStates.products = true;
    // Deep clone products data for backup
    _backups.products = JSON.parse(JSON.stringify(_productsData));
    renderSubTabContent();
  }

  function cancelEditProducts() {
    _productsData = JSON.parse(JSON.stringify(_backups.products));
    _editStates.products = false;
    renderSubTabContent();
  }

  function addProductRow() {
    captureActiveSheetEdits();
    const cols = _productsColumns[_activeProductSheet] || ['Sản phẩm', 'Dòng SP', 'Model'];
    const newRow = {};
    cols.forEach(c => {
      newRow[c] = 'Mới';
    });
    _productsData[_activeProductSheet].unshift(newRow);
    renderSubTabContent();
  }

  function deleteProductRow(idx) {
    captureActiveSheetEdits();
    _productsData[_activeProductSheet].splice(idx, 1);
    renderSubTabContent();
  }

  async function saveProducts() {
    captureActiveSheetEdits();
    
    const payload = {
      sheet_name: _activeProductSheet,
      products: _productsData[_activeProductSheet]
    };

    try {
      await API.put('/pipeline/products', payload);
      _editStates.products = false;
      Toast.success(`Đã lưu danh mục sản phẩm của sheet '${_activeProductSheet}' thành công`);
      renderSubTabContent();
    } catch (e) {
      Toast.error('Lỗi lưu danh mục Excel: ' + e.message);
    }
  }

  /* ---- Pipeline Tab ---- */
  function renderPipelineTab() {
    const s = _settings || {};
    return `
      <div class="card animate-in" style="max-width:700px;">
        <div class="card-header">
          <span class="card-title"><span class="icon">🔧</span> Cấu hình Pipeline</span>
        </div>

        <div class="form-row">
          <div class="form-group">
            <label class="form-label">LLM Batch Size</label>
            <input type="number" class="form-input" id="set-batch-size" value="${s.llm_batch_size || s.batch_size || 5}" min="1" max="50">
            <span class="form-hint">Số request LLM gửi đồng thời</span>
          </div>
          <div class="form-group">
            <label class="form-label">Checkpoint mỗi</label>
            <input type="number" class="form-input" id="set-checkpoint" value="${s.checkpoint_every || 50}" min="5" max="500">
            <span class="form-hint">Lưu checkpoint mỗi N dòng</span>
          </div>
        </div>

        <div class="form-row">
          <div class="form-group">
            <label class="form-label">Base Wait (giây)</label>
            <input type="number" class="form-input" id="set-base-wait" value="${s.base_wait || 2}" min="0" max="60" step="0.5">
          </div>
          <div class="form-group">
            <label class="form-label">Max Retry</label>
            <input type="number" class="form-input" id="set-max-retry" value="${s.max_retry || 3}" min="0" max="10">
          </div>
        </div>

        <div class="form-row">
          <div class="form-group">
            <label class="form-label">Rate Gap (giây)</label>
            <input type="number" class="form-input" id="set-rate-gap" value="${s.rate_limit_gap || s.rate_gap || 1}" min="0" max="30" step="0.1">
          </div>
          <div class="form-group">
            <label class="form-label">BM25 Min Score</label>
            <input type="number" class="form-input" id="set-bm25-score" value="${s.bm25_min_score || 0.3}" min="0" max="1" step="0.05">
          </div>
        </div>

        <div class="form-group">
          <label class="form-label">HTTP Timeout (giây)</label>
          <input type="number" class="form-input" id="set-http-timeout" value="${s.http_timeout || s.http_timeout_seconds || 30}" min="5" max="300" style="max-width:200px;">
        </div>

        <button class="btn btn-primary mt-4" onclick="SettingsPage.savePipelineSettings()">
          💾 Lưu cài đặt Pipeline
        </button>
      </div>
    `;
  }

  async function savePipelineSettings() {
    const data = {
      llm_batch_size: parseInt(document.getElementById('set-batch-size')?.value) || 5,
      checkpoint_every: parseInt(document.getElementById('set-checkpoint')?.value) || 50,
      base_wait: parseFloat(document.getElementById('set-base-wait')?.value) || 2,
      max_retry: parseInt(document.getElementById('set-max-retry')?.value) || 3,
      rate_limit_gap: parseFloat(document.getElementById('set-rate-gap')?.value) || 1,
      bm25_min_score: parseFloat(document.getElementById('set-bm25-score')?.value) || 0.3,
      http_timeout: parseInt(document.getElementById('set-http-timeout')?.value) || 30,
    };

    try {
      await API.putSettings(data);
      Object.assign(_settings, data);
      Toast.success('Đã lưu cài đặt Pipeline');
      loadSettings();
    } catch (e) {
      Toast.error('Lỗi lưu: ' + e.message);
    }
  }

  /* ---- SharePoint Tab (Read-only) ---- */
  function renderSharePointTab() {
    const s = _settings || {};
    const sp = s.sharepoint || s;
    return `
      <div class="card animate-in" style="max-width:700px;">
        <div class="card-header" style="border-bottom:1px solid var(--border); padding-bottom:12px; margin-bottom:16px;">
          <span class="card-title"><span class="icon">🔑</span> Kết nối SharePoint (Chỉ xem)</span>
        </div>

        <div style="display:grid; grid-template-columns: 1fr 1fr; gap:16px;">
          <div style="grid-column: span 2; background:rgba(255,255,255,0.02); padding:12px; border-radius:6px; border:1px solid var(--border);">
            <div style="font-size:10px; color:var(--text-muted); margin-bottom:4px; font-weight:600; text-transform:uppercase; letter-spacing:0.5px;">SITE URL</div>
            <div style="font-size:13px; font-weight:500; word-break:break-all;">${esc(s.sharepoint_site_url || s.site_url || '(Chưa cấu hình)')}</div>
          </div>
          
          <div style="background:rgba(255,255,255,0.02); padding:12px; border-radius:6px; border:1px solid var(--border);">
            <div style="font-size:10px; color:var(--text-muted); margin-bottom:4px; font-weight:600; text-transform:uppercase; letter-spacing:0.5px;">CLIENT ID</div>
            <div style="font-size:13px; font-weight:500; font-family:monospace;">${esc(maskSecret(s.azure_client_id || s.client_id || ''))}</div>
          </div>

          <div style="background:rgba(255,255,255,0.02); padding:12px; border-radius:6px; border:1px solid var(--border);">
            <div style="font-size:10px; color:var(--text-muted); margin-bottom:4px; font-weight:600; text-transform:uppercase; letter-spacing:0.5px;">TENANT ID</div>
            <div style="font-size:13px; font-weight:500; font-family:monospace;">${esc(maskSecret(s.azure_tenant_id || s.tenant_id || ''))}</div>
          </div>

          <div style="grid-column: span 2; background:rgba(255,255,255,0.02); padding:12px; border-radius:6px; border:1px solid var(--border);">
            <div style="font-size:10px; color:var(--text-muted); margin-bottom:4px; font-weight:600; text-transform:uppercase; letter-spacing:0.5px;">DRIVE ID</div>
            <div style="font-size:13px; font-weight:500; font-family:monospace; word-break:break-all;">${esc(s.sharepoint_drive_id || s.drive_id || '')}</div>
          </div>

          <div style="background:rgba(255,255,255,0.02); padding:12px; border-radius:6px; border:1px solid var(--border);">
            <div style="font-size:10px; color:var(--text-muted); margin-bottom:4px; font-weight:600; text-transform:uppercase; letter-spacing:0.5px;">THƯ MỤC ĐẦU VÀO</div>
            <div style="font-size:13px; font-weight:500; color:var(--accent-blue);">Input/</div>
          </div>

          <div style="background:rgba(255,255,255,0.02); padding:12px; border-radius:6px; border:1px solid var(--border);">
            <div style="font-size:10px; color:var(--text-muted); margin-bottom:4px; font-weight:600; text-transform:uppercase; letter-spacing:0.5px;">THƯ MỤC ĐẦU RA</div>
            <div style="font-size:13px; font-weight:500; color:var(--accent-green);">Output/</div>
          </div>
        </div>
      </div>
    `;
  }

  /* ---- Notification Tab ---- */
  function renderNotifyTab() {
    const s = _settings || {};
    const emails = s.notification_recipients_raw || s.notification_email || '';
    const successChecked = s.notify_on_success !== false;
    const errorChecked = s.notify_on_error !== false;

    return `
      <div class="card animate-in" style="max-width:700px;">
        <div class="card-header">
          <span class="card-title"><span class="icon">🔔</span> Cấu hình thông báo</span>
        </div>

        <div class="form-group">
          <label class="form-label" style="font-size:13px;">Email nhận thông báo (ngăn cách bằng dấu phẩy)</label>
          <input type="text" class="form-input" id="set-email" value="${esc(emails)}" 
                 oninput="SettingsPage.updateEmailCount()" placeholder="admin@company.com, user1@company.com" style="margin-top:6px;">
          <div style="margin-top:8px;" id="email-count-container">
            <span class="badge" id="email-count-badge" style="padding:4px 8px; font-size:11px; border-radius:4px; font-weight:600;">
              Đang tải danh sách email...
            </span>
          </div>
        </div>

        <div class="form-group mt-4" style="margin-top:20px;">
          <div class="checkbox-item" style="display:flex; align-items:center; gap:8px; margin-bottom:12px;">
            <input type="checkbox" id="set-notify-success" ${successChecked ? 'checked' : ''} style="cursor:pointer; width:16px; height:16px;">
            <span style="font-size:13px;">Thông báo khi xử lý thành công</span>
          </div>
        </div>

        <div class="form-group">
          <div class="checkbox-item" style="display:flex; align-items:center; gap:8px;">
            <input type="checkbox" id="set-notify-error" ${errorChecked ? 'checked' : ''} style="cursor:pointer; width:16px; height:16px;">
            <span style="font-size:13px;">Thông báo khi có lỗi xảy ra</span>
          </div>
        </div>

        <button class="btn btn-primary mt-4" onclick="SettingsPage.saveNotificationSettings()" style="margin-top:24px;">
          💾 Lưu cấu hình thông báo
        </button>
      </div>
    `;
  }

  function updateEmailCount() {
    const input = document.getElementById('set-email');
    const badge = document.getElementById('email-count-badge');
    if (input && badge) {
      const raw = input.value.trim();
      const emails = raw.split(',').map(e => e.trim()).filter(Boolean);
      
      if (emails.length === 0) {
        badge.textContent = '❌ Chưa cấu hình email nhận thông báo';
        badge.style.background = 'rgba(239, 68, 68, 0.1)';
        badge.style.color = 'var(--accent-red)';
      } else {
        badge.textContent = `📧 Tổng số tài khoản nhận thông báo: ${emails.length}`;
        badge.style.background = 'rgba(34, 197, 94, 0.1)';
        badge.style.color = 'var(--accent-green)';
      }
    }
  }

  async function saveNotificationSettings() {
    const email = document.getElementById('set-email')?.value || '';
    const notify_on_success = document.getElementById('set-notify-success')?.checked;
    const notify_on_error = document.getElementById('set-notify-error')?.checked;

    const data = {
      email,
      notify_on_success,
      notify_on_error
    };

    try {
      await API.putSettings(data);
      Object.assign(_settings, data);
      Toast.success('Đã lưu cấu hình thông báo thành công');
      loadSettings();
    } catch (e) {
      Toast.error('Lỗi lưu cấu hình: ' + e.message);
    }
  }

  /* ---- Helper functions ---- */
  function maskSecret(s) {
    if (!s || s.length < 8) return s;
    return s.substring(0, 4) + '••••' + s.substring(s.length - 4);
  }

  function esc(s) {
    if (s == null) return '';
    const d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
  }

  function escAttr(s) {
    return esc(s).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function destroy() {}

  /* ---- Sync to SharePoint ---- */
  async function syncKeywordsToSP() {
    const btn = document.getElementById('btn-sync-keywords');
    if (!btn) return;
    const origText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner" style="width:14px;height:14px;"></span> Đang đồng bộ...';
    try {
      const res = await API.syncKeywordsToSP();
      Toast.success(res.message || 'Đã đồng bộ từ khóa lên SharePoint');
    } catch (e) {
      Toast.error('Lỗi đồng bộ từ khóa lên SharePoint: ' + e.message);
    } finally {
      btn.disabled = false;
      btn.innerHTML = origText;
    }
  }

  async function syncProductsToSP() {
    const btn = document.getElementById('btn-sync-products');
    if (!btn) return;
    const origText = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner" style="width:14px;height:14px;"></span> Đang đồng bộ...';
    try {
      const res = await API.syncProductsToSP();
      Toast.success(res.message || 'Đã đồng bộ sản phẩm lên SharePoint');
    } catch (e) {
      Toast.error('Lỗi đồng bộ sản phẩm lên SharePoint: ' + e.message);
    } finally {
      btn.disabled = false;
      btn.innerHTML = origText;
    }
  }

  // === Label Timeline (tasks 4.1-4.5) ===

  function renderLabelsTab() {
    return `
      <div class="animate-in">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
          <p class="form-hint" style="color:var(--text-muted); font-size:12px; margin:0;">
            Lịch sử thay đổi nhãn phân loại. Nhấn vào mục để xem chi tiết.
          </p>
        </div>

        <div class="timeline-filter">
          <label style="font-size:12px;color:var(--text-muted);">Từ ngày:</label>
          <input type="date" id="label-date-from">
          <label style="font-size:12px;color:var(--text-muted);">Đến ngày:</label>
          <input type="date" id="label-date-to">
          <button class="btn btn-secondary btn-sm" onclick="SettingsPage.filterLabelHistory()">🔍 Lọc</button>
        </div>

        <div id="label-timeline" class="timeline-container"></div>

        <div id="label-load-more" class="hidden" style="text-align:center;margin-top:12px;">
          <button class="btn btn-secondary btn-sm" onclick="SettingsPage.loadMoreLabelHistory()">📋 Xem thêm</button>
        </div>
      </div>
    `;
  }

  async function loadLabelHistory(append = false) {
    if (!append) {
      _labelHistoryOffset = 0;
      _labelHistory = [];
    }

    const dateFrom = document.getElementById('label-date-from')?.value || '';
    const dateTo = document.getElementById('label-date-to')?.value || '';

    let url = `/pipeline/labels/history?limit=20&offset=${_labelHistoryOffset}`;
    if (dateFrom) url += `&date_from=${dateFrom}`;
    if (dateTo) url += `&date_to=${dateTo}`;

    try {
      const res = await API.get(url);
      const items = res.items || [];
      _labelHistoryHasMore = res.has_more || false;

      if (append) {
        _labelHistory.push(...items);
      } else {
        _labelHistory = items;
      }

      _labelHistoryOffset += items.length;
      _renderTimeline();
    } catch (e) {
      Toast.error('Lỗi tải lịch sử nhãn: ' + e.message);
    }
  }

  function _renderTimeline() {
    const container = document.getElementById('label-timeline');
    if (!container) return;

    if (_labelHistory.length === 0) {
      container.innerHTML = `
        <div class="timeline-empty">
          <div class="empty-icon">📋</div>
          <p>Chưa có lịch sử thay đổi nhãn</p>
        </div>
      `;
    } else {
      container.innerHTML = _labelHistory.map((entry, i) => _renderTimelineEntry(entry, i)).join('');
    }

    // Show/hide load more
    const loadMoreBtn = document.getElementById('label-load-more');
    if (loadMoreBtn) {
      loadMoreBtn.classList.toggle('hidden', !_labelHistoryHasMore);
    }
  }

  function _renderTimelineEntry(entry, index) {
    const actionIcons = { add: '➕', edit: '✏️', delete: '❌' };
    const actionLabels = { add: 'Thêm', edit: 'Sửa', delete: 'Xóa' };
    const icon = actionIcons[entry.action] || '📝';
    const actionLabel = actionLabels[entry.action] || entry.action;

    // Format timestamp to VN format
    let timeStr = entry.timestamp || '';
    try {
      const d = new Date(timeStr);
      timeStr = `${d.getDate().toString().padStart(2, '0')}/${(d.getMonth() + 1).toString().padStart(2, '0')}/${d.getFullYear()} ${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`;
    } catch { /* keep original */ }

    const oldVal = entry.old_value != null ? (typeof entry.old_value === 'object' ? JSON.stringify(entry.old_value, null, 2) : String(entry.old_value)) : null;
    const newVal = entry.new_value != null ? (typeof entry.new_value === 'object' ? JSON.stringify(entry.new_value, null, 2) : String(entry.new_value)) : null;

    return `
      <div class="timeline-entry action-${esc(entry.action)}" onclick="this.classList.toggle('expanded')">
        <div class="timeline-header">
          <span class="timeline-title">
            ${icon} <strong>${esc(entry.label_name)}</strong>
            <span class="chip" style="font-size:10px;">${actionLabel}</span>
            <span class="text-muted" style="font-size:11px;">(${esc(entry.field)})</span>
          </span>
          <span class="timeline-meta">
            <span>👤 ${esc(entry.user || 'Admin')}</span>
            <span>🕐 ${timeStr}</span>
          </span>
        </div>
        ${oldVal != null || newVal != null ? `
          <div class="timeline-details">
            <div class="timeline-diff">
              ${oldVal != null ? `<div class="diff-old"><div style="font-size:10px;color:var(--accent-red);margin-bottom:4px;">Giá trị cũ:</div>${esc(oldVal)}</div>` : '<div></div>'}
              ${newVal != null ? `<div class="diff-new"><div style="font-size:10px;color:var(--accent-green);margin-bottom:4px;">Giá trị mới:</div>${esc(newVal)}</div>` : '<div></div>'}
            </div>
          </div>
        ` : ''}
      </div>
    `;
  }

  function filterLabelHistory() {
    loadLabelHistory(false);
  }

  function loadMoreLabelHistory() {
    loadLabelHistory(true);
  }

  /* ---- Users Tab (Admin) ---- */
  let _users = [];

  function renderUsersTab() {
    return `
      <div class="card animate-in" style="max-width:900px;">
        <div class="card-header">
          <span class="card-title"><span class="icon">👥</span> Quản lý người dùng</span>
          <button class="btn btn-primary btn-sm" onclick="SettingsPage.createUser()">➕ Thêm người dùng</button>
        </div>
        <div class="table-wrap">
          <table class="table" id="users-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Tên đăng nhập</th>
                <th>Tên hiển thị</th>
                <th>Vai trò</th>
                <th>Trạng thái</th>
                <th style="width:120px;">Hành động</th>
              </tr>
            </thead>
            <tbody id="users-tbody">
              <tr><td colspan="6"><div class="text-center" style="padding:30px;"><span class="spinner"></span></div></td></tr>
            </tbody>
          </table>
        </div>
      </div>
    `;
  }

  async function loadUsers() {
    const tbody = document.getElementById('users-tbody');
    if (!tbody) return;
    try {
      const res = await API.get('/users');
      _users = res.users || res;
      if (_users.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6"><div class="empty-state" style="padding:30px;"><div class="empty-state-icon">👤</div><p class="empty-state-text">Chưa có người dùng</p></div></td></tr>';
        return;
      }
      tbody.innerHTML = _users.map((u, i) => `
        <tr>
          <td class="text-muted">${i + 1}</td>
          <td><strong>${esc(u.username)}</strong></td>
          <td>${esc(u.display_name || '—')}</td>
          <td><span class="badge ${u.role === 'admin' ? 'badge-blue' : 'badge-muted'}">${esc(u.role)}</span></td>
          <td><span class="badge ${u.is_active !== false ? 'badge-green' : 'badge-red'}">${u.is_active !== false ? 'Hoạt động' : 'Vô hiệu'}</span></td>
          <td>
            <div style="display:flex;gap:4px;">
              <button class="btn btn-ghost btn-sm" onclick="SettingsPage.editUser('${esc(u.username)}')" title="Sửa">✏️</button>
              <button class="btn btn-ghost btn-sm" onclick="SettingsPage.deleteUser('${esc(u.username)}')" title="Xóa" style="color:var(--accent-red);">🗑️</button>
            </div>
          </td>
        </tr>
      `).join('');
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="6"><div class="text-center text-red" style="padding:30px;">Lỗi tải danh sách: ${esc(e.message)}</div></td></tr>`;
    }
  }

  function createUser() {
    App.showModal(`
      <div style="max-width:400px;">
        <h3 style="margin-bottom:16px;">➕ Thêm người dùng</h3>
        <div class="form-group" style="margin-bottom:12px;">
          <label class="form-label">Tên đăng nhập</label>
          <input type="text" class="form-input" id="new-user-username" placeholder="username" required>
        </div>
        <div class="form-group" style="margin-bottom:12px;">
          <label class="form-label">Tên hiển thị</label>
          <input type="text" class="form-input" id="new-user-display" placeholder="Tên hiển thị">
        </div>
        <div class="form-group" style="margin-bottom:12px;">
          <label class="form-label">Mật khẩu</label>
          ${PasswordControls.renderInput({
            id: 'new-user-password',
            toggleId: 'new-user-password-toggle',
            hintId: 'new-user-password-ascii-hint',
            placeholder: 'Mật khẩu',
            autocomplete: 'new-password',
            required: true,
          })}
        </div>
        <div class="form-group" style="margin-bottom:16px;">
          <label class="form-label">Vai trò</label>
          <select class="form-select" id="new-user-role">
            <option value="user">User</option>
            <option value="admin">Admin</option>
          </select>
        </div>
        <div style="display:flex;gap:8px;justify-content:flex-end;">
          <button class="btn btn-secondary btn-sm" onclick="App.closeModal()">Hủy</button>
          <button class="btn btn-primary btn-sm" id="btn-create-user">💾 Tạo</button>
        </div>
      </div>
    `);
    document.getElementById('btn-create-user')?.addEventListener('click', async () => {
      const username = document.getElementById('new-user-username')?.value?.trim();
      const display_name = document.getElementById('new-user-display')?.value?.trim();
      const password = PasswordControls.normalizeInput('new-user-password', 'new-user-password-ascii-hint');
      const role = document.getElementById('new-user-role')?.value;
      if (!username || !password) { Toast.error('Vui lòng nhập đầy đủ thông tin'); return; }
      try {
        await API.post('/users', { username, password, display_name, role, is_active: true });
        Toast.success(`Đã tạo người dùng "${username}"`);
        App.closeModal();
        loadUsers();
      } catch (e) {
        Toast.error('Lỗi tạo người dùng: ' + e.message);
      }
    });
  }

  function editUser(username) {
    const user = _users.find(u => u.username === username);
    if (!user) return;
    App.showModal(`
      <div style="max-width:400px;">
        <h3 style="margin-bottom:16px;">✏️ Sửa người dùng: ${esc(username)}</h3>
        <div class="form-group" style="margin-bottom:12px;">
          <label class="form-label">Tên hiển thị</label>
          <input type="text" class="form-input" id="edit-user-display" value="${esc(user.display_name || '')}">
        </div>
        <div class="form-group" style="margin-bottom:12px;">
          <label class="form-label">Mật khẩu mới (để trống nếu không đổi)</label>
          ${PasswordControls.renderInput({
            id: 'edit-user-password',
            toggleId: 'edit-user-password-toggle',
            hintId: 'edit-user-password-ascii-hint',
            placeholder: 'Để trống nếu không đổi',
            autocomplete: 'new-password',
          })}
        </div>
        <div class="form-group" style="margin-bottom:12px;">
          <label class="form-label">Vai trò</label>
          <select class="form-select" id="edit-user-role">
            <option value="user" ${user.role === 'user' ? 'selected' : ''}>User</option>
            <option value="admin" ${user.role === 'admin' ? 'selected' : ''}>Admin</option>
          </select>
        </div>
        <div class="form-group" style="margin-bottom:16px;">
          <label style="display:flex;align-items:center;gap:8px;cursor:pointer;">
            <input type="checkbox" id="edit-user-active" ${user.is_active !== false ? 'checked' : ''}>
            Hoạt động
          </label>
        </div>
        <div style="display:flex;gap:8px;justify-content:flex-end;">
          <button class="btn btn-secondary btn-sm" onclick="App.closeModal()">Hủy</button>
          <button class="btn btn-primary btn-sm" id="btn-save-user">💾 Lưu</button>
        </div>
      </div>
    `);
    document.getElementById('btn-save-user')?.addEventListener('click', async () => {
      const data = {
        display_name: document.getElementById('edit-user-display')?.value?.trim(),
        role: document.getElementById('edit-user-role')?.value,
        is_active: document.getElementById('edit-user-active')?.checked,
      };
      const newPass = PasswordControls.normalizeInput('edit-user-password', 'edit-user-password-ascii-hint');
      if (newPass) data.password = newPass;
      try {
        await API.put(`/users/${username}`, data);
        Toast.success(`Đã cập nhật người dùng "${username}"`);
        App.closeModal();
        loadUsers();
      } catch (e) {
        Toast.error('Lỗi cập nhật: ' + e.message);
      }
    });
  }

  async function deleteUser(username) {
    if (!confirm(`Bạn có chắc muốn xóa người dùng "${username}"?`)) return;
    try {
      await API.del(`/users/${username}`);
      Toast.success(`Đã xóa người dùng "${username}"`);
      loadUsers();
    } catch (e) {
      Toast.error('Lỗi xóa người dùng: ' + e.message);
    }
  }

  function openPromptAssetEditor() {
    if (window.App?.state?.user?.role !== 'admin') {
      Toast.error('Bạn không có quyền chỉnh sửa prompt');
      return;
    }
    _activeTab = 'prompt';
    _activeSubTab = 'prompt_text';
    _editStates.prompt = true;
    _backups.prompt = _prompt;
    window.location.hash = '#settings';
    if (window.App) App.renderPage('settings');
    setTimeout(() => {
      switchTab('prompt');
      switchSubTab('prompt_text');
      document.getElementById('prompt-textarea')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      Toast.info('Đã mở editor System Prompt. Dùng Lưu thay đổi để lưu vào file hệ thống.');
    }, 50);
  }

  async function openKeywordAssetEditor() {
    if (window.App?.state?.user?.role !== 'admin') {
      Toast.error('Bạn không có quyền chỉnh sửa từ khóa');
      return;
    }
    _activeTab = 'prompt';
    _activeSubTab = 'keywords';
    if (!_rawKeywords) {
      try {
        _rawKeywords = await API.get('/pipeline/keywords/raw');
      } catch (e) {
        Toast.error('Không thể tải từ khóa: ' + e.message);
        return;
      }
    }
    _editStates.keywords = true;
    _backups.keywords = JSON.parse(JSON.stringify(_rawKeywords));
    window.location.hash = '#settings';
    if (window.App) App.renderPage('settings');
    setTimeout(() => {
      switchTab('prompt');
      switchSubTab('keywords');
      document.getElementById('kw-groups-container')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      Toast.info('Đã mở editor từ khóa. Dùng Lưu thay đổi để lưu local, Đồng bộ SharePoint để đẩy lên Cloud.');
    }, 50);
  }

  async function openProductAssetEditor() {
    if (window.App?.state?.user?.role !== 'admin') {
      Toast.error('Bạn không có quyền chỉnh sửa sản phẩm');
      return;
    }
    _activeTab = 'prompt';
    _activeSubTab = 'products';
    if (!_productsData) {
      try {
        const res = await API.get('/pipeline/products/list');
        _productsData = res.sheets || {};
        _productsColumns = {};
        _productsSheetNames = res.sheet_names || ['Lọc lần 1', 'Lọc lần 2', 'Lọc lần 3'];
        for (const name of _productsSheetNames) {
          _productsColumns[name] = _productsData[name]?.columns || ['Sản phẩm', 'Dòng SP', 'Model'];
          _productsData[name] = _productsData[name]?.products || [];
        }
        _activeProductSheet = _productsSheetNames[0] || 'Lọc lần 1';
      } catch (e) {
        Toast.error('Không thể tải bảng sản phẩm: ' + e.message);
        return;
      }
    }
    _editStates.products = true;
    _backups.products = JSON.parse(JSON.stringify(_productsData));
    window.location.hash = '#settings';
    if (window.App) App.renderPage('settings');
    setTimeout(() => {
      switchTab('prompt');
      switchSubTab('products');
      document.getElementById('products-edit-table')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      Toast.info('Đã mở editor sản phẩm. Dùng Lưu thay đổi để lưu local, Đồng bộ SharePoint để đẩy lên Cloud.');
    }, 50);
  }

  return {
    render, destroy, switchTab, onBackendChange, testConnection, revealApiKey,
    toggleEditPrompt, cancelEditPrompt, copyPrompt, savePrompt, saveModelSettings, savePipelineSettings,
    switchSubTab, renderSubTabContent, toggleEditKeywords, cancelEditKeywords, saveKeywords,
    filterKeywordGroups, focusFirstKeywordInput,
    toggleEditProducts, cancelEditProducts, switchProductSheet, saveProducts, addProductRow, deleteProductRow,
    updateEmailCount, saveNotificationSettings,
    syncKeywordsToSP, syncProductsToSP,
    addKeywordGroup, deleteKeywordGroup, renameKeywordGroup,
    _kwDragStart, _kwDragOver, _kwDrop, _kwDragEnd,
    _kwAutocomplete, _kwDropdownNav, _kwSelectAc,
    renderLabelsTab, loadLabelHistory, filterLabelHistory, loadMoreLabelHistory,
    createUser, editUser, deleteUser, loadUsers,
    openPromptAssetEditor, openKeywordAssetEditor, openProductAssetEditor
  };
})();
