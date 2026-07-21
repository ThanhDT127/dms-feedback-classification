/* ============================================================
   Classification Page — Text, File, and Batch modes
   The MOST IMPORTANT page in the application
   ============================================================ */

window.ClassifyPage = (() => {
  let _mode = 'text'; // 'text' | 'file' | 'batch' | 'config'
  let _wsClient = null;
  let _currentJob = null;
  let _isPaused = false;
  let _lastTextResult = null;
  let _lastTextInput = '';
  let _adminJobs = [];
  let _adminJobMetrics = null;

  let _labelGroups = [];

  // Config tab cache (task 3.3)
  let _configPrompt = null;
  let _configKeywords = null;
  let _configProducts = null;
  let _configSheetTab = 0;
  let _productEditorColumns = [];
  let _productEditorSheetName = '';

  function isAdmin() {
    return window.App?.state?.user?.role === 'admin';
  }

  function isTerminalStatus(status) {
    return ['completed', 'error', 'cancelled'].includes(status);
  }

  function isRetryingJob(job) {
    return job?.status === 'queued' && Number(job?.retry_count || 0) > 0;
  }

  /**
   * Strip UUID prefix from output filenames for user-friendly display.
   * e.g. "a1b2c3d4-e5f6-7890-abcd-ef1234567890_output_file.xlsx" → "output_file.xlsx"
   */
  function getFriendlyFileName(path) {
    if (!path) return '';
    // Get just the filename from a full path
    const name = path.split('/').pop() || path.split('\\').pop() || path;
    // Strip leading UUID prefix (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx_)
    return name.replace(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_/i, '');
  }

  function jobStatusMeta(job) {
    const status = job?.status || 'unknown';
    if (status === 'running' && job?.cancellation_requested) {
      return { label: 'Đang hủy...', badge: 'badge-purple', message: 'Yêu cầu hủy đã được ghi nhận. Đang dừng tác vụ một cách an toàn...' };
    }
    if (isRetryingJob(job)) {
      return { label: 'Đang chờ chạy lại', badge: 'badge-purple', message: 'Job đang chờ chạy lại sau lỗi hoặc hủy trước đó.' };
    }
    const map = {
      queued: { label: 'Đang xếp hàng', badge: 'badge-amber', message: 'Job đã được nhận và đang chờ tới lượt xử lý.' },
      running: { label: 'Đang xử lý', badge: 'badge-blue', message: 'AI pipeline đang xử lý dữ liệu và sẽ cập nhật kết quả theo từng lô.' },
      completed: { label: 'Hoàn tất', badge: 'badge-green', message: 'Job đã hoàn tất. Bạn có thể tải file kết quả hoặc mở liên kết SharePoint nếu có.' },
      error: { label: 'Thất bại', badge: 'badge-red', message: 'Job thất bại. Vui lòng thử lại hoặc liên hệ admin nếu lỗi lặp lại.' },
      cancelled: { label: 'Đã hủy', badge: 'badge-muted', message: 'Job đã bị hủy và không còn tiếp tục xử lý.' },
    };
    return map[status] || { label: status, badge: 'badge-muted', message: 'Trạng thái job chưa xác định.' };
  }

  function statusBadge(job) {
    const meta = jobStatusMeta(job);
    return `<span class="badge ${meta.badge}">${esc(meta.label)}</span>`;
  }

  function formatDateTime(value) {
    if (!value) return '—';
    try {
      return new Date(value).toLocaleString('vi-VN');
    } catch (_) {
      return String(value);
    }
  }

  function configPromptText() {
    return _configPrompt?.raw_template || _configPrompt?.prompt_template || _configPrompt?.prompt || _configPrompt?.system_prompt || '';
  }

  function downloadTemplate() {
    return API.download('/files/template', 'template_dms.xlsx');
  }

  function downloadJob(jobId) {
    if (!jobId) return;
    return API.download(`/classify/jobs/${encodeURIComponent(jobId)}/download`, `ket_qua_${jobId}.xlsx`);
  }

  async function loadLabels() {
    try {
      const data = await API.getLabels();
      const groupsMap = {};
      const minorOrder = data.minor_order || Object.keys(data.minor_to_major || {});
      const minorToMajor = data.minor_to_major || {};
      for (const minor of minorOrder) {
        const major = minorToMajor[minor];
        if (major) {
          if (!groupsMap[major]) {
            groupsMap[major] = [];
          }
          groupsMap[major].push(minor);
        }
      }
      _labelGroups = Object.entries(groupsMap).map(([name, labels]) => ({ name, labels }));
    } catch (e) {
      console.error("Failed to load labels dynamically", e);
      _labelGroups = [
        { name: 'Sản phẩm', labels: ['Báo lỗi', 'Báo CL tốt', 'Y/c cải tiến', 'Đề xuất SPM'] },
        { name: 'Yêu cầu công cụ BH', labels: ['Bảng giá, Catalogue', 'Bảng biển', 'Kệ bóng, thử đèn,…', 'Khác'] },
        { name: 'Giá, cơ chế RD', labels: ['Tốt/ ko tốt', 'Trả thưởng', 'Đề xuất'] },
        { name: 'Dịch vụ', labels: ['Bảo hành', 'HTPP', 'Hàng hoá'] },
        { name: 'Hàng giả', labels: ['Hàng giả'] },
        { name: 'Website', labels: ['Website'] },
        { name: 'Đối thủ cạnh tranh', labels: ['Hãng', 'Hoạt động', 'CTKM, giá, cơ chế', 'TT SP'] },
        { name: 'Tin trung lập', labels: ['Tin trung lập'] }
      ];
    }
  }

  async function render() {
    const app = document.getElementById('app');
    if (_labelGroups.length === 0) {
      await loadLabels();
    }
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
          ${isAdmin() ? '<button class="pill-tab" data-mode="jobs" onclick="ClassifyPage.setMode(\'jobs\')">📋 Jobs</button>' : ''}
          ${isAdmin() ? '<button class="pill-tab" data-mode="config" onclick="ClassifyPage.setMode(\'config\')">⚙️ Cấu hình</button>' : ''}
        </div>
      </div>

      <!-- Mode Content -->
      <div id="classify-content"></div>
    `;

    renderMode();
    checkActiveJob();
  }

  function setMode(mode) {
    if ((mode === 'config' || mode === 'jobs') && !isAdmin()) {
      Toast.error('Bạn không có quyền truy cập cấu hình');
      mode = 'text';
    }
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
          refreshCurrentJobStatus(_currentJob.job_id || _currentJob.id);
          // Reconnect WebSocket if the job is still active
          if ((_currentJob.status === 'running' || _currentJob.status === 'queued') && !_currentJob.terminal && (!_wsClient || !_wsClient.isOpen())) {
            connectJobWS(_currentJob.job_id || _currentJob.id);
          }
          // Restore pause button state (task 1.3)
          const pauseBtn = document.getElementById('btn-pause');
          if (pauseBtn && _isPaused) {
            pauseBtn.innerHTML = '▶️ Tiếp tục';
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
      case 'config':
        el.innerHTML = renderConfigMode();
        loadConfigData();
        break;
      case 'jobs':
        el.innerHTML = renderAdminJobsMode();
        loadAdminJobs();
        break;
    }
  }

  function renderJobStatusNotice(job) {
    const progressWrap = document.getElementById('file-progress')?.querySelector('.card');
    if (!progressWrap || !job) return;
    let notice = document.getElementById('job-status-notice');
    if (!notice) {
      notice = document.createElement('div');
      notice.id = 'job-status-notice';
      notice.style.cssText = 'margin:12px 0;padding:10px 12px;background:var(--bg-tertiary);border:1px solid var(--border);border-radius:8px;display:flex;align-items:center;justify-content:space-between;gap:12px;';
      progressWrap.insertBefore(notice, progressWrap.querySelector('.progress-wrap'));
    }
    const meta = jobStatusMeta(job);
    const retry = Number(job.retry_count || 0) > 0 ? `<span class="text-muted" style="font-size:11px;">Retry: ${Number(job.retry_count || 0)}</span>` : '';
    notice.innerHTML = `
      <div>
        <div style="display:flex;align-items:center;gap:8px;">${statusBadge(job)} ${retry}</div>
        <div class="text-muted" style="font-size:12px;margin-top:4px;">${esc(meta.message)}</div>
      </div>
      <div class="text-muted" style="font-size:11px;text-align:right;">
        ${job.queued_at ? `<div>Queued: ${esc(formatDateTime(job.queued_at))}</div>` : ''}
        ${job.started_at ? `<div>Started: ${esc(formatDateTime(job.started_at))}</div>` : ''}
      </div>
    `;
  }

  function renderTerminalJobActions(job) {
    const progressWrap = document.getElementById('file-progress')?.querySelector('.card');
    if (!progressWrap || !job) return;
    let infoBar = document.getElementById('output-info-bar');
    if (!infoBar) {
      infoBar = document.createElement('div');
      infoBar.id = 'output-info-bar';
      infoBar.style.cssText = 'margin-top:12px;padding:12px 16px;background:var(--bg-card);border:1px solid var(--border);border-radius:8px;display:flex;align-items:center;justify-content:space-between;gap:12px;';
      progressWrap.appendChild(infoBar);
    }
    const jobId = job.job_id || job.id;
    renderJobStatusNotice(job);

    if (job.status === 'completed') {
      const spLinkHtml = job.sp_web_url
        ? `<a href="${escAttr(job.sp_web_url)}" target="_blank" class="btn btn-secondary btn-sm" style="text-decoration:none;">☁️ Xem SharePoint</a>`
        : `<button class="btn btn-secondary btn-sm" id="btn-push-sp-${jobId}" onclick="ClassifyPage.pushToSharePoint('${jobId}')">☁️ Đẩy SharePoint</button>`;
      infoBar.style.borderColor = 'var(--accent-green)';
      infoBar.innerHTML = `
        <div>
          <div style="font-weight:600;font-size:13px;">Phân loại hoàn tất</div>
          <div class="text-muted" style="font-size:11px;margin-top:2px;">${esc(job.filename || getFriendlyFileName(job.output_path))}</div>
        </div>
        <div style="display:flex;gap:6px;align-items:center;">
          <button class="btn btn-success btn-sm" onclick="ClassifyPage.downloadJob('${jobId}')">📥 Tải file kết quả</button>
          ${spLinkHtml}
        </div>
      `;
    } else if (job.status === 'error') {
      infoBar.style.borderColor = 'var(--accent-red)';
      infoBar.innerHTML = `
        <div>
          <div style="font-weight:600;font-size:13px;color:var(--accent-red);">Job thất bại</div>
          <div class="text-muted" style="font-size:12px;margin-top:2px;">${esc(job.error_summary || job.error || 'Vui lòng thử lại hoặc liên hệ admin.')}</div>
        </div>
        <button class="btn btn-secondary btn-sm" onclick="ClassifyPage.resetJob()">Chọn file khác</button>
      `;
    } else if (job.status === 'cancelled') {
      infoBar.style.borderColor = 'var(--border)';
      infoBar.innerHTML = `
        <div>
          <div style="font-weight:600;font-size:13px;">Job đã hủy</div>
          <div class="text-muted" style="font-size:12px;margin-top:2px;">Tiến trình đã dừng và sẽ không tiếp tục xử lý.</div>
        </div>
        <button class="btn btn-secondary btn-sm" onclick="ClassifyPage.resetJob()">Chọn file khác</button>
      `;
    }
  }

  async function refreshCurrentJobStatus(jobId) {
    if (!jobId) return;
    try {
      const job = await API.get(`/classify/jobs/${encodeURIComponent(jobId)}`, { silent: true });
      _currentJob = { ...(_currentJob || {}), ...job };
      if (isTerminalStatus(job.status)) {
        if (_wsClient) {
          _wsClient.close();
          _wsClient = null;
        }
        restoreActiveJobUI(_currentJob);
      }
    } catch (e) {
      console.warn('Không thể làm mới trạng thái job:', e);
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
          <td style="font-size:12px;">${esc(r.product_line || '—')}</td>
          <td style="font-size:12px;">${esc(r.model || '—')}</td>
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
        rows_done: job.total_rows ?? job.rows_done ?? 0,
        total_rows: job.total_rows ?? job.rows_done ?? 0,
        speed: 0,
        eta: 'Hoàn thành',
        status: 'completed'
      });
      updateFileSteps(4, 'done');
      const dl = document.getElementById('btn-download');
      if (dl) dl.classList.remove('hidden');
      document.getElementById('btn-reset-job')?.classList.remove('hidden');
      renderTerminalJobActions(job);

      // Show completed info bar
      const jobId = job.job_id || job.id;
      const outputPath = job.output_path || '';
      const spWebUrl = job.sp_web_url || '';
      const progressWrap = document.getElementById('file-progress')?.querySelector('.card');
      if (progressWrap && jobId) {
        let infoBar = document.getElementById('output-info-bar');
        if (!infoBar) {
          infoBar = document.createElement('div');
          infoBar.id = 'output-info-bar';
          infoBar.style.cssText = 'margin-top:12px;padding:12px 16px;background:var(--bg-card);border:1px solid var(--accent-green);border-radius:8px;display:flex;align-items:center;justify-content:space-between;gap:12px;';
          progressWrap.appendChild(infoBar);
        }
        const spLinkHtml = spWebUrl
          ? `<a href="${escAttr(spWebUrl)}" target="_blank" class="btn btn-secondary btn-sm" style="text-decoration:none;">☁️ Xem SharePoint</a>`
          : `<button class="btn btn-secondary btn-sm" id="btn-push-sp-${jobId}" onclick="ClassifyPage.pushToSharePoint('${jobId}')">☁️ Đẩy SharePoint</button>`;
        infoBar.innerHTML = `
          <div style="display:flex;align-items:center;gap:8px;">
            <span style="color:var(--accent-green);font-size:18px;">✅</span>
            <div>
              <div style="font-weight:600;font-size:13px;">Phân loại hoàn tất</div>
              ${outputPath ? `<div class="text-muted" style="font-size:11px;margin-top:2px;">📁 ${esc(job.filename || getFriendlyFileName(outputPath))}</div>` : ''}
            </div>
          </div>
          <div style="display:flex;gap:6px;align-items:center;">
            <button class="btn btn-success btn-sm" onclick="ClassifyPage.downloadJob('${jobId}')">
              📥 Tải file kết quả (.xlsx)
            </button>
            ${spLinkHtml}
          </div>
        `;
      }
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
      renderTerminalJobActions(job);
    } else if (job.status === 'cancelled') {
      const bar = document.getElementById('file-progress-bar');
      if (bar) {
        bar.style.width = `${job.percent || 0}%`;
        bar.style.backgroundColor = 'var(--border-light)';
      }
      const txt = document.getElementById('file-progress-text');
      if (txt) txt.textContent = 'Job đã hủy';
      const pctEl = document.getElementById('file-progress-pct');
      if (pctEl) pctEl.textContent = 'Đã hủy';
      updateFileSteps(job.step || 1, 'waiting');
      document.getElementById('btn-reset-job')?.classList.remove('hidden');
      renderTerminalJobActions(job);
    } else {
      // 'running' or 'queued'
      updateFileProgress({
        rows_done: job.rows_done ?? 0,
        total_rows: job.total_rows ?? 0,
        speed: 0,
        eta: isRetryingJob(job) ? 'Đang chờ chạy lại...' : (job.status === 'queued' ? 'Đang chờ xếp hàng...' : 'Đang xử lý...'),
        status: job.status
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
        ${_labelGroups.map(group => `
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
    const isJobActive = _currentJob && ['queued', 'running', 'completed', 'error', 'cancelled'].includes(_currentJob.status);

    return `
      ${renderExcelGuide()}
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
              <input type="number" class="form-input" id="cfg-batch-size" value="15" min="1" max="100">
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
              <button class="btn btn-primary btn-sm hidden" id="btn-reset-job" onclick="ClassifyPage.resetJob()">🔄 Reset</button>
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
              <div class="step-card-title">🤖 Trích xuất SP</div>
              <div class="step-card-status">Chờ</div>
            </div>
            <div class="step-connector">→</div>
            <div class="step-card waiting" id="fstep-2">
              <div class="step-card-num">Bước 2</div>
              <div class="step-card-title">🔍 Tra cứu SP</div>
              <div class="step-card-status">Chờ</div>
            </div>
            <div class="step-connector">→</div>
            <div class="step-card waiting" id="fstep-3">
              <div class="step-card-num">Bước 3</div>
              <div class="step-card-title">🏷️ Gán nhãn</div>
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
                  <th>Dòng SP</th>
                  <th>Model</th>
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

    const batchSize = parseInt(document.getElementById('cfg-batch-size')?.value) || 15;
    const checkpoint = parseInt(document.getElementById('cfg-checkpoint')?.value) || 50;

    const fd = new FormData();
    fd.append('file', _selectedFile);
    fd.append('batch_size', batchSize);
    fd.append('checkpoint_every', checkpoint);
    fd.append('mode', 'single');

    const btn = document.getElementById('btn-classify-file');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Đang khởi tạo...';

    try {
      const job = await API.classifyFile(fd);
      _currentJob = job;
      const jobId = job.job_id || job.id;
      sessionStorage.setItem('classify_active_job_id', jobId);
      sessionStorage.removeItem('classify_reset');

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
        // Clear previous results to avoid duplication on reconnect/connect
        if (_currentJob) {
          _currentJob.results = [];
        }
        const tbody = document.getElementById('file-results-tbody');
        if (tbody) {
          tbody.innerHTML = '';
        }
        const countEl = document.getElementById('result-count');
        if (countEl) {
          countEl.textContent = '0 dòng';
        }
      },
      onClose: () => {
        console.log('[WS] Disconnected from job:', jobId);
      },
      onProgress: (data) => {
        console.log('[WS] Progress:', data);
        if (_currentJob && isTerminalStatus(_currentJob.status)) return;
        // Null-safe: DOM elements may not exist if user is on another page (task 1.2)
        if (document.getElementById('file-progress-bar')) {
          updateFileProgress(data);
        }
        if (data.step && document.getElementById('file-pipeline-steps')) {
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
        if (_currentJob && isTerminalStatus(_currentJob.status)) return;
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
          _currentJob = { ..._currentJob, ...data, status: 'completed' };
          restoreActiveJobUI(_currentJob);
        }
      },
      onError: (data) => {
        console.error('[WS] Error:', data);
        const errMsg = data.error || data.message;
        if (!errMsg) {
          // Filter out browser network/disconnect events
          console.warn('[WS] Transient connection error or closed connection.');
          return;
        }
        Toast.error('Lỗi job: ' + errMsg);
        if (_currentJob) {
          _currentJob.status = 'error';
          _currentJob.error = errMsg;
          _currentJob.error_summary = errMsg;
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
        renderJobStatusNotice(_currentJob);
        renderTerminalJobActions(_currentJob);
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

      // Restoring active job logic based on sessionStorage and status
      const activeJobId = sessionStorage.getItem('classify_active_job_id');
      const wasReset = sessionStorage.getItem('classify_reset') === '1';

      const activeJob = jobs
        .filter(j => {
          if (j.mode === 'batch') return false;
          if (wasReset) {
            return j.status === 'queued' || j.status === 'running';
          }
          // Always restore active or queued jobs
          if (j.status === 'queued' || j.status === 'running') return true;
          // Restore completed/error/cancelled ONLY if they match the active job started in this session
          if (activeJobId && (j.job_id === activeJobId || j.id === activeJobId)) return true;
          return false;
        })
        .sort((a, b) => new Date(b.created_at) - new Date(a.created_at))[0];

      // Clear reset flag once we've checked
      if (wasReset) sessionStorage.removeItem('classify_reset');

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
          total_rows: activeJob.total_rows,
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
              <td style="font-size:12px;">${esc(r.product_line || '—')}</td>
              <td style="font-size:12px;">${esc(r.model || '—')}</td>
              <td style="font-size:12px;">
                ${(r.labels || []).map(l => `<span class="chip" style="margin:1px;">${esc(typeof l === 'string' ? l : l.label || l.name)}</span>`).join(' ') || '—'}
              </td>
              <td><span class="badge ${sentimentBadge(r.sentiment)}">${esc(r.sentiment || '—')}</span></td>
            `;
            tbody.appendChild(tr);
          });
          if (countEl) countEl.textContent = `${activeJob.results.length} dòng`;
        }

        if (!isTerminalStatus(activeJob.status)) {
          connectJobWS(activeJob.job_id);
        }
      }
    } catch (e) {
      console.warn('Lỗi kiểm tra active job:', e);
    }
  }

  function updateFileProgress(data) {
    const done = data.rows_done ?? data.processed ?? 0;
    const total = data.total_rows ?? data.total ?? 0;
    const pct = total > 0 ? Math.round((done / total) * 100) : 0;
    const speed = data.speed || 0;
    const eta = data.eta || '—';

    const bar = document.getElementById('file-progress-bar');
    const text = document.getElementById('file-progress-text');
    const pctEl = document.getElementById('file-progress-pct');
    const etaEl = document.getElementById('file-eta');
    const speedEl = document.getElementById('file-speed');

    if (bar) bar.style.width = (pct || 0) + '%';
    // Show helpful message when waiting for first batch
    if (text) {
      if (data.status === 'queued') {
        text.textContent = data.retry_count > 0 ? 'Đang chờ chạy lại' : 'Đang chờ xếp hàng';
      } else if (done === 0 && total > 0 && data.status !== 'completed') {
        text.textContent = `Đang gọi AI xử lý... (0 / ${total} dòng)`;
      } else {
        text.textContent = `${done} / ${total} dòng`;
      }
    }
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
        <td style="font-size:12px;">${esc(r.product_line || '—')}</td>
        <td style="font-size:12px;">${esc(r.model || '—')}</td>
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
    updateFileProgress({ rows_done: data.total_rows ?? data.total ?? 0, total_rows: data.total_rows ?? data.total ?? 0, speed: 0, eta: 'Xong', status: 'completed' });
    updateFileSteps(4, 'done');

    const dl = document.getElementById('btn-download');
    if (dl) dl.classList.remove('hidden');
    document.getElementById('btn-reset-job')?.classList.remove('hidden');

    // Show output info bar
    const jobId = _currentJob?.job_id || _currentJob?.id || data.job_id;
    const outputPath = data.output_path || _currentJob?.output_path || '';
    const duration = data.duration_seconds ? `${Math.round(data.duration_seconds)}s` : '';
    const spWebUrl = data.sp_web_url || _currentJob?.sp_web_url || '';
    const progressWrap = document.getElementById('file-progress')?.querySelector('.card');
    if (progressWrap && jobId) {
      let infoBar = document.getElementById('output-info-bar');
      if (!infoBar) {
        infoBar = document.createElement('div');
        infoBar.id = 'output-info-bar';
        infoBar.style.cssText = 'margin-top:12px;padding:12px 16px;background:var(--bg-card);border:1px solid var(--accent-green);border-radius:8px;display:flex;align-items:center;justify-content:space-between;gap:12px;';
        progressWrap.appendChild(infoBar);
      }
      const spLinkHtml = spWebUrl
        ? `<a href="${escAttr(spWebUrl)}" target="_blank" class="btn btn-secondary btn-sm" style="text-decoration:none;">☁️ Xem SharePoint</a>`
        : `<button class="btn btn-secondary btn-sm" id="btn-push-sp-${jobId}" onclick="ClassifyPage.pushToSharePoint('${jobId}')">☁️ Đẩy SharePoint</button>`;
      infoBar.innerHTML = `
        <div style="display:flex;align-items:center;gap:8px;">
          <span style="color:var(--accent-green);font-size:18px;">✅</span>
          <div>
            <div style="font-weight:600;font-size:13px;">Phân loại hoàn tất${duration ? ' — ' + duration : ''}</div>
            ${outputPath ? `<div class="text-muted" style="font-size:11px;margin-top:2px;">📁 ${esc(_currentJob?.filename || getFriendlyFileName(outputPath))}</div>` : ''}
          </div>
        </div>
        <div style="display:flex;gap:6px;align-items:center;">
          <button class="btn btn-success btn-sm" onclick="ClassifyPage.downloadJob('${jobId}')">📥 Tải file kết quả (.xlsx)</button>
          ${spLinkHtml}
        </div>
      `;
    }

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

  async function stopClassify() {
    const jobId = _currentJob?.job_id || _currentJob?.id;
    if (jobId) {
      try {
        await API.cancelJob(jobId);
        _currentJob = { ..._currentJob, status: 'cancelled', terminal: true };
        restoreActiveJobUI(_currentJob);
      } catch (e) {
        Toast.error('Không thể hủy job: ' + e.message);
        return;
      }
    }
    if (_wsClient) {
      _wsClient.send({ action: 'stop' });
      _wsClient.close();
      _wsClient = null;
    }
    Toast.warning('Đã hủy job phân loại');
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
    // Mark reset so page refresh won't auto-restore terminal jobs
    sessionStorage.setItem('classify_reset', '1');
    sessionStorage.removeItem('classify_active_job_id');
    // Re-render file mode fresh
    renderMode();
    Toast.info('Đã reset. Chọn file mới để phân loại.');
  }

  function downloadResult() {
    if (_currentJob) {
      const jobId = _currentJob.job_id || _currentJob.id;
      downloadJob(jobId);
    }
  }

  /* ============================================================
     BATCH MODE
     ============================================================ */

  function renderBatchMode() {
    return `
      ${renderExcelGuide()}
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
                  <th style="width:40px;"></th>
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
  let _batchState = [];
  let _batchDone = 0;
  let _isBatchRunning = false;

  function batchDrop(e) {
    e.preventDefault();
    e.currentTarget.classList.remove('dragover');
    handleBatchFiles(e.dataTransfer.files);
  }

  function handleBatchFiles(fileList) {
    if (!fileList || fileList.length === 0) return;
    const newFiles = Array.from(fileList);
    const startIdx = _batchFiles.length;

    // Append mode: add new files to end of existing queue instead of overwriting
    newFiles.forEach(f => {
      _batchFiles.push(f);
      _batchState.push({
        name: f.name,
        size: f.size,
        status: 'pending', // 'pending', 'running', 'completed', 'failed'
        percent: 0,
        jobId: null,
        error: null,
        spWebUrl: null
      });
    });

    document.getElementById('batch-queue')?.classList.remove('hidden');

    if (_isBatchRunning) {
      // Batch is running: append new rows directly to DOM without full re-render
      // (avoids resetting progress bars / status badges of in-progress rows)
      const tbody = document.getElementById('batch-tbody');
      if (tbody) {
        newFiles.forEach((_, j) => {
          const i = startIdx + j;
          const tr = document.createElement('tr');
          tr.id = `batch-row-${i}`;
          tr.innerHTML = `
            <td class="text-muted">${i + 1}</td>
            <td style="font-weight:500;">${esc(_batchState[i].name)}</td>
            <td class="text-muted text-mono" style="font-size:12px;">${formatSize(_batchState[i].size)}</td>
            <td><span class="badge badge-muted" id="batch-status-${i}">⏳ Chờ</span></td>
            <td>
              <div class="progress-wrap" style="height:6px;">
                <div class="progress-bar" id="batch-bar-${i}" style="width:0%"></div>
              </div>
            </td>
            <td></td>
          `;
          tbody.appendChild(tr);
        });
        updateBatchOverall();
      }
    } else {
      renderBatchTable();
    }
  }

  function renderBatchTable() {
    const tbody = document.getElementById('batch-tbody');
    if (!tbody) return;

    tbody.innerHTML = _batchState.map((state, i) => {
      let statusHtml = '';
      if (state.status === 'pending') {
        statusHtml = `<span class="badge badge-muted" id="batch-status-${i}">⏳ Chờ</span>`;
      } else if (state.status === 'running') {
        statusHtml = `<span class="badge badge-blue" id="batch-status-${i}">🔄 Đang xử lý (${state.percent}%)</span>`;
      } else if (state.status === 'completed') {
        const spLink = state.spWebUrl
          ? `<button class="btn btn-ghost btn-sm" onclick="window.open('${escAttr(state.spWebUrl)}', '_blank')" title="Xem trên SharePoint" style="padding: 2px 6px; margin-left: 4px; font-size: 11px;">☁️ Cloud</button>`
          : `<button class="btn btn-ghost btn-sm" id="btn-push-sp-batch-${i}" onclick="ClassifyPage.pushToSharePoint('${state.jobId}', ${i})" title="Đẩy lên SharePoint" style="padding: 2px 6px; margin-left: 4px; font-size: 11px;">☁️ Đẩy SP</button>`;
        statusHtml = `
          <span class="badge badge-green" id="batch-status-${i}">✅ Hoàn thành</span>
          <button class="btn btn-ghost btn-sm" onclick="ClassifyPage.downloadJob('${state.jobId}')" title="Tải kết quả" style="padding: 2px 6px; margin-left: 4px; font-size: 11px;">📥 Tải</button>
          ${spLink}
        `;
      } else if (state.status === 'failed') {
        statusHtml = `<span class="badge badge-red" id="batch-status-${i}" title="${esc(state.error || 'Lỗi không xác định')}">❌ Thất bại</span>`;
      }

      return `
        <tr id="batch-row-${i}">
          <td class="text-muted">${i + 1}</td>
          <td style="font-weight:500;">${esc(state.name)}</td>
          <td class="text-muted text-mono" style="font-size:12px;">${formatSize(state.size)}</td>
          <td>${statusHtml}</td>
          <td>
            <div class="progress-wrap" style="height:6px;">
              <div class="progress-bar" id="batch-bar-${i}" style="width:${state.percent}%"></div>
            </div>
          </td>
          <td>${_isBatchRunning ? '' : `<button class="btn btn-ghost btn-sm" id="batch-remove-${i}" onclick="ClassifyPage.removeBatchFile(${i})" title="Xóa file" style="padding:2px 6px;font-size:11px;">✕</button>`}</td>
        </tr>
      `;
    }).join('');

    updateBatchOverall();

    const btn = document.getElementById('btn-batch-start');
    if (btn) {
      if (_isBatchRunning) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> Đang xử lý...';
      } else {
        btn.disabled = false;
        btn.innerHTML = '🚀 Bắt đầu tất cả';
      }
    }
  }

  function updateBatchRowUI(i) {
    const state = _batchState[i];
    if (!state) return;

    const statusEl = document.getElementById(`batch-status-${i}`);
    const barEl = document.getElementById(`batch-bar-${i}`);

    if (statusEl) {
      if (state.status === 'pending') {
        statusEl.innerHTML = `<span class="badge badge-muted">⏳ Chờ</span>`;
      } else if (state.status === 'queued') {
        statusEl.innerHTML = `<span class="badge badge-muted">⏳ Đang chờ xếp hàng</span>`;
      } else if (state.status === 'running') {
        let stepText = '';
        if (state.step === 1) {
          stepText = ' - B1: Trích xuất SP';
        } else if (state.step === 2) {
          stepText = ' - B2: Tra cứu SP';
        } else if (state.step === 3) {
          stepText = ' - B3: Gán nhãn';
        }
        
        let rowsText = '';
        if (state.totalRows > 0) {
          rowsText = ` (${state.rowsDone}/${state.totalRows} dòng)`;
        } else {
          rowsText = ' (Khởi tạo)';
        }

        statusEl.innerHTML = `<span class="badge badge-blue">🔄 Đang xử lý${rowsText}${stepText}</span>`;
      } else if (state.status === 'completed') {
        const spLink = state.spWebUrl
          ? `<button class="btn btn-ghost btn-sm" onclick="window.open('${escAttr(state.spWebUrl)}', '_blank')" title="Xem trên SharePoint" style="padding: 2px 6px; margin-left: 4px; font-size: 11px;">☁️ Cloud</button>`
          : `<button class="btn btn-ghost btn-sm" id="btn-push-sp-batch-${i}" onclick="ClassifyPage.pushToSharePoint('${state.jobId}', ${i})" title="Đẩy lên SharePoint" style="padding: 2px 6px; margin-left: 4px; font-size: 11px;">☁️ Đẩy SP</button>`;
        statusEl.innerHTML = `
          <span class="badge badge-green">✅ Hoàn thành</span>
          <button class="btn btn-ghost btn-sm" onclick="ClassifyPage.downloadJob('${state.jobId}')" title="Tải kết quả" style="padding: 2px 6px; margin-left: 4px; font-size: 11px;">📥 Tải</button>
          ${spLink}
        `;
      } else if (state.status === 'cancelled') {
        statusEl.innerHTML = `<span class="badge badge-muted">❌ Đã hủy</span>`;
      } else if (state.status === 'failed') {
        statusEl.innerHTML = `<span class="badge badge-red" title="${esc(state.error || 'Lỗi không xác định')}">❌ Thất bại</span>`;
      }
    }

    if (barEl) {
      barEl.style.width = `${state.percent}%`;
    }
  }

  // Process a single file in the batch at a given index.
  // Isolated: errors only affect this index, not the whole queue.
  async function _processBatchFile(index) {
    _batchState[index].status = 'running';
    _batchState[index].percent = 0;
    updateBatchRowUI(index);

    const fd = new FormData();
    fd.append('file', _batchFiles[index]);
    fd.append('batch_size', 10);
    fd.append('mode', 'batch');

    try {
      const job = await API.classifyFile(fd);
      const jobId = job.job_id || job.id;
      _batchState[index].jobId = jobId;

      let completed = false;
      while (!completed) {
        await sleep(2000);
        const statusJob = await API.get(`/classify/jobs/${jobId}`);
        const status = statusJob.status;

        if (status === 'completed') {
          completed = true;
          _batchState[index].status = 'completed';
          _batchState[index].percent = 100;
          _batchState[index].spWebUrl = statusJob.sp_web_url || null;
        } else if (status === 'error') {
          completed = true;
          _batchState[index].status = 'failed';
          _batchState[index].error = statusJob.error || 'Lỗi không xác định';
        } else if (status === 'cancelled') {
          completed = true;
          _batchState[index].status = 'cancelled';
          _batchState[index].error = 'Tác vụ bị hủy';
        } else {
          const done = statusJob.rows_done || 0;
          const total = statusJob.total_rows || 0;
          const pct = total > 0 ? Math.round((done / total) * 100) : 0;
          _batchState[index].status = statusJob.status || 'running';
          _batchState[index].percent = pct;
          _batchState[index].rowsDone = done;
          _batchState[index].totalRows = total;
          _batchState[index].step = statusJob.step || null;
          _batchState[index].stepStatus = statusJob.step_status || null;
        }

        updateBatchRowUI(index);
      }
    } catch (e) {
      // Task 2.2: isolate per-file errors — only this row fails, queue continues
      _batchState[index].status = 'failed';
      _batchState[index].error = e.message || 'Lỗi kết nối';
      updateBatchRowUI(index);
    }

    _batchDone++;
    updateBatchOverall();
  }

  async function startBatch() {
    if (_isBatchRunning) return;
    _isBatchRunning = true;

    const btn = document.getElementById('btn-batch-start');
    if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Đang xử lý...'; }

    renderBatchTable(); // lock remove buttons during processing

    try {
      _batchDone = _batchState.filter(s => s.status === 'completed').length;
      updateBatchOverall();

      // Task 2.1: While-loop drains all pending items, including files appended mid-run.
      // Each iteration finds all current 'pending' indices and runs them concurrently.
      // Loop exits only when no pending items remain in the queue.
      while (true) {
        const pendingIndices = _batchState
          .map((s, i) => s.status === 'pending' ? i : -1)
          .filter(i => i !== -1);

        if (pendingIndices.length === 0) break;

        // Launch all currently-pending files concurrently
        await Promise.all(pendingIndices.map(index => _processBatchFile(index)));

        // Brief yield so any appended files can register in _batchState
        await sleep(300);
      }
    } finally {
      _isBatchRunning = false;
      const activeBtn = document.getElementById('btn-batch-start');
      if (activeBtn) { activeBtn.disabled = false; activeBtn.innerHTML = '🚀 Bắt đầu tất cả'; }
      renderBatchTable(); // restore remove buttons
      Toast.success(`Đã xử lý ${_batchDone}/${_batchFiles.length} file`);
    }
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

  function removeBatchFile(index) {
    if (_isBatchRunning) return;
    if (index < 0 || index >= _batchFiles.length) return;
    _batchFiles.splice(index, 1);
    _batchState.splice(index, 1);
    if (_batchFiles.length === 0) {
      clearBatch();
    } else {
      renderBatchTable();
    }
  }

  function clearBatch() {
    _batchFiles = [];
    _batchState = [];
    _batchDone = 0;
    _isBatchRunning = false;
    document.getElementById('batch-queue')?.classList.add('hidden');
    const picker = document.getElementById('batch-file-picker');
    if (picker) picker.value = '';
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

  function escAttr(s) {
    if (s == null) return '';
    return String(s).replace(/'/g, "\\'").replace(/"/g, '&quot;');
  }

  async function pushToSharePoint(jobId, batchIndex = null) {
    if (!jobId) return;

    let btn;
    if (batchIndex !== null) {
      btn = document.getElementById(`btn-push-sp-batch-${batchIndex}`);
    } else {
      btn = document.getElementById(`btn-push-sp-${jobId}`);
    }

    const originalHtml = btn ? btn.innerHTML : '';
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span> Đang đẩy...';
    }

    try {
      const res = await API.uploadJobToSharePoint(jobId);
      const spWebUrl = res.sp_web_url;
      Toast.success('Đã tải thành công file input và output lên SharePoint');

      if (batchIndex !== null) {
        if (_batchState[batchIndex]) {
          _batchState[batchIndex].spWebUrl = spWebUrl;
          updateBatchRowUI(batchIndex);
        }
      } else {
        if (_currentJob && (_currentJob.job_id === jobId || _currentJob.id === jobId)) {
          _currentJob.sp_web_url = spWebUrl;
          restoreActiveJobUI(_currentJob);
        }
      }
    } catch (e) {
      Toast.error('Không thể upload lên SharePoint: ' + e.message);
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = originalHtml;
      }
    }
  }

  function destroy() {
    // Task 1.1: Do NOT close WS in destroy() — let it persist across page navigations
    // WS messages continue updating _currentJob state in background
    // _wsClient.close() is only called by stopClassify() and resetJob() (intentional user actions)
    // Preserve _currentJob, _selectedFile, _batchFiles, _isPaused so state persists
  }

  // === Excel Guide (tasks 2.1-2.5) ===

  function renderExcelGuide() {
    return `
      <div class="excel-guide-banner animate-in" style="margin-bottom:16px; font-size:13px; display:flex; flex-direction:column; gap:8px; background:rgba(245,158,11,0.05); border:1px solid rgba(245,158,11,0.2); border-radius:var(--radius-md); padding:12px; width:100%;">
        <div style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px;">
          <span style="color:var(--accent-amber); font-weight:500; display:flex; align-items:center; gap:6px;">
            ⚠️ Định dạng Excel yêu cầu: Cột văn bản bắt buộc phải chứa chữ "Nội dung" hoặc "noi dung".
          </span>
          <div style="display:flex; gap:8px;">
            <button class="btn btn-secondary btn-sm" style="padding:4px 10px; font-size:12px;" onclick="ClassifyPage.previewTemplate()">👁️ Xem cấu trúc mẫu</button>
            <button class="btn btn-ghost btn-sm" style="font-size:12px; padding:4px 10px; color:var(--text-secondary);" onclick="ClassifyPage.downloadTemplate()"><span style="text-decoration:underline;">Tải file mẫu (.xlsx)</span></button>
          </div>
        </div>
        <p style="font-size:12px; color:var(--text-muted); margin:0; line-height:1.5;">
          Hệ thống sẽ quét cột này để phân loại. Các cột thông tin đi kèm (như Tên, Ngày, Mã phản hồi...) sẽ được tự động giữ nguyên và đi kèm trong kết quả phân loại xuất ra.
        </p>
      </div>
    `;
  }

  function previewTemplate() {
    const columns = ['Nội dung phản hồi', 'Người gửi', 'Ghi chú (Tùy chọn)'];
    const rows = [
      { 'Nội dung phản hồi': 'Ứng dụng chạy mượt nhưng đôi khi lag nhẹ khi tải dữ liệu lớn.', 'Người gửi': 'Nguyễn Văn A', 'Ghi chú (Tùy chọn)': 'Góp ý giao diện' },
      { 'Nội dung phản hồi': 'Không thể đăng nhập vào tài khoản từ sáng nay, báo lỗi kết nối.', 'Người gửi': 'Trần Thị B', 'Ghi chú (Tùy chọn)': 'Lỗi kỹ thuật' }
    ];
    let tableHtml = '<table class="table" style="font-size:12px;"><thead><tr>' + columns.map(c => `<th>${esc(c)}</th>`).join('') + '</tr></thead><tbody>';
    rows.forEach(r => {
      tableHtml += '<tr>' + columns.map(c => `<td>${esc(r[c])}</td>`).join('') + '</tr>';
    });
    tableHtml += '</tbody></table>';

    openClassifyModal('📄 Cấu trúc file Excel mẫu', `
      <div style="font-size:13px;color:var(--text-secondary);line-height:1.6;margin-bottom:16px;">
        <p style="margin:0 0 8px;">📌 <strong>Cột bắt buộc:</strong> Tiêu đề chứa từ "Nội dung" hoặc "noi dung".</p>
        <p style="margin:0;">Các cột bổ sung sẽ được giữ nguyên trong kết quả phân loại.</p>
      </div>
      <div style="overflow-x:auto;">${tableHtml}</div>
      <div style="margin-top:12px;"><button class="btn btn-primary btn-sm" onclick="ClassifyPage.downloadTemplate()">📥 Tải file mẫu (.xlsx)</button></div>
    `);
  }

  // === Classify Modal (task 7.1) ===

  function openClassifyModal(title, contentHtml, onSave) {
    // Remove existing modal if any
    document.getElementById('classify-modal-overlay')?.remove();

    const overlay = document.createElement('div');
    overlay.id = 'classify-modal-overlay';
    overlay.className = 'classify-modal-overlay';
    overlay.innerHTML = `
      <div class="classify-modal">
        <div class="classify-modal-header">
          <h3>${title}</h3>
          <button class="btn btn-ghost btn-sm" onclick="ClassifyPage.closeClassifyModal()" style="font-size:16px;">✕</button>
        </div>
        <div class="classify-modal-body">${contentHtml}</div>
        ${onSave ? `
          <div class="classify-modal-footer">
            <button class="btn btn-secondary btn-sm" onclick="ClassifyPage.closeClassifyModal()">Hủy</button>
            <button class="btn btn-primary btn-sm" id="classify-modal-save">💾 Lưu</button>
          </div>
        ` : ''}
      </div>
    `;

    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) closeClassifyModal();
    });

    document.body.appendChild(overlay);

    if (onSave) {
      document.getElementById('classify-modal-save')?.addEventListener('click', onSave);
    }
  }

  function closeClassifyModal() {
    const overlay = document.getElementById('classify-modal-overlay');
    if (overlay) overlay.remove();
  }

  // === Admin Job Operations ===

  function renderAdminJobsMode() {
    if (!isAdmin()) return '<div class="card"><p class="text-muted">Bạn không có quyền truy cập.</p></div>';
    return `
      <div class="card animate-in">
        <div class="card-header">
          <span class="card-title"><span class="icon">📋</span> Vận hành job phân loại</span>
          <button class="btn btn-ghost btn-sm" onclick="ClassifyPage.loadAdminJobs()">🔄</button>
        </div>
        <div id="admin-job-metrics" class="grid-4" style="margin-bottom:16px;"></div>
        <div class="table-wrap" style="max-height:520px;overflow:auto;">
          <table class="table">
            <thead>
              <tr>
                <th>Job</th>
                <th>Người tạo</th>
                <th>File</th>
                <th>Trạng thái</th>
                <th>Queued</th>
                <th>Started</th>
                <th>Completed</th>
                <th>Retry</th>
                <th>Lỗi</th>
                <th>Thao tác</th>
              </tr>
            </thead>
            <tbody id="admin-jobs-tbody">
              <tr><td colspan="10" class="text-muted">Đang tải...</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    `;
  }

  async function loadAdminJobs() {
    if (!isAdmin()) return;
    try {
      const [jobs, metrics] = await Promise.all([
        API.getJobs(),
        API.getJobMetrics()
      ]);
      _adminJobs = Array.isArray(jobs) ? jobs : [];
      _adminJobMetrics = metrics || null;
      renderAdminJobMetrics();
      renderAdminJobsTable();
    } catch (e) {
      Toast.error('Không thể tải danh sách jobs: ' + e.message);
    }
  }

  function renderMetricCard(label, value, hint = '') {
    return `
      <div style="padding:12px;background:var(--bg-tertiary);border:1px solid var(--border);border-radius:8px;">
        <div class="text-muted" style="font-size:11px;">${esc(label)}</div>
        <div style="font-size:22px;font-weight:700;margin-top:4px;">${esc(String(value ?? 0))}</div>
        ${hint ? `<div class="text-muted" style="font-size:11px;margin-top:2px;">${esc(hint)}</div>` : ''}
      </div>
    `;
  }

  function renderAdminJobMetrics() {
    const el = document.getElementById('admin-job-metrics');
    if (!el) return;
    const counts = _adminJobMetrics?.counts || {};
    el.innerHTML = [
      renderMetricCard('Queued', counts.queued || 0, `Retrying: ${counts.retrying || 0}`),
      renderMetricCard('Running', counts.running || 0),
      renderMetricCard('Failed', counts.failed || 0),
      renderMetricCard('Avg wait', `${_adminJobMetrics?.avg_queue_wait_seconds || 0}s`, `Avg run: ${_adminJobMetrics?.avg_processing_seconds || 0}s`),
    ].join('');
  }

  function renderAdminJobsTable() {
    const tbody = document.getElementById('admin-jobs-tbody');
    if (!tbody) return;
    if (!_adminJobs.length) {
      tbody.innerHTML = '<tr><td colspan="10" class="text-muted">Chưa có job phân loại.</td></tr>';
      return;
    }
    tbody.innerHTML = _adminJobs.map(job => {
      const jobId = job.job_id || job.id;
      const error = job.error_summary || job.error || '';
      const actions = [];
      if (job.can_cancel || ['queued', 'running'].includes(job.status)) {
        actions.push(`<button class="btn btn-danger btn-sm" onclick="ClassifyPage.cancelAdminJob('${jobId}')">Hủy</button>`);
      }
      if (job.can_retry || ['error', 'cancelled'].includes(job.status)) {
        actions.push(`<button class="btn btn-secondary btn-sm" onclick="ClassifyPage.retryAdminJob('${jobId}')">Retry</button>`);
      }
      return `
        <tr>
          <td class="text-mono" style="font-size:11px;">${esc(jobId.slice(0, 8))}</td>
          <td>${esc(job.owner_username || '')}</td>
          <td class="wrap" style="max-width:220px;">${esc(job.filename || '')}</td>
          <td>${statusBadge(job)}</td>
          <td style="font-size:12px;">${esc(formatDateTime(job.queued_at || job.created_at))}</td>
          <td style="font-size:12px;">${esc(formatDateTime(job.started_at))}</td>
          <td style="font-size:12px;">${esc(formatDateTime(job.completed_at))}</td>
          <td>${Number(job.retry_count || 0)}</td>
          <td class="wrap" style="max-width:220px;font-size:12px;">${esc(error)}</td>
          <td><div style="display:flex;gap:6px;">${actions.join('') || '<span class="text-muted">—</span>'}</div></td>
        </tr>
      `;
    }).join('');
  }

  async function cancelAdminJob(jobId) {
    try {
      await API.cancelJob(jobId);
      Toast.success('Đã hủy job');
      await loadAdminJobs();
    } catch (e) {
      Toast.error('Không thể hủy job: ' + e.message);
    }
  }

  async function retryAdminJob(jobId) {
    try {
      await API.retryJob(jobId);
      Toast.success('Đã đưa job vào hàng đợi retry');
      await loadAdminJobs();
    } catch (e) {
      Toast.error('Không thể retry job: ' + e.message);
    }
  }

  // === Config Mode (tasks 3-6) ===

  function renderConfigMode() {
    return `
      <div class="config-panel animate-in">
        <!-- System Prompt -->
        <details class="config-section" open>
          <summary class="config-section-header">
            <h4>📝 System Prompt</h4>
            <button class="btn btn-ghost btn-sm" onclick="event.stopPropagation(); ClassifyPage.openPromptEditor()" style="font-size:11px;">✏️ Chỉnh sửa</button>
          </summary>
          <div class="config-section-body" id="config-prompt-body">
            <div class="text-muted" style="font-size:12px;"><span class="spinner" style="width:14px;height:14px;"></span> Đang tải...</div>
          </div>
        </details>

        <!-- Keywords -->
        <details class="config-section">
          <summary class="config-section-header">
            <h4>🔑 Từ khóa phân loại</h4>
            <button class="btn btn-ghost btn-sm" onclick="event.stopPropagation(); ClassifyPage.openKeywordEditor()" style="font-size:11px;">✏️ Chỉnh sửa</button>
          </summary>
          <div class="config-section-body" id="config-keywords-body">
            <div class="text-muted" style="font-size:12px;"><span class="spinner" style="width:14px;height:14px;"></span> Đang tải...</div>
          </div>
        </details>

        <!-- Products -->
        <details class="config-section">
          <summary class="config-section-header">
            <h4>📦 Bảng sản phẩm</h4>
            <button class="btn btn-ghost btn-sm" onclick="event.stopPropagation(); ClassifyPage.openProductEditor()" style="font-size:11px;">✏️ Chỉnh sửa</button>
          </summary>
          <div class="config-section-body" id="config-products-body">
            <div class="text-muted" style="font-size:12px;"><span class="spinner" style="width:14px;height:14px;"></span> Đang tải...</div>
          </div>
        </details>
      </div>
    `;
  }

  async function loadConfigData() {
    const results = await Promise.allSettled([
      _configPrompt ? Promise.resolve(_configPrompt) : API.get('/settings/prompt').catch(() => ({ prompt: '(Không thể tải prompt)' })),
      _configKeywords ? Promise.resolve(_configKeywords) : API.get('/pipeline/keywords/raw').catch(() => ({})),
      _configProducts ? Promise.resolve(_configProducts) : API.get('/pipeline/products/list').catch(() => ({ sheets: {}, sheet_names: [] }))
    ]);

    // Prompt
    if (results[0].status === 'fulfilled') {
      _configPrompt = results[0].value;
      const body = document.getElementById('config-prompt-body');
      if (body) {
        const text = configPromptText() || JSON.stringify(_configPrompt);
        const truncated = text.length > 500 ? text.substring(0, 500) + '...' : text;
        body.innerHTML = `
          <pre style="white-space:pre-wrap;word-break:break-word;font-size:12px;line-height:1.6;color:var(--text-secondary);font-family:var(--font-mono);background:var(--bg-tertiary);padding:12px;border-radius:var(--radius-sm);max-height:200px;overflow-y:auto;">${esc(truncated)}</pre>
          <div style="margin-top:8px;font-size:11px;color:var(--text-muted);">${text.length} ký tự</div>
        `;
      }
    }

    // Keywords
    if (results[1].status === 'fulfilled') {
      _configKeywords = results[1].value;
      const body = document.getElementById('config-keywords-body');
      if (body) {
        const categories = _configKeywords.categories || _configKeywords;
        if (typeof categories === 'object' && !Array.isArray(categories)) {
          const entries = Object.entries(categories);
          let html = `<div style="margin-bottom:10px;"><input type="text" class="form-input" placeholder="🔍 Tìm từ khóa..." style="font-size:12px;padding:6px 10px;" oninput="ClassifyPage.filterKeywords(this.value)"></div>`;
          html += '<div id="config-keywords-grid">';
          entries.forEach(([cat, keywords]) => {
            const kws = Array.isArray(keywords) ? keywords : [];
            html += `
              <div class="config-kw-category" data-category="${esc(cat)}" style="margin-bottom:12px;">
                <div style="font-weight:600;font-size:12px;color:var(--text-primary);margin-bottom:4px;">${esc(cat)} <span class="text-muted" style="font-weight:400;">(${kws.length})</span></div>
                <div class="config-kw-chips">${kws.map(k => `<span class="keyword-chip">${esc(String(k))}</span>`).join('')}</div>
              </div>
            `;
          });
          html += '</div>';
          // Quick add UI
          html += `
            <div style="margin-top:12px;padding-top:12px;border-top:1px solid var(--border);display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
              <input type="text" class="form-input" id="config-kw-new" placeholder="Từ khóa mới" style="font-size:12px;padding:6px 10px;flex:1;min-width:120px;">
              <select class="form-input" id="config-kw-cat" style="font-size:12px;padding:6px 10px;max-width:200px;">
                ${entries.map(([cat]) => `<option value="${esc(cat)}">${esc(cat)}</option>`).join('')}
              </select>
              <button class="btn btn-primary btn-sm" style="font-size:11px;" onclick="ClassifyPage.quickAddKeyword()">➕ Thêm</button>
            </div>
          `;
          body.innerHTML = html;
        } else {
          body.innerHTML = '<div class="text-muted">Không có dữ liệu từ khóa</div>';
        }
      }
    }

    // Products
    if (results[2].status === 'fulfilled') {
      _configProducts = results[2].value;
      const body = document.getElementById('config-products-body');
      if (body) {
        renderProductsPanel(body);
      }
    }
  }

  function normalizeProductSheets() {
    const source = _configProducts?.sheets || _configProducts?.data || [];
    if (Array.isArray(source)) return source;
    if (source && typeof source === 'object') {
      const names = _configProducts?.sheet_names || Object.keys(source);
      return names.map(name => ({
        name,
        columns: source[name]?.columns || [],
        products: source[name]?.products || source[name]?.rows || source[name]?.data || []
      }));
    }
    return [];
  }

  function renderProductsPanel(body) {
    const sheets = normalizeProductSheets();
    if (!Array.isArray(sheets) || sheets.length === 0) {
      // Try treating as single table
      if (_configProducts?.rows || _configProducts?.products) {
        const rows = _configProducts.rows || _configProducts.products || [];
        body.innerHTML = renderProductTable(rows);
        return;
      }
      body.innerHTML = '<div class="text-muted">Không có dữ liệu sản phẩm</div>';
      return;
    }

    let html = '<div class="config-sheet-tabs">';
    sheets.forEach((sheet, i) => {
      const name = sheet.name || sheet.sheet_name || `Sheet ${i + 1}`;
      html += `<button class="config-sheet-tab ${i === _configSheetTab ? 'active' : ''}" onclick="ClassifyPage.switchConfigSheet(${i})">${esc(name)}</button>`;
    });
    html += '</div>';
    html += `
      <div style="margin-bottom:10px;display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
        <input type="text" class="form-input" placeholder="🔍 Tìm sản phẩm..." style="font-size:12px;padding:6px 10px;flex:1;min-width:220px;" oninput="ClassifyPage.filterProducts(this.value)">
      </div>
    `;
    html += '<div id="config-products-table">';

    const activeSheet = sheets[_configSheetTab] || sheets[0];
    const rows = activeSheet?.rows || activeSheet?.data || activeSheet?.products || [];
    html += renderProductTable(rows);
    html += '</div>';
    body.innerHTML = html;
  }

  function renderProductTable(rows) {
    if (!Array.isArray(rows) || rows.length === 0) return '<div class="text-muted">Không có dữ liệu</div>';
    const headers = Object.keys(rows[0]);
    let html = '<div style="overflow-x:auto;max-height:300px;overflow-y:auto;"><table class="config-product-table"><thead><tr>';
    headers.forEach(h => { html += `<th>${esc(String(h))}</th>`; });
    html += '</tr></thead><tbody id="config-product-tbody">';
    rows.slice(0, 50).forEach(row => {
      html += '<tr>';
      headers.forEach(h => { html += `<td>${esc(String(row[h] ?? ''))}</td>`; });
      html += '</tr>';
    });
    html += '</tbody></table></div>';
    if (rows.length > 50) {
      html += `<div class="text-muted" style="font-size:11px;margin-top:6px;">Hiển thị 50/${rows.length} dòng</div>`;
    }
    return html;
  }

  function switchConfigSheet(index) {
    _configSheetTab = index;
    const body = document.getElementById('config-products-body');
    if (body) renderProductsPanel(body);
  }

  function activeProductSheet() {
    const sheets = normalizeProductSheets();
    const sheet = sheets[_configSheetTab] || sheets[0] || null;
    if (sheet) return sheet;
    const rows = _configProducts?.rows || _configProducts?.products || [];
    return {
      name: _configProducts?.sheet_name || 'Sản phẩm',
      columns: rows[0] ? Object.keys(rows[0]) : ['Sản phẩm', 'Dòng SP', 'Model'],
      products: rows
    };
  }

  function renderProductEditorRow(row = {}) {
    const cells = _productEditorColumns.map(col => `
      <td contenteditable="true" data-product-field="${escAttr(col)}" style="min-width:140px;">${esc(row[col] ?? '')}</td>
    `).join('');
    return `
      <tr>
        ${cells}
        <td style="width:70px;">
          <button class="btn btn-ghost btn-sm" onclick="ClassifyPage.deleteProductEditorRow(this)" title="Xóa dòng">🗑️</button>
        </td>
      </tr>
    `;
  }

  function openProductEditor() {
    if (!isAdmin()) {
      Toast.error('Bạn không có quyền chỉnh sửa bảng sản phẩm');
      return;
    }
    if (window.SettingsPage?.openProductAssetEditor) {
      SettingsPage.openProductAssetEditor();
      return;
    }
    const sheet = activeProductSheet();
    const rows = sheet?.products || sheet?.rows || sheet?.data || [];
    _productEditorSheetName = sheet?.name || sheet?.sheet_name || 'Sản phẩm';
    _productEditorColumns = Array.isArray(sheet?.columns) && sheet.columns.length
      ? sheet.columns.map(String)
      : (rows[0] ? Object.keys(rows[0]) : ['Sản phẩm', 'Dòng SP', 'Model']);

    const table = `
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:12px;">
        <div class="text-muted" style="font-size:12px;">Sheet: <strong>${esc(_productEditorSheetName)}</strong></div>
        <button class="btn btn-secondary btn-sm" onclick="ClassifyPage.addProductEditorRow()">➕ Thêm dòng</button>
      </div>
      <div style="overflow:auto;max-height:52vh;border:1px solid var(--border);border-radius:var(--radius-sm);">
        <table class="config-product-table">
          <thead>
            <tr>
              ${_productEditorColumns.map(col => `<th>${esc(col)}</th>`).join('')}
              <th>Hành động</th>
            </tr>
          </thead>
          <tbody id="config-product-editor-tbody">
            ${(Array.isArray(rows) && rows.length ? rows : [{}]).map(row => renderProductEditorRow(row)).join('')}
          </tbody>
        </table>
      </div>
      <p class="text-muted" style="font-size:11px;margin-top:10px;">Nhấp trực tiếp vào ô để sửa. Dữ liệu sẽ lưu vào sheet hiện tại và giữ nguyên các sheet khác.</p>
    `;

    openClassifyModal(`✏️ Sửa danh mục sản phẩm`, table, saveProductEditor);
  }

  function addProductEditorRow() {
    const tbody = document.getElementById('config-product-editor-tbody');
    if (!tbody) return;
    const temp = document.createElement('tbody');
    temp.innerHTML = renderProductEditorRow({});
    tbody.appendChild(temp.firstElementChild);
  }

  function deleteProductEditorRow(button) {
    const row = button?.closest?.('tr');
    const tbody = document.getElementById('config-product-editor-tbody');
    if (!row || !tbody) return;
    if (tbody.querySelectorAll('tr').length <= 1) {
      row.querySelectorAll('[data-product-field]').forEach(cell => { cell.textContent = ''; });
      return;
    }
    row.remove();
  }

  async function saveProductEditor() {
    if (!_productEditorSheetName || _productEditorColumns.length === 0) return;
    const rows = Array.from(document.querySelectorAll('#config-product-editor-tbody tr')).map(tr => {
      const row = {};
      tr.querySelectorAll('[data-product-field]').forEach(cell => {
        row[cell.getAttribute('data-product-field')] = cell.textContent.trim();
      });
      return row;
    }).filter(row => Object.values(row).some(value => String(value).trim() !== ''));

    try {
      await API.put('/pipeline/products', {
        sheet_name: _productEditorSheetName,
        products: rows
      });
      Toast.success(`Đã lưu bảng sản phẩm "${_productEditorSheetName}"`);
      _configProducts = null;
      closeClassifyModal();
      if (_mode === 'config') renderMode();
    } catch (e) {
      Toast.error('Lỗi lưu bảng sản phẩm: ' + e.message);
    }
  }

  // === Prompt Editor (task 4.4) ===

  async function openPromptEditor() {
    if (window.SettingsPage?.openPromptAssetEditor) {
      SettingsPage.openPromptAssetEditor();
      return;
    }
    if (!_configPrompt) {
      try {
        _configPrompt = await API.get('/settings/prompt');
      } catch (e) {
        Toast.error('Không thể tải System Prompt: ' + e.message);
        return;
      }
    }
    const text = configPromptText();
    openClassifyModal('✏️ Chỉnh sửa System Prompt', `
      <textarea id="config-prompt-textarea" class="form-input" style="width:100%;min-height:350px;font-family:var(--font-mono);font-size:12px;line-height:1.6;resize:vertical;">${esc(text)}</textarea>
    `, async () => {
      const newText = document.getElementById('config-prompt-textarea')?.value;
      if (!newText) return;
      try {
        await API.put('/settings/prompt', { prompt: newText });
        _configPrompt = null; // clear cache
        Toast.success('System prompt đã được lưu');
        closeClassifyModal();
        if (_mode === 'config') { renderMode(); }
      } catch (e) {
        Toast.error('Lỗi lưu prompt: ' + e.message);
      }
    });
  }

  function openKeywordEditor() {
    if (!isAdmin()) {
      Toast.error('Bạn không có quyền chỉnh sửa từ khóa');
      return;
    }
    if (window.SettingsPage?.openKeywordAssetEditor) {
      SettingsPage.openKeywordAssetEditor();
      return;
    }
    Toast.error('Không thể mở editor từ khóa');
  }

  // === Keyword filter + quick add (tasks 5.1-5.3) ===

  function filterKeywords(query) {
    const q = query.trim().toLowerCase();
    document.querySelectorAll('.config-kw-category').forEach(cat => {
      const chips = cat.querySelectorAll('.keyword-chip');
      let hasMatch = false;
      chips.forEach(chip => {
        const text = chip.textContent.toLowerCase();
        if (!q || text.includes(q)) {
          chip.style.display = '';
          chip.classList.toggle('highlight', !!q && text.includes(q));
          hasMatch = true;
        } else {
          chip.style.display = 'none';
          chip.classList.remove('highlight');
        }
      });
      cat.style.display = hasMatch || !q ? '' : 'none';
    });
  }

  async function quickAddKeyword() {
    const input = document.getElementById('config-kw-new');
    const select = document.getElementById('config-kw-cat');
    if (!input || !select) return;
    const keyword = input.value.trim();
    const category = select.value;
    if (!keyword) { Toast.error('Nhập từ khóa'); return; }

    // Add to cached data
    const categories = _configKeywords?.categories || _configKeywords || {};
    if (!categories[category]) categories[category] = [];
    if (categories[category].some(k => String(k).trim().toLowerCase() === keyword.toLowerCase())) {
      Toast.error('Từ khóa đã tồn tại trong nhóm này');
      return;
    }
    categories[category].push(keyword);

    try {
      await API.put('/pipeline/keywords', categories);
      _configKeywords = null; // clear cache
      input.value = '';
      Toast.success(`Đã thêm "${keyword}" vào ${category}`);
      if (_mode === 'config') { renderMode(); }
    } catch (e) {
      Toast.error('Lỗi lưu từ khóa: ' + e.message);
    }
  }

  // === Product filter (tasks 6.1-6.2) ===

  function filterProducts(query) {
    const q = query.trim().toLowerCase();
    const tbody = document.getElementById('config-product-tbody');
    if (!tbody) return;
    tbody.querySelectorAll('tr').forEach(tr => {
      const text = tr.textContent.toLowerCase();
      const match = !q || text.includes(q);
      tr.style.display = match ? '' : 'none';
      tr.classList.toggle('highlight', !!q && match);
    });
  }

  function reset() {
    _currentJob = null;
    _selectedFile = null;
    _batchFiles = [];
    _isPaused = false;
    if (_wsClient) { _wsClient.close(); _wsClient = null; }
    // Mark reset so page refresh won't auto-restore terminal jobs
    sessionStorage.setItem('classify_reset', '1');
    sessionStorage.removeItem('classify_active_job_id');
  }

  return {
    render, destroy, setMode, reset,
    updateCharCount, clearInput, classifyText,
    fileDragOver, fileDragLeave, fileDrop, handleFile, clearFile,
    startFileClassify, togglePause, stopClassify, downloadResult, resetJob,
    batchDrop, handleBatchFiles, startBatch, clearBatch, removeBatchFile,
    pushToSharePoint,
    loadAdminJobs, cancelAdminJob, retryAdminJob,
    previewTemplate, downloadTemplate, downloadJob, openPromptEditor, openKeywordEditor, quickAddKeyword,
    filterKeywords, filterProducts, switchConfigSheet,
    openProductEditor, addProductEditorRow, deleteProductEditorRow, saveProductEditor,
    openClassifyModal, closeClassifyModal
  };
})();
