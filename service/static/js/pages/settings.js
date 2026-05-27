/* ============================================================
   Settings Page — Model, Prompt, Pipeline, SharePoint, Notifications
   ============================================================ */

window.SettingsPage = (() => {
  let _activeTab = 'model';
  let _activeSubTab = 'prompt_text';
  let _settings = null;
  let _models = [];
  let _prompt = '';
  let _rawKeywords = null;
  let _productsData = null;

  const TABS = [
    { id: 'model',    icon: '🤖', label: 'Model' },
    { id: 'prompt',   icon: '📝', label: 'Prompt / Dữ liệu' },
    { id: 'pipeline', icon: '🔧', label: 'Pipeline' },
    { id: 'sharepoint', icon: '🔑', label: 'SharePoint' },
    { id: 'notify',   icon: '🔔', label: 'Thông báo' },
  ];

  function render() {
    const app = document.getElementById('app');
    app.innerHTML = `
      <div class="page-header">
        <h2>⚙️ Cài đặt</h2>
        <p>Cấu hình hệ thống phân loại phản hồi</p>
      </div>

      <!-- Tabs -->
      <div class="tabs" id="settings-tabs">
        ${TABS.map(t => `
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
      case 'sharepoint': el.innerHTML = renderSharePointTab(); break;
      case 'notify':     
        el.innerHTML = renderNotifyTab(); 
        updateEmailCount();
        break;
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
                  const mName = typeof m === 'string' ? m : m.name || m.id;
                  return `<option value="${esc(mName)}" ${mName === model ? 'selected' : ''}>${esc(mName)}</option>`;
                }).join('')
              : `<option value="${esc(model)}">${esc(model || 'Không có model')}</option>`
            }
          </select>
        </div>

        <!-- API Key fields -->
        <div id="apikey-fields" ${!isApiKey ? 'class="hidden"' : ''}>
          <div class="form-group">
            <label class="form-label">API Key</label>
            <input type="password" class="form-input" id="set-apikey" value="${esc(s.gemini_api_key || s.api_key || '')}" placeholder="••••••••••••">
            <span class="form-hint" style="font-size:11px;color:var(--text-muted);">Key sẽ được mã hóa bảo mật khi lưu</span>
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
    const wordCount = _prompt ? _prompt.split(/\s+/).length : 0;
    const tokenEstimate = Math.round(wordCount * 1.3);

    return `
      <div class="animate-in">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
          <span style="font-size:13px;font-weight:600;color:var(--text-muted);">Mẫu Prompt của Classifier</span>
          <div class="btn-group">
            <button class="btn btn-ghost btn-sm" onclick="SettingsPage.copyPrompt()">📋 Sao chép</button>
            <button class="btn btn-secondary btn-sm" id="btn-edit-prompt" onclick="SettingsPage.toggleEditPrompt()">✏️ Chỉnh sửa</button>
          </div>
        </div>

        <textarea class="form-textarea code" id="prompt-textarea" readonly
                  style="min-height:380px;line-height:1.6;font-family:monospace;font-size:12px;width:100%;border-radius:6px;background:rgba(0,0,0,0.2);">${esc(_prompt)}</textarea>

        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:12px;">
          <div style="display:flex;gap:16px;font-size:11px;color:var(--text-muted);">
            <span>📝 ${wordCount.toLocaleString()} từ</span>
            <span>🔤 ${tokenEstimate.toLocaleString()} tokens (ước tính)</span>
            <span>📏 ${_prompt.length.toLocaleString()} ký tự</span>
          </div>
          <button class="btn btn-primary hidden" id="btn-save-prompt" onclick="SettingsPage.savePrompt()">
            💾 Lưu System Prompt
          </button>
        </div>
      </div>
    `;
  }

  function toggleEditPrompt() {
    const textarea = document.getElementById('prompt-textarea');
    const saveBtn = document.getElementById('btn-save-prompt');
    const editBtn = document.getElementById('btn-edit-prompt');
    if (!textarea) return;

    const isReadonly = textarea.readOnly;
    textarea.readOnly = !isReadonly;
    textarea.style.borderColor = isReadonly ? 'var(--accent-blue)' : '';
    if (saveBtn) saveBtn.classList.toggle('hidden', isReadonly);
    if (editBtn) editBtn.innerHTML = isReadonly ? '🔒 Khóa' : '✏️ Chỉnh sửa';
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
      Toast.success('Đã lưu prompt thành công');
      toggleEditPrompt();
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

    const categories = Object.keys(_rawKeywords).filter(k => k !== 'manual_brand_alias');
    return `
      <div class="animate-in">
        <p class="form-hint mb-4" style="color:var(--text-muted); font-size:12px; margin-bottom:16px;">
          Chỉnh sửa từ khóa gợi ý cho từng nhãn phân loại. Nhập các từ khóa cách nhau bằng dấu phẩy.
        </p>
        <div style="max-height: 400px; overflow-y: auto; padding-right: 8px;">
          ${categories.map(cat => `
            <div class="form-group mb-4" style="border-bottom:1px solid rgba(255,255,255,0.05); padding-bottom:12px; margin-bottom:16px;">
              <label class="form-label" style="font-weight:600; display:flex; justify-content:space-between; font-size:12px;">
                <span>🏷️ ${esc(cat)}</span>
                <span style="font-size:11px; font-weight:normal; color:var(--text-muted);">(${_rawKeywords[cat].length} từ khóa)</span>
              </label>
              <input type="text" class="form-input keyword-input" data-cat="${esc(cat)}" 
                     value="${esc(_rawKeywords[cat].join(', '))}" placeholder="Nhập từ khóa gợi ý..." style="font-size:12px; padding:6px 10px;">
            </div>
          `).join('')}
        </div>
        <button class="btn btn-primary mt-4" onclick="SettingsPage.saveKeywords()">
          💾 Lưu từ khóa gợi ý
        </button>
      </div>
    `;
  }

  async function saveKeywords() {
    const inputs = document.querySelectorAll('.keyword-input');
    const data = { ..._rawKeywords };
    inputs.forEach(input => {
      const cat = input.dataset.cat;
      const val = input.value.split(',').map(v => v.trim()).filter(Boolean);
      data[cat] = val;
    });

    try {
      await API.put('/pipeline/keywords', data);
      _rawKeywords = data;
      Toast.success('Đã lưu từ khóa gợi ý thành công');
    } catch (e) {
      Toast.error('Lỗi lưu từ khóa: ' + e.message);
    }
  }

  /* ---- Products Excel Sub-tab ---- */
  async function loadProductsData() {
    if (_productsData) return;
    try {
      const res = await API.get('/pipeline/products/list');
      _productsData = res.products || [];
      renderSubTabContent();
    } catch (e) {
      Toast.error('Không thể tải danh mục sản phẩm: ' + e.message);
    }
  }

  function renderProductsSubTab() {
    if (!_productsData) {
      loadProductsData();
      return `<div class="text-center" style="padding:40px;"><span class="spinner"></span> Đang tải danh mục sản phẩm...</div>`;
    }

    return `
      <div class="animate-in">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
          <p class="form-hint" style="color:var(--text-muted); margin:0; font-size:12px;">
            Kích đúp vào ô bất kỳ để chỉnh sửa trực tiếp thông tin sản phẩm.
          </p>
          <button class="btn btn-secondary btn-sm" style="padding:4px 10px; font-size:12px;" onclick="SettingsPage.addProductRow()">
            ➕ Thêm sản phẩm mới
          </button>
        </div>
        <div style="max-height: 380px; overflow-y: auto; border: 1px solid var(--border); border-radius: var(--radius-md);">
          <table class="table" style="width:100%; border-collapse:collapse; text-align:left; font-size:12px;" id="products-edit-table">
            <thead>
              <tr style="background:rgba(255,255,255,0.02); border-bottom:1px solid var(--border);">
                <th style="padding:10px 12px; font-weight:600;">Sản phẩm</th>
                <th style="padding:10px 12px; font-weight:600;">Dòng SP</th>
                <th style="padding:10px 12px; font-weight:600;">Model</th>
                <th style="padding:10px 12px; width:80px; text-align:center; font-weight:600;">Thao tác</th>
              </tr>
            </thead>
            <tbody>
              ${_productsData.length === 0 
                ? `<tr><td colspan="4" style="padding:24px; text-align:center; color:var(--text-muted);">Không có sản phẩm nào. Click "Thêm sản phẩm mới" để bắt đầu.</td></tr>`
                : _productsData.map((p, idx) => `
                  <tr style="border-bottom:1px solid var(--border);" data-idx="${idx}">
                    <td style="padding:8px 12px;" contenteditable="true" class="prod-cell" data-field="Sản phẩm">${esc(p["Sản phẩm"] || '')}</td>
                    <td style="padding:8px 12px;" contenteditable="true" class="prod-cell" data-field="Dòng SP">${esc(p["Dòng SP"] || p["dong_sp"] || '')}</td>
                    <td style="padding:8px 12px;" contenteditable="true" class="prod-cell" data-field="Model">${esc(p["Model"] || p["model"] || '')}</td>
                    <td style="padding:8px 12px; text-align:center;">
                      <button class="btn btn-ghost btn-sm" style="color:var(--accent-red); padding:2px 6px; font-size:11px;" 
                              onclick="SettingsPage.deleteProductRow(${idx})">🗑️ Xóa</button>
                    </td>
                  </tr>
                `).join('')}
            </tbody>
          </table>
        </div>
        <button class="btn btn-primary mt-4" onclick="SettingsPage.saveProducts()">
          💾 Lưu danh mục sản phẩm Excel
        </button>
      </div>
    `;
  }

  function addProductRow() {
    if (!_productsData) _productsData = [];
    _productsData.unshift({
      "Sản phẩm": "Thiết bị điện",
      "Dòng SP": "Mới",
      "Model": "Mới"
    });
    renderTab();
  }

  function deleteProductRow(idx) {
    if (!_productsData) return;
    _productsData.splice(idx, 1);
    renderTab();
  }

  async function saveProducts() {
    const rows = document.querySelectorAll('#products-edit-table tbody tr');
    const products = [];
    rows.forEach(row => {
      const idx = row.dataset.idx;
      if (idx == null) return;
      
      const cells = row.querySelectorAll('.prod-cell');
      const item = {};
      cells.forEach(cell => {
        const field = cell.dataset.field;
        item[field] = cell.textContent.trim();
      });
      
      if (item["Sản phẩm"] || item["Dòng SP"] || item["Model"]) {
        products.push(item);
      }
    });

    try {
      await API.put('/pipeline/products', products);
      _productsData = products;
      Toast.success('Đã lưu danh mục sản phẩm Excel thành công');
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

  function destroy() {}

  return {
    render, destroy, switchTab, onBackendChange, testConnection,
    toggleEditPrompt, copyPrompt, savePrompt, saveModelSettings, savePipelineSettings,
    switchSubTab, renderSubTabContent, saveKeywords, saveProducts, addProductRow, deleteProductRow,
    updateEmailCount, saveNotificationSettings
  };
})();
