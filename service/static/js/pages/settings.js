/* ============================================================
   Settings Page — Model, Prompt, Pipeline, SharePoint, Notifications
   ============================================================ */

window.SettingsPage = (() => {
  let _activeTab = 'model';
  let _settings = null;
  let _models = [];
  let _prompt = '';

  const TABS = [
    { id: 'model',    icon: '🤖', label: 'Model' },
    { id: 'prompt',   icon: '📝', label: 'Prompt' },
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
      _prompt = prompt.status === 'fulfilled' ? (typeof prompt.value === 'string' ? prompt.value : prompt.value?.prompt || '') : '';

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
      case 'prompt':     el.innerHTML = renderPromptTab(); break;
      case 'pipeline':   el.innerHTML = renderPipelineTab(); break;
      case 'sharepoint': el.innerHTML = renderSharePointTab(); break;
      case 'notify':     el.innerHTML = renderNotifyTab(); break;
    }
  }

  /* ---- Model Tab ---- */
  function renderModelTab() {
    const s = _settings || {};
    const backend = s.backend || s.llm_backend || 'vertex_ai';
    const model = s.model || s.llm_model || '';

    return `
      <div class="card animate-in" style="max-width:700px;">
        <div class="card-header">
          <span class="card-title"><span class="icon">🤖</span> Cấu hình Model AI</span>
        </div>

        <!-- Backend selection -->
        <div class="form-group">
          <label class="form-label">Backend</label>
          <div class="radio-group">
            <label class="radio-item">
              <input type="radio" name="backend" value="vertex_ai" ${backend === 'vertex_ai' ? 'checked' : ''} onchange="SettingsPage.onBackendChange()">
              Vertex AI
            </label>
            <label class="radio-item">
              <input type="radio" name="backend" value="api_key" ${backend === 'api_key' ? 'checked' : ''} onchange="SettingsPage.onBackendChange()">
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

        <!-- Vertex AI fields -->
        <div id="vertex-fields" ${backend !== 'vertex_ai' ? 'class="hidden"' : ''}>
          <div class="form-group">
            <label class="form-label">Project ID</label>
            <input type="text" class="form-input" id="set-project" value="${esc(s.project_id || s.vertex_project || '')}" placeholder="my-gcp-project">
          </div>
          <div class="form-group">
            <label class="form-label">Location</label>
            <input type="text" class="form-input" id="set-location" value="${esc(s.location || s.vertex_location || 'us-central1')}" placeholder="us-central1">
          </div>
        </div>

        <!-- API Key fields -->
        <div id="apikey-fields" ${backend !== 'api_key' ? 'class="hidden"' : ''}>
          <div class="form-group">
            <label class="form-label">API Key</label>
            <input type="password" class="form-input" id="set-apikey" value="${esc(s.api_key || '')}" placeholder="••••••••••••">
            <span class="form-hint">Key sẽ được mã hóa khi lưu</span>
          </div>
        </div>

        <!-- Test & Save -->
        <div style="display:flex;gap:12px;margin-top:24px;">
          <button class="btn btn-secondary" id="btn-test-conn" onclick="SettingsPage.testConnection()">
            🔌 Kiểm tra kết nối
          </button>
          <button class="btn btn-primary" onclick="SettingsPage.saveSettings()">
            💾 Lưu cài đặt
          </button>
        </div>

        <!-- Test result -->
        <div id="test-result" class="hidden mt-4"></div>
      </div>
    `;
  }

  function onBackendChange() {
    const backend = document.querySelector('input[name="backend"]:checked')?.value;
    const vertexFields = document.getElementById('vertex-fields');
    const apikeyFields = document.getElementById('apikey-fields');

    if (vertexFields) vertexFields.classList.toggle('hidden', backend !== 'vertex_ai');
    if (apikeyFields) apikeyFields.classList.toggle('hidden', backend !== 'api_key');
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
      result.innerHTML = `
        <div style="padding:12px;border-radius:var(--radius-md);background:rgba(34,197,94,0.1);border:1px solid rgba(34,197,94,0.3);color:var(--accent-green);font-size:13px;">
          ✅ Kết nối thành công! ${res.message || ''}
        </div>
      `;
    } catch (e) {
      result.classList.remove('hidden');
      result.innerHTML = `
        <div style="padding:12px;border-radius:var(--radius-md);background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);color:var(--accent-red);font-size:13px;">
          ❌ Kết nối thất bại: ${esc(e.message)}
        </div>
      `;
    } finally {
      btn.disabled = false;
      btn.innerHTML = '🔌 Kiểm tra kết nối';
    }
  }

  function gatherModelSettings() {
    const backend = document.querySelector('input[name="backend"]:checked')?.value || 'vertex_ai';
    return {
      backend,
      model: document.getElementById('set-model')?.value,
      project_id: document.getElementById('set-project')?.value,
      location: document.getElementById('set-location')?.value,
      api_key: document.getElementById('set-apikey')?.value,
    };
  }

  /* ---- Prompt Tab ---- */
  function renderPromptTab() {
    const wordCount = _prompt ? _prompt.split(/\s+/).length : 0;
    const tokenEstimate = Math.round(wordCount * 1.3);

    return `
      <div class="card animate-in" style="max-width:900px;">
        <div class="card-header">
          <span class="card-title"><span class="icon">📝</span> System Prompt</span>
          <div class="btn-group">
            <button class="btn btn-ghost btn-sm" onclick="SettingsPage.copyPrompt()">📋 Sao chép</button>
            <button class="btn btn-secondary btn-sm" id="btn-edit-prompt" onclick="SettingsPage.toggleEditPrompt()">✏️ Chỉnh sửa</button>
          </div>
        </div>

        <textarea class="form-textarea code" id="prompt-textarea" readonly
                  style="min-height:400px;line-height:1.7;">${esc(_prompt)}</textarea>

        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:12px;">
          <div style="display:flex;gap:16px;font-size:12px;color:var(--text-muted);">
            <span>📝 ${wordCount.toLocaleString()} từ</span>
            <span>🔤 ${tokenEstimate.toLocaleString()} tokens (ước tính)</span>
            <span>📏 ${_prompt.length.toLocaleString()} ký tự</span>
          </div>
          <button class="btn btn-primary hidden" id="btn-save-prompt" onclick="SettingsPage.savePrompt()">
            💾 Lưu prompt
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
      await API.putSettings({ prompt: textarea.value });
      _prompt = textarea.value;
      Toast.success('Đã lưu prompt');
      toggleEditPrompt();
    } catch (e) {
      Toast.error('Lỗi lưu prompt: ' + e.message);
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
          <input type="number" class="form-input" id="set-http-timeout" value="${s.http_timeout || 30}" min="5" max="300" style="max-width:200px;">
        </div>

        <button class="btn btn-primary mt-4" onclick="SettingsPage.savePipelineSettings()">
          💾 Lưu cài đặt Pipeline
        </button>
      </div>
    `;
  }

  /* ---- SharePoint Tab ---- */
  function renderSharePointTab() {
    const s = _settings || {};
    const sp = s.sharepoint || s;
    return `
      <div class="card animate-in" style="max-width:700px;">
        <div class="card-header">
          <span class="card-title"><span class="icon">🔑</span> Kết nối SharePoint</span>
        </div>

        <div class="form-group">
          <label class="form-label">Site URL</label>
          <input type="text" class="form-input" id="set-sp-url" value="${esc(sp.site_url || sp.sharepoint_url || '')}" placeholder="https://company.sharepoint.com/sites/...">
        </div>

        <div class="form-row">
          <div class="form-group">
            <label class="form-label">Client ID</label>
            <input type="text" class="form-input" id="set-sp-client" value="${esc(maskSecret(sp.client_id || ''))}" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx">
          </div>
          <div class="form-group">
            <label class="form-label">Client Secret</label>
            <input type="password" class="form-input" id="set-sp-secret" value="${esc(sp.client_secret || '')}" placeholder="••••••••">
          </div>
        </div>

        <div class="form-row">
          <div class="form-group">
            <label class="form-label">Tenant ID</label>
            <input type="text" class="form-input" id="set-sp-tenant" value="${esc(maskSecret(sp.tenant_id || ''))}" placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx">
          </div>
          <div class="form-group">
            <label class="form-label">Drive ID</label>
            <input type="text" class="form-input" id="set-sp-drive" value="${esc(sp.drive_id || '')}" placeholder="b!xxxxx...">
          </div>
        </div>

        <hr style="border:none;border-top:1px solid var(--border);margin:20px 0;">

        <div class="form-row">
          <div class="form-group">
            <label class="form-label">Thư mục đầu vào</label>
            <input type="text" class="form-input" id="set-sp-input" value="${esc(sp.input_folder || 'Input')}">
          </div>
          <div class="form-group">
            <label class="form-label">Thư mục đầu ra</label>
            <input type="text" class="form-input" id="set-sp-output" value="${esc(sp.output_folder || 'Output')}">
          </div>
        </div>

        <button class="btn btn-primary mt-4" onclick="SettingsPage.saveSettings()">
          💾 Lưu cài đặt
        </button>
      </div>
    `;
  }

  /* ---- Notification Tab ---- */
  function renderNotifyTab() {
    const s = _settings || {};
    const n = s.notifications || s;
    return `
      <div class="card animate-in" style="max-width:700px;">
        <div class="card-header">
          <span class="card-title"><span class="icon">🔔</span> Cấu hình thông báo</span>
        </div>

        <div class="form-group">
          <label class="form-label">Teams Webhook URL</label>
          <input type="url" class="form-input" id="set-webhook" value="${esc(n.teams_webhook || n.webhook_url || '')}" placeholder="https://outlook.office.com/webhook/...">
          <span class="form-hint">Webhook URL để gửi thông báo qua Microsoft Teams</span>
        </div>

        <div class="form-group">
          <label class="form-label">Email thông báo</label>
          <input type="email" class="form-input" id="set-email" value="${esc(n.email || '')}" placeholder="admin@company.com">
        </div>

        <div class="form-group">
          <div class="checkbox-item">
            <input type="checkbox" id="set-notify-success" ${n.notify_on_success !== false ? 'checked' : ''}>
            <span>Thông báo khi xử lý thành công</span>
          </div>
        </div>

        <div class="form-group">
          <div class="checkbox-item">
            <input type="checkbox" id="set-notify-error" ${n.notify_on_error !== false ? 'checked' : ''}>
            <span>Thông báo khi có lỗi</span>
          </div>
        </div>

        <button class="btn btn-primary mt-4" onclick="SettingsPage.saveSettings()">
          💾 Lưu cài đặt
        </button>
      </div>
    `;
  }

  /* ---- Save helpers ---- */
  async function saveSettings() {
    const data = { ..._settings };

    // Gather from current tab
    const model = gatherModelSettings();
    Object.assign(data, model);

    try {
      await API.putSettings(data);
      _settings = { ..._settings, ...data };
      Toast.success('Đã lưu cài đặt thành công');
    } catch (e) {
      Toast.error('Lỗi lưu cài đặt: ' + e.message);
    }
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
    } catch (e) {
      Toast.error('Lỗi lưu: ' + e.message);
    }
  }

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
    toggleEditPrompt, copyPrompt, savePrompt, saveSettings, savePipelineSettings
  };
})();
