/* ============================================================
   Classification Page — Text, File, and Batch modes
   The MOST IMPORTANT page in the application
   ============================================================ */

window.ClassifyPage = (() => {
  let _mode = 'text'; // 'text' | 'file' | 'batch'
  let _wsClient = null;
  let _currentJob = null;
  let _isPaused = false;
  let _lastTextResult = null;
  let _lastTextInput = '';

  /* ---- Label groups matching MINOR_TO_MAJOR from pipeline ---- */
  const LABEL_GROUPS = [
    {
      name: 'Sản phẩm',
      labels: ['Báo lỗi', 'Báo CL tốt', 'Y/c cải tiến', 'Đề xuất SPM']
    },
    {
      name: 'YC Công cụ BH',
      labels: ['Bảng giá, Catalogue', 'Bảng biển', 'Kệ bóng, thử đèn,…', 'Khác']
    },
    {
      name: 'Giá cơ chế',
      labels: ['Tốt/ ko tốt', 'Trả thưởng', 'Đề xuất']
    },
    {
      name: 'Dịch vụ',
      labels: ['Bảo hành', 'HTPP', 'Hàng hoá', 'Hàng giả', 'Website']
    },
    {
      name: 'Đối thủ',
      labels: ['Hãng', 'Hoạt động', 'CTKM, giá, cơ chế', 'TT SP']
    },
    {
      name: 'Khác',
      labels: ['Tin trung lập']
    }
  ];

  function render() {
    const app = document.getElementById('app');
    app.innerHTML = `
      <div class="page-header">
        <h2>⚡ Phân loại phản hồi</h2>
        <p>Phân loại phản hồi khách hàng bằng AI pipeline</p>
      </div>

      <!-- Mode Selector -->
      <div style="margin-bottom:24px;">
        <div class="pill-tabs" id="classify-mode">
          <button class="pill-tab active" data-mode="text" onclick="ClassifyPage.setMode('text')">📝 Đoạn văn bản</button>
          <button class="pill-tab" data-mode="file" onclick="ClassifyPage.setMode('file')">📄 Một file</button>
          <button class="pill-tab" data-mode="batch" onclick="ClassifyPage.setMode('batch')">📁 Nhiều file</button>
        </div>
      </div>

      <!-- Mode Content -->
      <div id="classify-content"></div>
    `;

    renderMode();
    checkActiveJob();
  }

  function setMode(mode) {
    _mode = mode;
    document.querySelectorAll('#classify-mode .pill-tab').forEach(t => {
      t.classList.toggle('active', t.dataset.mode === mode);
    });
    renderMode();
  }

  function renderMode() {
    const el = document.getElementById('classify-content');
    if (!el) return;

    switch (_mode) {
      case 'text': 
        el.innerHTML = renderTextMode();
        // Restore saved text input and result after tab switch
        if (_lastTextInput) {
          const input = document.getElementById('classify-input');
          if (input) { input.value = _lastTextInput; updateCharCount(); }
        }
        if (_lastTextResult) {
          renderTextResult(_lastTextResult);
          setStepState(1, 'done');
          setStepState(2, 'done');
          setStepState(3, 'done');
        }
        break;
      case 'file': 
        el.innerHTML = renderFileMode(); 
        if (_currentJob) {
          restoreActiveJobUI(_currentJob);
          // Reconnect WebSocket if the job is still active
          if ((_currentJob.status === 'running' || _currentJob.status === 'queued') && (!_wsClient || !_wsClient.isOpen())) {
            connectJobWS(_currentJob.job_id || _currentJob.id);
          }
        }
        break;
      case 'batch': 
        el.innerHTML = renderBatchMode(); 
        if (_batchFiles && _batchFiles.length > 0) {
          document.getElementById('batch-queue')?.classList.remove('hidden');
          renderBatchTable();
        }
        break;
    }
  }

  function restoreActiveJobUI(job) {
    if (!job) return;

    // Show/hide UI sections
    const dropzone = document.getElementById('classify-dropzone');
    const info = document.getElementById('file-info');
    const config = document.getElementById('file-config');
    const progress = document.getElementById('file-progress');
    const results = document.getElementById('file-results');

    if (dropzone) dropzone.classList.add('hidden');
    if (config) config.classList.add('hidden');
    if (info) {
      info.classList.remove('hidden');
      document.getElementById('file-info-name').textContent = job.filename || 'Excel File';
      document.getElementById('file-info-size').textContent = '';
    }
    if (progress) progress.classList.remove('hidden');
    if (results) results.classList.remove('hidden');

    // Populate results table
    const tbody = document.getElementById('file-results-tbody');
    const countEl = document.getElementById('result-count');
    if (tbody && job.results) {
      tbody.innerHTML = '';
      job.results.forEach((r, idx) => {
        const num = idx + 1;
        const tr = document.createElement('tr');
        tr.className = 'animate-in';
        tr.innerHTML = `
          <td class="text-muted">${num}</td>
          <td class="wrap" style="max-width:300px;font-size:12px;">${esc(r.text || r.content || '—').substring(0, 150)}...</td>
          <td style="font-size:12px;">${esc(r.product || r.product_name || '—')}</td>
          <td style="font-size:12px;">
            ${(r.labels || []).map(l => `<span class="chip" style="margin:1px;">${esc(typeof l === 'string' ? l : l.label || l.name)}</span>`).join(' ') || '—'}
          </td>
          <td><span class="badge ${sentimentBadge(r.sentiment)}">${esc(r.sentiment || '—')}</span></td>
        `;
        tbody.appendChild(tr);
      });
      if (countEl) countEl.textContent = `${job.results.length} dòng`;
    }

    if (job.status === 'completed') {
      updateFileProgress({
        rows_done: job.total_rows || job.rows_done || 0,
        total_rows: job.total_rows || job.rows_done || 0,
        speed: 0,
        eta: 'Hoàn thành'
      });
      updateFileSteps(4, 'done');
      const dl = document.getElementById('btn-download');
      if (dl) dl.classList.remove('hidden');
      document.getElementById('btn-reset-job')?.classList.remove('hidden');
    } else if (job.status === 'error') {
      const errMsg = job.error || 'Lỗi không xác định';
      const bar = document.getElementById('file-progress-bar');
      if (bar) {
        bar.style.width = '100%';
        bar.style.backgroundColor = 'var(--accent-red)';
      }
      const txt = document.getElementById('file-progress-text');
      if (txt) txt.textContent = `Thất bại: ${errMsg}`;
      const pctEl = document.getElementById('file-progress-pct');
      if (pctEl) pctEl.textContent = 'Lỗi';
      
      updateFileSteps(1, 'failed');
      document.getElementById('btn-reset-job')?.classList.remove('hidden');
    } else {
      // 'running' or 'queued'
      updateFileProgress({
        rows_done: job.rows_done,
        total_rows: job.total_rows,
        speed: 0,
        eta: job.status === 'queued' ? 'Đang chờ xếp hàng...' : 'Đang xử lý...'
      });
      updateFileSteps(job.step || 1, job.step_status || 'running');
    }
  }

  /* ============================================================
     TEXT MODE
     ============================================================ */

  function renderTextMode() {
    return `
      <div class="grid-2-1">
        <div>
          <!-- Input area -->
          <div class="card animate-in">
            <div class="card-header">
              <span class="card-title"><span class="icon">📝</span> Nội dung phản hồi</span>
              <span class="text-muted" style="font-size:12px;" id="char-count">0 ký tự</span>
            </div>
            <textarea class="form-textarea" id="classify-input"
                      style="min-height:180px;font-family:var(--font-sans);font-size:14px;"
                      placeholder="Nhập nội dung phản hồi cần phân loại...&#10;&#10;Ví dụ: Sản phẩm sơn nội thất bị bong tróc sau 2 tháng sử dụng, yêu cầu bảo hành..."
                      oninput="ClassifyPage.updateCharCount()"></textarea>
            <div style="margin-top:14px;display:flex;justify-content:space-between;align-items:center;">
              <button class="btn btn-ghost btn-sm" onclick="ClassifyPage.clearInput()">🗑️ Xóa</button>
              <button class="btn btn-primary" id="btn-classify-text" onclick="ClassifyPage.classifyText()">
                🚀 Phân loại
              </button>
            </div>
          </div>

          <!-- Pipeline Steps -->
          <div class="mt-6 animate-in animate-in-delay-1">
            <div class="card-title mb-4"><span class="icon">⚙️</span> Pipeline xử lý</div>
            <div class="steps" id="pipeline-steps">
              <div class="step-card waiting" id="step-1">
                <div class="step-card-num">Bước 1</div>
                <div class="step-card-title">🤖 LLM Trích xuất</div>
                <div class="step-card-status">Chờ xử lý</div>
              </div>
              <div class="step-connector">→</div>
              <div class="step-card waiting" id="step-2">
                <div class="step-card-num">Bước 2</div>
                <div class="step-card-title">🔍 BM25 Tìm kiếm</div>
                <div class="step-card-status">Chờ xử lý</div>
              </div>
              <div class="step-connector">→</div>
              <div class="step-card waiting" id="step-3">
                <div class="step-card-num">Bước 3</div>
                <div class="step-card-title">🏷️ Phân loại nhãn</div>
                <div class="step-card-status">Chờ xử lý</div>
              </div>
            </div>
          </div>
        </div>

        <!-- Results sidebar -->
        <div>
          <div id="text-result-area">
            <div class="card animate-in animate-in-delay-2">
              <div class="empty-state" style="padding:40px 20px;">
                <div class="empty-state-icon">🎯</div>
                <p class="empty-state-text">Kết quả phân loại</p>
                <p class="empty-state-hint">Nhập văn bản và nhấn "Phân loại" để xem kết quả</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Full-width results below -->
      <div id="text-results-full" class="mt-6 hidden"></div>
    `;
  }

  function updateCharCount() {
    const input = document.getElementById('classify-input');
    const count = document.getElementById('char-count');
    if (input && count) {
      count.textContent = `${input.value.length} ký tự`;
    }
  }

  function clearInput() {
    const input = document.getElementById('classify-input');
    if (input) { input.value = ''; updateCharCount(); }
    _lastTextResult = null;
    _lastTextInput = '';
  }

  async function classifyText() {
    const input = document.getElementById('classify-input');
    const btn = document.getElementById('btn-classify-text');
    if (!input || !input.value.trim()) {
      Toast.warning('Vui lòng nhập nội dung phản hồi');
      return;
    }

    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Đang phân loại...';

    // Animate pipeline steps
    setStepState(1, 'running');
    setStepState(2, 'waiting');
    setStepState(3, 'waiting');

    try {
      // Step 1: Start
      await sleep(300);
      setStepState(1, 'running');

      const result = await API.classifyText({ text: input.value.trim() });

      setStepState(1, 'done');
      await sleep(200);
      setStepState(2, 'done');
      await sleep(200);
      setStepState(3, 'done');

      renderTextResult(result);
      _lastTextResult = result;
      _lastTextInput = input.value;
      Toast.success('Phân loại hoàn tất!');
    } catch (e) {
      setStepState(1, 'done');
      setStepState(2, 'done');
      setStepState(3, 'waiting');
      Toast.error('Lỗi phân loại: ' + e.message);
    } finally {
      btn.disabled = false;
      btn.innerHTML = '🚀 Phân loại';
    }
  }

  function setStepState(num, state) {
    const step = document.getElementById(`step-${num}`);
    if (!step) return;
    step.className = `step-card ${state}`;
    const statusEl = step.querySelector('.step-card-status');
    if (statusEl) {
      switch (state) {
        case 'waiting': statusEl.textContent = 'Chờ xử lý'; break;
        case 'running': statusEl.textContent = 'Đang xử lý...'; break;
        case 'done':    statusEl.textContent = 'Hoàn thành ✓'; break;
      }
    }
  }

  function renderTextResult(result) {
    const sidebar = document.getElementById('text-result-area');
    const fullArea = document.getElementById('text-results-full');
    if (!sidebar || !fullArea) return;

    // Product match card in sidebar
    const prod = result.product || {};
    const sentiment = result.sentiment || '—';
    const brand = result.brand || '—';

    sidebar.innerHTML = `
      <div class="result-card animate-in">
        <div class="result-card-header">🎯 Kết quả sản phẩm</div>
        <div class="result-card-body">
          <div class="result-row">
            <span class="result-key">LLM trích xuất</span>
            <span class="result-value">${esc(prod.llm_extracted || '—')}</span>
          </div>
          <div class="result-row">
            <span class="result-key">Model</span>
            <span class="result-value text-mono" style="font-size:11px;">${esc(prod.model || '—')}</span>
          </div>
          <div class="result-row">
            <span class="result-key">Dòng SP</span>
            <span class="result-value">${esc(prod.dong_sp || '—')}</span>
          </div>
          <div class="result-row">
            <span class="result-key">Sản phẩm</span>
            <span class="result-value" style="color:var(--accent-blue);font-weight:700;">${esc(prod.san_pham || '—')}</span>
          </div>
          <div class="result-row">
            <span class="result-key">Điểm BM25</span>
            <span class="result-value">${prod.score != null ? Number(prod.score).toFixed(3) : '—'}</span>
          </div>
          <div class="result-row">
            <span class="result-key">Nguồn</span>
            <span class="badge ${prod.src === 'BM25' ? 'badge-blue' : 'badge-purple'}">${esc(prod.src || '—')}</span>
          </div>
        </div>
      </div>

      <!-- Sentiment & Brand -->
      <div class="card mt-4 animate-in animate-in-delay-1">
        <div class="result-row">
          <span class="result-key">💬 Cảm xúc</span>
          <span class="badge ${sentimentBadge(sentiment)}">${esc(sentiment || 'Trung tính')}</span>
        </div>
        <div class="result-row">
          <span class="result-key">🏢 Thương hiệu</span>
          <span class="result-value">${esc(brand || 'Rạng Đông')}</span>
        </div>
      </div>
    `;

    // Labels: API returns {labelName: true/false} dict
    const labelsDict = result.labels || {};
    const activeLabels = Object.entries(labelsDict)
      .filter(([, v]) => v === true)
      .map(([k]) => k);
    const decisionLog = result.decision_log || [];

    fullArea.classList.remove('hidden');
    fullArea.innerHTML = `
      <!-- Label Grid -->
      <div class="card animate-in">
        <div class="card-header">
          <span class="card-title"><span class="icon">🏷️</span> Phân loại nhãn</span>
          <span class="text-muted" style="font-size:12px;">${activeLabels.length} nhãn được chọn</span>
        </div>
        ${renderLabelGrid(labelsDict)}
      </div>

      <!-- Decision Log -->
      <div class="card mt-6 animate-in animate-in-delay-1">
        <div class="card-header">
          <span class="card-title"><span class="icon">📋</span> Nhật ký quyết định</span>
        </div>
        ${renderDecisionLog(decisionLog)}
      </div>
    `;
  }

  function renderLabelGrid(labelsDict) {
    // labelsDict is {labelName: true/false}
    if (!labelsDict || typeof labelsDict !== 'object') labelsDict = {};

    return `
      <div class="label-grid">
        ${LABEL_GROUPS.map(group => `
          <div class="label-group">
            <div class="label-group-header">${esc(group.name)}</div>
            <div class="label-group-items">
              ${group.labels.map(label => {
                const isChecked = labelsDict[label] === true;
                return `
                  <div class="label-item ${isChecked ? 'checked' : 'unchecked'}">
                    <div class="label-check">${isChecked ? '✓' : ''}</div>
                    <span>${esc(label)}</span>
                  </div>
                `;
              }).join('')}
            </div>
          </div>
        `).join('')}
      </div>
    `;
  }

  function renderDecisionLog(decisions) {
    if (!decisions || decisions.length === 0) {
      return '<p class="text-muted text-center" style="padding:16px;">Không có nhật ký quyết định</p>';
    }

    return `
      <div class="table-wrap">
        <table class="table">
          <thead>
            <tr>
              <th style="width:40px;">#</th>
              <th>Nhãn</th>
              <th>Hành động</th>
              <th>Bằng chứng</th>
              <th>Lý do</th>
            </tr>
          </thead>
          <tbody>
            ${decisions.map((d, i) => `
              <tr>
                <td class="text-muted">${i + 1}</td>
                <td style="font-weight:600;">${esc(d.label || d.name || '—')}</td>
                <td>
                  <span class="badge ${(d.action||'').toUpperCase() === 'ADD' || (d.action||'').toLowerCase() === 'yes' ? 'badge-green' : 'badge-red'}">
                    ${(d.action||'').toUpperCase() === 'ADD' || (d.action||'').toLowerCase() === 'yes' ? '✅ Thêm' : '❌ Bỏ'}
                  </span>
                </td>
                <td class="wrap" style="max-width:250px;font-size:12px;">${esc(d.evidence || '—')}</td>
                <td class="wrap" style="max-width:250px;font-size:12px;color:var(--text-secondary);">${esc(d.reason || '—')}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    `;
  }

  function sentimentBadge(s) {
    if (!s) return 'badge-muted';
    const lower = s.toLowerCase();
    if (lower.includes('tích cực') || lower.includes('positive')) return 'badge-green';
    if (lower.includes('tiêu cực') || lower.includes('negative')) return 'badge-red';
    if (lower.includes('trung tính') || lower.includes('neutral')) return 'badge-amber';
    return 'badge-muted';
  }

  /* ============================================================
     FILE MODE
     ============================================================ */

  function renderFileMode() {
    const isJobActive = _currentJob && (_currentJob.status === 'running' || _currentJob.status === 'queued' || _currentJob.status === 'completed' || _currentJob.status === 'error');

    return `
      <div class="card animate-in">
        <!-- Dropzone -->
        <div class="dropzone ${isJobActive ? 'hidden' : ''}" id="classify-dropzone"
             ondragover="ClassifyPage.fileDragOver(event)"
             ondragleave="ClassifyPage.fileDragLeave(event)"
             ondrop="ClassifyPage.fileDrop(event)"
             onclick="document.getElementById('classify-file-picker').click()">
          <div class="dropzone-icon">📄</div>
          <div class="dropzone-text">Kéo thả file Excel (.xlsx) vào đây</div>
          <div class="dropzone-hint">hoặc nhấn để chọn file</div>
          <input type="file" id="classify-file-picker" style="display:none;"
                 accept=".xlsx" onchange="ClassifyPage.handleFile(this.files[0])">
        </div>

        <!-- Selected file info -->
        <div id="file-info" class="${!isJobActive ? 'hidden' : ''} mt-4">
          <div style="display:flex;align-items:center;gap:12px;padding:12px;background:var(--bg-tertiary);border-radius:var(--radius-md);border:1px solid var(--border);">
            <span style="font-size:24px;">📊</span>
            <div style="flex:1;">
              <div style="font-weight:600;" id="file-info-name">${esc(_currentJob ? _currentJob.filename : '—')}</div>
              <div class="text-muted" style="font-size:12px;" id="file-info-size">—</div>
            </div>
            ${isJobActive ? '' : '<button class="btn btn-ghost btn-sm" onclick="ClassifyPage.clearFile()">✕</button>'}
          </div>
        </div>

        <!-- Config -->
        <div id="file-config" class="hidden mt-4">
          <div class="form-row">
            <div class="form-group">
              <label class="form-label">Batch Size</label>
              <input type="number" class="form-input" id="cfg-batch-size" value="10" min="1" max="100">
              <span class="form-hint">Số dòng xử lý mỗi lô</span>
            </div>
            <div class="form-group">
              <label class="form-label">Checkpoint mỗi</label>
              <input type="number" class="form-input" id="cfg-checkpoint" value="50" min="10" max="1000">
              <span class="form-hint">Lưu checkpoint mỗi N dòng</span>
            </div>
          </div>
          <button class="btn btn-primary" id="btn-classify-file" onclick="ClassifyPage.startFileClassify()">
            🚀 Bắt đầu phân loại
          </button>
        </div>
      </div>

      <!-- Progress -->
      <div id="file-progress" class="${!isJobActive ? 'hidden' : ''} mt-6">
        <div class="card animate-in">
          <div class="card-header">
            <span class="card-title"><span class="icon">📊</span> Tiến trình phân loại</span>
            <div class="btn-group">
              <button class="btn btn-secondary btn-sm" id="btn-pause" onclick="ClassifyPage.togglePause()">⏸️ Tạm dừng</button>
              <button class="btn btn-danger btn-sm" onclick="ClassifyPage.stopClassify()">⏹️ Dừng</button>
              <button class="btn btn-success btn-sm hidden" id="btn-download" onclick="ClassifyPage.downloadResult()">📥 Tải kết quả</button>
              <button class="btn btn-primary btn-sm hidden" id="btn-reset-job" onclick="ClassifyPage.resetJob()">🔄 Phân loại lại</button>
            </div>
          </div>

          <!-- Progress bar -->
          <div class="progress-wrap lg">
            <div class="progress-bar" id="file-progress-bar" style="width:0%"></div>
          </div>
          <div class="progress-info">
            <span id="file-progress-text">0 / 0 dòng</span>
            <div style="display:flex;gap:16px;">
              <span id="file-eta">ETA: —</span>
              <span id="file-speed">0 dòng/phút</span>
            </div>
            <span class="progress-pct" id="file-progress-pct">0%</span>
          </div>

          <!-- Current step -->
          <div class="steps mt-4" id="file-pipeline-steps">
            <div class="step-card waiting" id="fstep-1">
              <div class="step-card-num">Bước 1</div>
              <div class="step-card-title">🤖 LLM</div>
              <div class="step-card-status">Chờ</div>
            </div>
            <div class="step-connector">→</div>
            <div class="step-card waiting" id="fstep-2">
              <div class="step-card-num">Bước 2</div>
              <div class="step-card-title">🔍 BM25</div>
              <div class="step-card-status">Chờ</div>
            </div>
            <div class="step-connector">→</div>
            <div class="step-card waiting" id="fstep-3">
              <div class="step-card-num">Bước 3</div>
              <div class="step-card-title">🏷️ Nhãn</div>
              <div class="step-card-status">Chờ</div>
            </div>
          </div>
        </div>
      </div>

      <!-- Live Results Table -->
      <div id="file-results" class="${!isJobActive ? 'hidden' : ''} mt-6">
        <div class="card animate-in">
          <div class="card-header">
            <span class="card-title"><span class="icon">📋</span> Kết quả phân loại</span>
            <span class="text-muted" style="font-size:12px;" id="result-count">0 dòng</span>
          </div>
          <div class="table-wrap" style="max-height:400px;overflow-y:auto;">
            <table class="table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Nội dung</th>
                  <th>Sản phẩm</th>
                  <th>Nhãn</th>
                  <th>Cảm xúc</th>
                </tr>
              </thead>
              <tbody id="file-results-tbody"></tbody>
            </table>
          </div>
        </div>
      </div>
    `;
  }

  let _selectedFile = null;

  function fileDragOver(e) { e.preventDefault(); e.currentTarget.classList.add('dragover'); }
  function fileDragLeave(e) { e.currentTarget.classList.remove('dragover'); }
  function fileDrop(e) {
    e.preventDefault();
    e.currentTarget.classList.remove('dragover');
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  }

  function handleFile(file) {
    if (!file) return;
    _selectedFile = file;
    const info = document.getElementById('file-info');
    const config = document.getElementById('file-config');
    if (info) {
      info.classList.remove('hidden');
      document.getElementById('file-info-name').textContent = file.name;
      document.getElementById('file-info-size').textContent = formatSize(file.size);
    }
    if (config) config.classList.remove('hidden');
    document.getElementById('classify-dropzone')?.classList.add('hidden');
  }

  function clearFile() {
    _selectedFile = null;
    document.getElementById('file-info')?.classList.add('hidden');
    document.getElementById('file-config')?.classList.add('hidden');
    document.getElementById('classify-dropzone')?.classList.remove('hidden');
    document.getElementById('classify-file-picker').value = '';
  }

  async function startFileClassify() {
    if (!_selectedFile) {
      Toast.warning('Vui lòng chọn file');
      return;
    }

    const batchSize = parseInt(document.getElementById('cfg-batch-size')?.value) || 10;
    const checkpoint = parseInt(document.getElementById('cfg-checkpoint')?.value) || 50;

    const fd = new FormData();
    fd.append('file', _selectedFile);
    fd.append('batch_size', batchSize);
    fd.append('checkpoint_every', checkpoint);

    const btn = document.getElementById('btn-classify-file');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Đang khởi tạo...';

    try {
      const job = await API.classifyFile(fd);
      _currentJob = job;
      const jobId = job.job_id || job.id;

      document.getElementById('file-progress')?.classList.remove('hidden');
      document.getElementById('file-results')?.classList.remove('hidden');
      document.getElementById('file-config')?.classList.add('hidden');

      // Connect WebSocket for progress
      connectJobWS(jobId);
      Toast.info(`Job ${jobId} đã bắt đầu`);
    } catch (e) {
      Toast.error('Lỗi bắt đầu phân loại: ' + e.message);
      btn.disabled = false;
      btn.innerHTML = '🚀 Bắt đầu phân loại';
    }
  }

  function connectJobWS(jobId) {
    if (_wsClient) _wsClient.close();

    _wsClient = WS.classifyWS(jobId, {
      onOpen: () => {
        console.log('[WS] Connected to job:', jobId);
      },
      onClose: () => {
        console.log('[WS] Disconnected from job:', jobId);
      },
      onProgress: (data) => {
        console.log('[WS] Progress:', data);
        updateFileProgress(data);
        if (data.step) {
          updateFileSteps(data.step, data.step_status);
        }
        if (_currentJob) {
          _currentJob.rows_done = data.rows_done;
          _currentJob.total_rows = data.total_rows;
          _currentJob.step = data.step;
          _currentJob.step_status = data.step_status;
        }
      },
      onBatchResult: (data) => {
        appendBatchResults(data);
        if (_currentJob) {
          if (!_currentJob.results) _currentJob.results = [];
          const rows = data.rows || data.results || [data];
          _currentJob.results.push(...rows);
        }
      },
      onComplete: (data) => {
        console.log('[WS] Complete:', data);
        onJobComplete(data);
        if (_currentJob) {
          _currentJob.status = 'completed';
          _currentJob.rows_done = data.rows_done;
        }
      },
      onError: (data) => {
        console.error('[WS] Error:', data);
        const errMsg = data.error || data.message || 'Lỗi không xác định';
        Toast.error('Lỗi job: ' + errMsg);
        if (_currentJob) {
          _currentJob.status = 'error';
          _currentJob.error = errMsg;
        }
        
        // Show error visually
        const bar = document.getElementById('file-progress-bar');
        if (bar) {
          bar.style.width = '100%';
          bar.style.backgroundColor = 'var(--accent-red)';
        }
        const txt = document.getElementById('file-progress-text');
        if (txt) txt.textContent = `Thất bại: ${errMsg}`;
        const pctEl = document.getElementById('file-progress-pct');
        if (pctEl) pctEl.textContent = 'Lỗi';
        
        updateFileSteps(1, 'failed');
        // Show reset button on error
        document.getElementById('btn-reset-job')?.classList.remove('hidden');
      },
      onMessage: (data) => {
        if (data.step) {
          updateFileSteps(data.step, data.step_status || data.status);
        }
      }
    });
  }

  async function checkActiveJob() {
    if (_currentJob) return;
    try {
      const jobs = await API.getJobs();
      if (!Array.isArray(jobs) || jobs.length === 0) return;

      // Find the most recent active job (running or queued)
      const activeJob = jobs
        .filter(j => j.status === 'running' || j.status === 'queued')
        .sort((a, b) => new Date(b.created_at) - new Date(a.created_at))[0];

      if (activeJob) {
        _currentJob = activeJob;
        _mode = 'file';
        
        // Render file mode UI
        setMode('file');

        // Populate selected file info
        document.getElementById('classify-dropzone')?.classList.add('hidden');
        const info = document.getElementById('file-info');
        if (info) {
          info.classList.remove('hidden');
          document.getElementById('file-info-name').textContent = activeJob.filename || 'Excel File';
          document.getElementById('file-info-size').textContent = '';
        }

        // Show progress panel & results panel
        document.getElementById('file-progress')?.classList.remove('hidden');
        document.getElementById('file-results')?.classList.remove('hidden');
        document.getElementById('file-config')?.classList.add('hidden');

        // Populate progress bar and progress text
        updateFileProgress({
          rows_done: activeJob.rows_done,
          rows_total: activeJob.total_rows,
          speed: 0,
          eta: 'Đang chạy ngầm...'
        });

        // Set sub-steps progress
        updateFileSteps(activeJob.step || 1, activeJob.step_status || 'running');

        // Render existing batch results
        const tbody = document.getElementById('file-results-tbody');
        const countEl = document.getElementById('result-count');
        if (tbody && activeJob.results) {
          tbody.innerHTML = '';
          activeJob.results.forEach((r, idx) => {
            const num = idx + 1;
            const tr = document.createElement('tr');
            tr.className = 'animate-in';
            tr.innerHTML = `
              <td class="text-muted">${num}</td>
              <td class="wrap" style="max-width:300px;font-size:12px;">${esc(r.text || r.content || '—').substring(0, 150)}...</td>
              <td style="font-size:12px;">${esc(r.product || r.product_name || '—')}</td>
              <td style="font-size:12px;">
                ${(r.labels || []).map(l => `<span class="chip" style="margin:1px;">${esc(typeof l === 'string' ? l : l.label || l.name)}</span>`).join(' ') || '—'}
              </td>
              <td><span class="badge ${sentimentBadge(r.sentiment)}">${esc(r.sentiment || '—')}</span></td>
            `;
            tbody.appendChild(tr);
          });
          if (countEl) countEl.textContent = `${activeJob.results.length} dòng`;
        }

        // Connect WebSocket
        connectJobWS(activeJob.job_id);
      }
    } catch (e) {
      console.warn('Lỗi kiểm tra active job:', e);
    }
  }

  function updateFileProgress(data) {
    const done = data.rows_done || data.processed || 0;
    const total = data.total_rows || data.total || 1;
    const pct = Math.round((done / total) * 100);
    const speed = data.speed || 0;
    const eta = data.eta || '—';

    const bar = document.getElementById('file-progress-bar');
    const text = document.getElementById('file-progress-text');
    const pctEl = document.getElementById('file-progress-pct');
    const etaEl = document.getElementById('file-eta');
    const speedEl = document.getElementById('file-speed');

    if (bar) bar.style.width = pct + '%';
    if (text) text.textContent = `${done} / ${total} dòng`;
    if (pctEl) pctEl.textContent = pct + '%';
    if (etaEl) etaEl.textContent = `ETA: ${eta}`;
    if (speedEl) speedEl.textContent = `${speed.toFixed(1)} dòng/phút`;
  }

  function updateFileSteps(step, status) {
    for (let i = 1; i <= 3; i++) {
      const el = document.getElementById(`fstep-${i}`);
      if (!el) continue;
      if (i < step) {
        el.className = 'step-card done';
        el.querySelector('.step-card-status').textContent = 'Xong ✓';
      } else if (i === step) {
        el.className = `step-card ${status || 'running'}`;
        el.querySelector('.step-card-status').textContent = status === 'done' ? 'Xong ✓' : 'Đang xử lý...';
      } else {
        el.className = 'step-card waiting';
        el.querySelector('.step-card-status').textContent = 'Chờ';
      }
    }
  }

  function appendBatchResults(data) {
    const tbody = document.getElementById('file-results-tbody');
    const countEl = document.getElementById('result-count');
    if (!tbody) return;

    const rows = data.rows || data.results || [data];
    rows.forEach((r, idx) => {
      const num = tbody.children.length + 1;
      const tr = document.createElement('tr');
      tr.className = 'animate-in';
      tr.innerHTML = `
        <td class="text-muted">${num}</td>
        <td class="wrap" style="max-width:300px;font-size:12px;">${esc(r.text || r.content || '—').substring(0, 150)}...</td>
        <td style="font-size:12px;">${esc(r.product || r.product_name || '—')}</td>
        <td style="font-size:12px;">
          ${(r.labels || []).map(l => `<span class="chip" style="margin:1px;">${esc(typeof l === 'string' ? l : l.label || l.name)}</span>`).join(' ') || '—'}
        </td>
        <td><span class="badge ${sentimentBadge(r.sentiment)}">${esc(r.sentiment || '—')}</span></td>
      `;
      tbody.appendChild(tr);
    });

    if (countEl) countEl.textContent = `${tbody.children.length} dòng`;
    tbody.lastElementChild?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function onJobComplete(data) {
    Toast.success('Phân loại hoàn tất!');
    updateFileProgress({ rows_done: data.total_rows || data.total || 0, total_rows: data.total_rows || data.total || 0, speed: 0, eta: 'Xong' });
    updateFileSteps(4, 'done');

    const dl = document.getElementById('btn-download');
    if (dl) dl.classList.remove('hidden');

    if (_wsClient) {
      _wsClient.close();
      _wsClient = null;
    }
  }

  function togglePause() {
    _isPaused = !_isPaused;
    const btn = document.getElementById('btn-pause');
    if (btn) {
      btn.innerHTML = _isPaused ? '▶️ Tiếp tục' : '⏸️ Tạm dừng';
    }
    if (_wsClient) {
      _wsClient.send({ action: _isPaused ? 'pause' : 'resume' });
    }
  }

  function stopClassify() {
    if (_wsClient) {
      _wsClient.send({ action: 'stop' });
      _wsClient.close();
      _wsClient = null;
    }
    Toast.warning('Đã dừng phân loại');
    // Show reset button
    document.getElementById('btn-reset-job')?.classList.remove('hidden');
  }

  function resetJob() {
    // Close WS if still open
    if (_wsClient) {
      _wsClient.close();
      _wsClient = null;
    }
    // Clear job state
    _currentJob = null;
    _selectedFile = null;
    _isPaused = false;
    // Re-render file mode fresh
    renderMode();
    Toast.info('Đã reset. Chọn file mới để phân loại.');
  }

  function downloadResult() {
    if (_currentJob) {
      const jobId = _currentJob.job_id || _currentJob.id;
      window.open(`/api/classify/jobs/${jobId}/download`, '_blank');
    }
  }

  /* ============================================================
     BATCH MODE
     ============================================================ */

  function renderBatchMode() {
    return `
      <div class="card animate-in">
        <!-- Multiple file upload -->
        <div class="dropzone" id="batch-dropzone"
             ondragover="ClassifyPage.fileDragOver(event)"
             ondragleave="ClassifyPage.fileDragLeave(event)"
             ondrop="ClassifyPage.batchDrop(event)"
             onclick="document.getElementById('batch-file-picker').click()">
          <div class="dropzone-icon">📁</div>
          <div class="dropzone-text">Kéo thả nhiều file Excel vào đây</div>
          <div class="dropzone-hint">Hỗ trợ: .xlsx — Chọn nhiều file cùng lúc</div>
          <input type="file" id="batch-file-picker" style="display:none;"
                 accept=".xlsx" multiple onchange="ClassifyPage.handleBatchFiles(this.files)">
        </div>
      </div>

      <!-- Queue Table -->
      <div id="batch-queue" class="hidden mt-6">
        <div class="card animate-in">
          <div class="card-header">
            <span class="card-title"><span class="icon">📋</span> Hàng đợi xử lý</span>
            <div class="btn-group">
              <button class="btn btn-primary btn-sm" id="btn-batch-start" onclick="ClassifyPage.startBatch()">🚀 Bắt đầu tất cả</button>
              <button class="btn btn-danger btn-sm" onclick="ClassifyPage.clearBatch()">🗑️ Xóa tất cả</button>
            </div>
          </div>

          <!-- Overall progress -->
          <div class="mb-4">
            <div class="progress-wrap">
              <div class="progress-bar" id="batch-overall-bar" style="width:0%"></div>
            </div>
            <div class="progress-info">
              <span id="batch-overall-text">0 / 0 file</span>
              <span class="progress-pct" id="batch-overall-pct">0%</span>
            </div>
          </div>

          <div class="table-wrap">
            <table class="table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Tên file</th>
                  <th>Kích thước</th>
                  <th>Trạng thái</th>
                  <th style="width:200px;">Tiến trình</th>
                </tr>
              </thead>
              <tbody id="batch-tbody"></tbody>
            </table>
          </div>
        </div>
      </div>
    `;
  }

  let _batchFiles = [];
  let _batchDone = 0;

  function batchDrop(e) {
    e.preventDefault();
    e.currentTarget.classList.remove('dragover');
    handleBatchFiles(e.dataTransfer.files);
  }

  function handleBatchFiles(fileList) {
    if (!fileList || fileList.length === 0) return;
    _batchFiles = Array.from(fileList);
    _batchDone = 0;

    document.getElementById('batch-queue')?.classList.remove('hidden');
    renderBatchTable();
  }

  function renderBatchTable() {
    const tbody = document.getElementById('batch-tbody');
    if (!tbody) return;

    tbody.innerHTML = _batchFiles.map((f, i) => `
      <tr id="batch-row-${i}">
        <td class="text-muted">${i + 1}</td>
        <td style="font-weight:500;">${esc(f.name)}</td>
        <td class="text-muted text-mono" style="font-size:12px;">${formatSize(f.size)}</td>
        <td><span class="badge badge-muted" id="batch-status-${i}">⏳ Chờ</span></td>
        <td>
          <div class="progress-wrap" style="height:6px;">
            <div class="progress-bar" id="batch-bar-${i}" style="width:0%"></div>
          </div>
        </td>
      </tr>
    `).join('');

    updateBatchOverall();
  }

  async function startBatch() {
    const btn = document.getElementById('btn-batch-start');
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Đang xử lý...'; }

    for (let i = 0; i < _batchFiles.length; i++) {
      const statusEl = document.getElementById(`batch-status-${i}`);
      const barEl = document.getElementById(`batch-bar-${i}`);

      if (statusEl) statusEl.innerHTML = '<span class="badge badge-blue">🔄 Đang xử lý (0%)</span>';

      const fd = new FormData();
      fd.append('file', _batchFiles[i]);
      fd.append('batch_size', 10);

      try {
        const job = await API.classifyFile(fd);
        const jobId = job.job_id || job.id;
        
        let completed = false;
        while (!completed) {
          await sleep(2000);
          const statusJob = await API.get(`/classify/jobs/${jobId}`);
          const status = statusJob.status;
          
          if (status === 'completed') {
            completed = true;
            if (statusEl) statusEl.innerHTML = `<span class="badge badge-green">✅ Hoàn thành</span> <button class="btn btn-ghost btn-sm" onclick="window.open('/api/classify/jobs/${jobId}/download', '_blank')" title="Tải kết quả" style="padding: 2px 6px; margin-left: 4px; font-size: 11px;">📥 Tải</button>`;
            if (barEl) barEl.style.width = '100%';
          } else if (status === 'error') {
            completed = true;
            if (statusEl) statusEl.innerHTML = `<span class="badge badge-red" title="${esc(statusJob.error || 'Lỗi không xác định')}">❌ Thất bại</span>`;
          } else {
            const done = statusJob.rows_done || 0;
            const total = statusJob.total_rows || 1;
            const pct = Math.round((done / total) * 100);
            if (barEl) barEl.style.width = `${pct}%`;
            if (statusEl) statusEl.innerHTML = `<span class="badge badge-blue">🔄 Đang xử lý (${pct}%)</span>`;
          }
        }
      } catch (e) {
        if (statusEl) statusEl.innerHTML = '<span class="badge badge-red">❌ Thất bại</span>';
      }

      _batchDone = i + 1;
      updateBatchOverall();
    }

    if (btn) { btn.disabled = false; btn.innerHTML = '🚀 Bắt đầu tất cả'; }
    Toast.success(`Đã xử lý ${_batchDone}/${_batchFiles.length} file`);
  }

  function updateBatchOverall() {
    const total = _batchFiles.length;
    const pct = total > 0 ? Math.round((_batchDone / total) * 100) : 0;
    const bar = document.getElementById('batch-overall-bar');
    const text = document.getElementById('batch-overall-text');
    const pctEl = document.getElementById('batch-overall-pct');

    if (bar) bar.style.width = pct + '%';
    if (text) text.textContent = `${_batchDone} / ${total} file`;
    if (pctEl) pctEl.textContent = pct + '%';
  }

  function clearBatch() {
    _batchFiles = [];
    _batchDone = 0;
    document.getElementById('batch-queue')?.classList.add('hidden');
    document.getElementById('batch-file-picker').value = '';
  }

  /* ---- Helpers ---- */
  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  function formatSize(bytes) {
    if (!bytes) return '—';
    const units = ['B', 'KB', 'MB', 'GB'];
    let idx = 0, size = bytes;
    while (size >= 1024 && idx < units.length - 1) { size /= 1024; idx++; }
    return `${size.toFixed(idx > 0 ? 1 : 0)} ${units[idx]}`;
  }

  function esc(s) {
    if (s == null) return '';
    const d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
  }

  function destroy() {
    if (_wsClient) {
      _wsClient.close();
      _wsClient = null;
    }
    // Preserve _currentJob, _selectedFile, and _batchFiles so that state is maintained when switching tabs
  }

  return {
    render, destroy, setMode,
    updateCharCount, clearInput, classifyText,
    fileDragOver, fileDragLeave, fileDrop, handleFile, clearFile,
    startFileClassify, togglePause, stopClassify, downloadResult, resetJob,
    batchDrop, handleBatchFiles, startBatch, clearBatch
  };
})();
