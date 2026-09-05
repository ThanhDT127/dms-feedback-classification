/* ============================================================
   File Manager Page — browse, upload, preview files
   ============================================================ */

window.FilesPage = (() => {
  // Checkpoint and Model tabs are hidden from UI (synced by Watcher backend only)
  const FOLDERS = [
    { id: 'input',   label: 'Đầu vào', icon: '📥' },
    { id: 'output',  label: 'Kết quả',  icon: '📤' },
    { id: 'keyword', label: 'Từ khóa', icon: '🔑' },
  ];

  let _activeFolder = 'input';
  let _files = [];
  let _previewFile = null;
  let _refreshInterval = null;
  let _lastFilesHash = '';

  // Interaction-aware refresh state (tasks 2.1-2.6)
  let _isUserInteracting = false;
  let _pendingData = null;
  let _scrollIdleTimer = null;
  let _tableWrapEl = null;

  // Enhanced file management state
  let _expandedRows = new Set();
  let _selectedFiles = new Set();
  let _metadataCache = new Map();
  let _dragCounter = 0;

  // Event handler references for cleanup
  let _onMouseEnter = null;
  let _onMouseLeave = null;
  let _onScroll = null;

  // Search / filter / sort state (file-manager-ui-overhaul)
  let _searchQuery = '';
  let _statusFilter = 'all';
  let _sortCol = 'modified';
  let _sortDir = 'desc';
  let _allFiles = [];      // master list from API (post-keyword-filter)
  let _lastMetrics = null; // cached metrics for output folder count display

  function isAdminRole() {
    return window.App?.state?.user?.role === 'admin';
  }

  async function loadConfigAssetSyncHealth() {
    const el = document.getElementById('config-asset-sync-health');
    if (!el) return;
    try {
      const data = await API.getHealth({ silent: true });
      const asset = data.config_assets;
      if (!asset) {
        el.textContent = 'Web UI đang chạy không có trạng thái watcher/config asset; bấm Làm mới hoặc kiểm tra service watcher nếu cần tự phát hiện thay đổi Keyword/Model trên SharePoint.';
        return;
      }
      const status = asset.status || asset.state || 'không rõ';
      const last = asset.last_sync || asset.last_checked || asset.updated_at || '';
      el.textContent = last
        ? `Config asset sync: ${status} · lần kiểm tra cuối ${last}`
        : `Config asset sync: ${status}`;
    } catch (e) {
      el.textContent = 'Không đọc được trạng thái watcher/config asset. Đồng bộ thủ công vẫn dùng nút Đồng bộ SharePoint.';
    }
  }

  function downloadTemplate() {
    return API.download('/files/template', 'template_dms.xlsx');
  }

  function downloadFile(filename) {
    if (!filename) return;
    return API.download(
      `/files/${_activeFolder}/${encodeURIComponent(filename)}/download`,
      filename
    );
  }

  async function ingestInputFile(filename) {
    if (!isAdminRole() || _activeFolder !== 'input' || !filename) return;
    try {
      const result = await API.ingestInputFile(filename);
      Toast.success(result.message || `Đã đưa ${result.ingested_rows || 0} dòng vào phân tích`);
    } catch (e) {
      Toast.error('Không thể đưa file vào phân tích: ' + e.message);
    }
  }

  function isEditableConfigAsset(filename) {
    const lower = String(filename || '').toLowerCase();
    return lower === 'kw_map.json'
      || lower.includes('hệ từ khóa lọc 3 lần')
      || lower.includes('he tu khoa loc 3 lan')
      || lower.includes('phân chia nhóm sản phẩm v2')
      || lower.includes('phan chia nhom san pham v2');
  }

  function editKeywordAsset(filename) {
    if (!isAdminRole()) {
      Toast.error('Bạn không có quyền chỉnh sửa cấu hình');
      return;
    }
    const lower = String(filename || '').toLowerCase();
    if (lower === 'kw_map.json' || lower.includes('hệ từ khóa') || lower.includes('he tu khoa')) {
      SettingsPage.openKeywordAssetEditor();
      return;
    }
    if (lower.includes('phân chia nhóm sản phẩm v2') || lower.includes('phan chia nhom san pham v2')) {
      SettingsPage.openProductAssetEditor();
      return;
    }
    Toast.info('Không hỗ trợ chỉnh sửa trực tiếp file này. Vui lòng tải xuống để kiểm tra hoặc dùng file kw_map.json / Phân Chia Nhóm Sản Phẩm V2.xlsx.');
  }

  function selectedFileItems() {
    return [..._selectedFiles].map(name => {
      const file = _files.find(f => (f.name || f.filename) === name) || {};
      return { name, id: file.id || null, source: file.source || (file.web_url ? 'sharepoint' : 'local_cache') };
    });
  }

  function render() {
    const app = document.getElementById('app');
    const isAdmin = isAdminRole();
    app.innerHTML = `
      <div class="page-header">
        <h2>📂 Quản lý file</h2>
        <p>Duyệt và xem trước file trong các thư mục hệ thống và SharePoint Cloud</p>
      </div>

      <!-- Tabs -->
      <div class="tabs" id="file-tabs">
        ${FOLDERS.map(f => `
          <div class="tab-item ${f.id === _activeFolder ? 'active' : ''}"
               data-folder="${f.id}"
               onclick="FilesPage.switchFolder('${f.id}')">
            ${f.icon} ${f.label}
          </div>
        `).join('')}
      </div>

      <!-- File Control Bar: search, status filter (file-manager-ui-overhaul) -->
      <div class="file-control-bar" id="file-control-bar">
        <div class="fcb-search">
          <input type="text" id="fcb-search-input" class="fcb-input"
            placeholder="🔍 Tìm tên file (hỗ trợ tiếng Việt không dấu)..."
            oninput="FilesPage.onSearch(this.value)">
        </div>
        <div class="fcb-filter">
          <select id="fcb-status-filter" class="fcb-select" onchange="FilesPage.onFilterStatus(this.value)">
            <option value="all">Tất cả trạng thái</option>
            <option value="new">🆕 File mới</option>
            <option value="processing">🔄 Đang xử lý</option>
            <option value="done">✅ Hoàn thành</option>
            <option value="failed">❌ Thất bại</option>
          </select>
        </div>
      </div>

      <div class="sync-help-card" id="file-sync-help" style="margin:0 0 16px;padding:12px 14px;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--bg-secondary);font-size:12px;color:var(--text-secondary);">
        <strong style="color:var(--accent-blue);">☁ Đồng bộ SharePoint:</strong>
        Tải Input từ SharePoint về local/cache và đẩy Output lên SharePoint. Hệ thống không tự đồng bộ thao tác xóa; xóa local/cache và Xóa trên SharePoint là hai hành động riêng.
        <span id="config-asset-sync-health" style="display:block;margin-top:6px;color:var(--text-muted);">Đang kiểm tra trạng thái đồng bộ cấu hình...</span>
      </div>

      <!-- Toolbar -->
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;flex-wrap:wrap;gap:8px;">
        <div style="display:flex;align-items:center;gap:8px;">
          <span id="file-count" class="text-secondary" style="font-size:13px;"></span>
        </div>
        <div class="btn-group" style="display:flex;gap:8px;align-items:center;">
          <input type="file" id="local-file-upload-input" style="display:none;" accept=".xlsx" multiple onchange="FilesPage.handleUpload(this)">
          ${isAdmin ? '<button id="btn-upload-file" class="btn btn-primary btn-sm" onclick="document.getElementById(\'local-file-upload-input\').click()">📤 Tải file lên</button>' : ''}
          ${isAdmin ? '<button id="btn-sync-sharepoint" class="btn btn-secondary btn-sm" onclick="FilesPage.syncSharePoint()">☁️ Đồng bộ SharePoint</button>' : ''}
          <button class="btn btn-secondary btn-sm" onclick="FilesPage.refresh()">🔄 Làm mới</button>
        </div>
      </div>

      <!-- Upload format warning & template link -->
      <div id="upload-hint-container" style="margin-top:-8px; margin-bottom:16px; font-size:13px; display:none; flex-direction:column; gap:8px; background:rgba(245,158,11,0.05); border:1px solid rgba(245,158,11,0.2); border-radius:var(--radius-md); padding:12px; width:100%;">
        <div style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px;">
          <span style="color:var(--accent-amber); font-weight:500; display:flex; align-items:center; gap:6px;">
            ⚠️ Định dạng Excel yêu cầu: Cột văn bản bắt buộc phải chứa chữ "Nội dung" hoặc "noi dung".
          </span>
          <div style="display:flex; gap:8px;">
            <button class="btn btn-secondary btn-sm" style="padding:4px 10px; font-size:12px;" onclick="FilesPage.previewTemplate()">👁️ Xem cấu trúc mẫu</button>
            <button class="btn btn-ghost btn-sm" style="font-size:12px; padding:4px 10px; color:var(--text-secondary);" onclick="FilesPage.downloadTemplate()"><span style="text-decoration:underline;">Tải file mẫu (.xlsx)</span></button>
          </div>
        </div>
        <p style="font-size:12px; color:var(--text-muted); margin:0; line-height:1.5;">
          Hệ thống sẽ quét cột này để phân loại. Các cột thông tin đi kèm (như Tên, Ngày, Mã phản hồi...) sẽ được tự động giữ nguyên và đi kèm trong kết quả phân loại xuất ra.
        </p>
      </div>

      <!-- New Data Banner (task 3.1-3.5) -->
      <div id="new-data-banner" class="hidden" style="margin-bottom:12px; padding:10px 16px; background:rgba(59,130,246,0.1); border:1px solid rgba(59,130,246,0.3); border-radius:var(--radius-md); display:flex; align-items:center; justify-content:space-between; cursor:pointer; transition:opacity 0.3s;" onclick="FilesPage.applyPendingData()">
        <span style="color:var(--accent-blue); font-size:13px; font-weight:500; display:flex; align-items:center; gap:6px;">
          🔔 Có dữ liệu mới — Nhấn để cập nhật
        </span>
        <span style="color:var(--text-muted); font-size:11px;">Bảng chưa được cập nhật vì bạn đang tương tác</span>
      </div>

      <!-- File Table -->
      <div class="card" style="padding:0;overflow:hidden;">
        ${isAdmin ? `<div class="bulk-toolbar" id="bulk-toolbar" data-layout="bulk-toolbar-inline">
          <div style="display:flex;align-items:center;gap:12px;">
            <span style="font-size:13px;font-weight:500;">Đã chọn <span id="bulk-count">0</span> file</span>
          </div>
          <div style="display:flex;gap:8px;">
            <button class="btn btn-secondary btn-sm" onclick="FilesPage.selectAllFiles()">☑️ Chọn tất cả</button>
            <button class="btn btn-danger btn-sm" onclick="FilesPage.bulkDelete()">🗑️ Xóa local/cache</button>
            <button class="btn btn-secondary btn-sm" onclick="FilesPage.bulkDeleteSharePoint()">☁️ Xóa trên SharePoint</button>
            <button class="btn btn-ghost btn-sm" onclick="FilesPage.clearSelection()">✕ Bỏ chọn</button>
          </div>
        </div>` : ''}
        <div class="table-wrap" id="file-table-wrap" style="max-height:450px;overflow-y:auto;overflow-x:auto;">
          <table class="table" id="file-table">
            <thead>
              <tr>
                <th style="width:40px;">#</th>
                <th>Tên file</th>
                <th>Kích thước</th>
                <th>Ngày sửa đổi</th>
                <th>Trạng thái</th>
                <th style="width:100px;">Hành động</th>
              </tr>
            </thead>
            <tbody id="file-tbody">
              <tr><td colspan="8"><div class="text-center text-muted" style="padding:30px;">Đang tải...</div></td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Drop Zone Overlay -->
      <div class="file-drop-zone" id="file-drop-zone">
        <div class="drop-icon">📥</div>
        <div>Thả file .xlsx vào đây</div>
      </div>

      <!-- Preview Panel (modal overlay) -->
      <div id="preview-panel" class="hidden" onclick="FilesPage._onBackdropClick(event)">
        <div class="preview-panel">
          <div class="preview-header">
            <span id="preview-filename">📄 Preview</span>
            <button class="btn btn-ghost btn-sm" onclick="FilesPage.closePreview()">✕ Đóng</button>
          </div>
          <div class="preview-body" id="preview-body"></div>
        </div>
      </div>

      <!-- Cloud-centric workflow instructions and status legend -->
      <div class="card mt-6 animate-in animate-in-delay-4" style="background:rgba(255,255,255,0.02);border:1px solid var(--border);padding:20px;">
        <div style="font-size:14px;font-weight:600;margin-bottom:12px;color:var(--accent-blue);display:flex;align-items:center;gap:6px;">
          <span>💡 Luồng hoạt động dữ liệu & Chú thích trạng thái</span>
        </div>
        <div style="font-size:12px;line-height:1.6;color:var(--text-secondary);display:flex;flex-direction:column;gap:12px;">
          <div>
            <strong style="color:var(--text-primary);">☁️ Chế độ Tự động hóa đồng bộ Cloud (SharePoint-centric):</strong>
            <p style="margin:4px 0 0 0;color:var(--text-muted);">Hệ thống hoạt động theo mô hình Cloud-first. Admin có thể tải file Excel vào Input để xử lý thủ công; watcher vẫn đồng bộ và đẩy kết quả lên thư mục <span style="color:var(--accent-green);font-weight:500;">Output</span> trên SharePoint Cloud.</p>
          </div>
          <div style="border-top:1px solid var(--border);padding-top:12px;">
            <strong style="color:var(--text-primary);display:block;margin-bottom:8px;">📌 Chú thích trạng thái file:</strong>
            <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(200px, 1fr));gap:8px;">
              <div style="display:flex;align-items:center;gap:6px;">
                <span class="badge badge-amber" style="width:85px;text-align:center;display:inline-block;">🆕 File mới</span>
                <span style="color:var(--text-muted);">File vừa tải lên Cloud, chờ Watcher quét.</span>
              </div>
              <div style="display:flex;align-items:center;gap:6px;">
                <span class="badge badge-blue" style="width:85px;text-align:center;display:inline-block;">🔄 Đang xử lý</span>
                <span style="color:var(--text-muted);">Watcher đang phân loại nội dung file.</span>
              </div>
              <div style="display:flex;align-items:center;gap:6px;">
                <span class="badge badge-green" style="width:85px;text-align:center;display:inline-block;">✅ Đã xử lý</span>
                <span style="color:var(--text-muted);">Hoàn thành phân loại & đẩy lên Cloud.</span>
              </div>
              <div style="display:flex;align-items:center;gap:6px;">
                <span class="badge badge-red" style="width:85px;text-align:center;display:inline-block;">❌ Thất bại</span>
                <span style="color:var(--text-muted);">Gặp lỗi nghiêm trọng (Format file sai...).</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    `;

    loadFiles();
    loadConfigAssetSyncHealth();
    startRefresh();
    _bindInteractionTracking();
    _initDragDrop();
  }

  // === Interaction tracking (tasks 2.1-2.6) ===

  function _bindInteractionTracking() {
    _tableWrapEl = document.getElementById('file-table-wrap');
    if (!_tableWrapEl) return;

    _onMouseEnter = () => { _isUserInteracting = true; };
    _onMouseLeave = () => {
      _isUserInteracting = false;
      // Apply pending data when user leaves table area
      if (_pendingData) {
        applyPendingData();
      }
    };
    _onScroll = () => {
      _isUserInteracting = true;
      // Debounce: clear interaction flag after 500ms of no scrolling
      if (_scrollIdleTimer) clearTimeout(_scrollIdleTimer);
      _scrollIdleTimer = setTimeout(() => {
        _isUserInteracting = false;
        if (_pendingData) {
          applyPendingData();
        }
      }, 500);
    };

    _tableWrapEl.addEventListener('mouseenter', _onMouseEnter);
    _tableWrapEl.addEventListener('mouseleave', _onMouseLeave);
    _tableWrapEl.addEventListener('scroll', _onScroll, { passive: true });
  }

  function _unbindInteractionTracking() {
    if (_tableWrapEl) {
      if (_onMouseEnter) _tableWrapEl.removeEventListener('mouseenter', _onMouseEnter);
      if (_onMouseLeave) _tableWrapEl.removeEventListener('mouseleave', _onMouseLeave);
      if (_onScroll) _tableWrapEl.removeEventListener('scroll', _onScroll);
    }
    if (_scrollIdleTimer) {
      clearTimeout(_scrollIdleTimer);
      _scrollIdleTimer = null;
    }
    _tableWrapEl = null;
    _onMouseEnter = null;
    _onMouseLeave = null;
    _onScroll = null;
  }

  // === New data banner (tasks 3.1-3.5) ===

  function _showBanner() {
    const banner = document.getElementById('new-data-banner');
    if (banner) banner.classList.remove('hidden');
  }

  function _hideBanner() {
    const banner = document.getElementById('new-data-banner');
    if (banner) banner.classList.add('hidden');
  }

  function applyPendingData() {
    if (!_pendingData) return;
    const { newFiles, metrics } = _pendingData;
    _pendingData = null;
    _hideBanner();

    _allFiles = newFiles;
    if (metrics) _lastMetrics = metrics;
    _applyFilters(true);
  }

  // === Row rendering helper (task 1.3) ===

  function buildRowHTML(file, index, isInput, silent) {
    const name = file.name || file.filename || 'unknown';
    const size = formatSize(file.size || 0);
    const date = formatDate(file.modified || file.date);
    const status = getStatusBadge(file.status);
    const webUrl = file.web_url || '';
    const source = file.source || (webUrl ? 'sharepoint' : 'local_cache');
    const itemId = file.id || '';
    const canEditAsset = isAdminRole() && _activeFolder === 'keyword' && isEditableConfigAsset(name);

    const cloudBtn = webUrl
      ? `<a href="${escAttr(webUrl)}" target="_blank" class="btn btn-ghost btn-sm" title="Xem trên SharePoint" style="text-decoration:none;">☁️</a>`
      : '';
    const downloadBtn = `<button class="btn btn-ghost btn-sm" title="Tải về" onclick="FilesPage.downloadFile('${escAttr(name)}')">📥</button>`;
    const editBtn = canEditAsset
      ? `<button class="btn btn-ghost btn-sm" title="Chỉnh sửa cấu hình" onclick="FilesPage.editKeywordAsset('${escAttr(name)}')">✏️</button>`
      : '';
    const ingestBtn = isAdminRole() && _activeFolder === 'input' && source === 'local_cache'
      ? `<button class="btn btn-ghost btn-sm" title="Đưa vào phân tích" aria-label="Đưa vào phân tích" onclick="FilesPage.ingestInputFile('${escAttr(name)}')">📊</button>`
      : '';

    const isSelected = _selectedFiles.has(name);
    const isExpanded = _expandedRows.has(name);
    const rowClass = [silent ? '' : 'animate-in', isSelected ? 'selected' : ''].filter(Boolean).join(' ');
    const animStyle = silent ? '' : `style="animation-delay:${index * 30}ms"`;

    return `
      <tr class="${rowClass}" ${animStyle} data-filename="${escAttr(name)}" data-file-id="${escAttr(itemId)}" data-source="${escAttr(source)}">
        ${isAdminRole() ? `<td><input type="checkbox" class="file-checkbox" ${isSelected ? 'checked' : ''} onchange="FilesPage.toggleFileSelection('${escAttr(name)}')"></td>` : ''}
        <td><span class="expand-icon ${isExpanded ? 'expanded' : ''}" onclick="FilesPage.toggleRowDetail('${escAttr(name)}')">▶</span></td>
        <td class="text-muted">${index + 1}</td>
        <td>
          <div style="display:flex;flex-direction:column;gap:3px;">
            <span style="cursor:pointer;color:var(--accent-blue);" onclick="FilesPage.preview('${escAttr(name)}')">
              ${escHtml(name)}
            </span>
            <span class="badge-cache ${source === 'local_cache' ? 'badge-cache-local' : 'badge-cache-cloud'}">
              ${source === 'local_cache' ? '💾 Đã lưu cache' : '☁️ Chỉ trên Cloud'}
            </span>
          </div>
        </td>
        <td class="text-muted text-mono" style="font-size:12px;">${size}</td>
        <td class="text-muted" style="font-size:12px;">${escHtml(date)}</td>
        ${isInput ? `<td>${status}</td>` : ''}
        <td>
          <div style="display:flex;gap:4px;align-items:center;">
            <button class="btn btn-ghost btn-sm" title="Xem" onclick="FilesPage.preview('${escAttr(name)}')">👁️</button>
            ${editBtn}
            ${ingestBtn}
            ${downloadBtn}
            ${cloudBtn}
          </div>
        </td>
      </tr>
    `;
  }

  // === DOM diffing (tasks 1.1-1.2, 4.1-4.2) ===

  function diffFileRows(tbody, newFiles, isInput, silent) {
    const tableWrap = document.getElementById('file-table-wrap');
    // Save scroll position (task 4.1)
    const savedScrollTop = tableWrap ? tableWrap.scrollTop : 0;

    // Build map of new files by name
    const newFileMap = new Map();
    newFiles.forEach((f, i) => {
      const name = f.name || f.filename || 'unknown';
      newFileMap.set(name, { file: f, index: i });
    });

    // Build map of existing rows by data-filename
    const existingRows = tbody.querySelectorAll('tr[data-filename]');
    const existingMap = new Map();
    existingRows.forEach(row => {
      existingMap.set(row.getAttribute('data-filename'), row);
    });

    // If existing tbody has no data-filename rows (first render or empty), do full innerHTML
    if (existingRows.length === 0) {
      tbody.innerHTML = newFiles.map((f, i) => buildRowHTML(f, i, isInput, silent)).join('');
      if (tableWrap) tableWrap.scrollTop = savedScrollTop;
      return;
    }

    // Remove rows that no longer exist
    existingRows.forEach(row => {
      const fname = row.getAttribute('data-filename');
      if (!newFileMap.has(fname)) {
        row.remove();
      }
    });

    // Update existing rows or add new ones
    let prevRow = null;
    newFiles.forEach((f, i) => {
      const name = f.name || f.filename || 'unknown';
      const existingRow = existingMap.get(name);
      const newRowHTML = buildRowHTML(f, i, isInput, true); // always silent for diff updates

      if (existingRow) {
        // Update row content if changed (compare innerHTML of cells)
        const tempDiv = document.createElement('tbody');
        tempDiv.innerHTML = newRowHTML;
        const newRow = tempDiv.firstElementChild;

        // Compare inner content (skip animation attributes)
        if (existingRow.innerHTML !== newRow.innerHTML) {
          existingRow.innerHTML = newRow.innerHTML;
        }
        // Update index number — find the # cell robustly regardless of admin/non-admin layout.
        // Admin: [checkbox][expand][#][name]...  Non-admin: [expand][#][name]...
        // The # cell is always a plain <td> containing only a number, so search all tds.
        const allTds = existingRow.querySelectorAll('td');
        for (const td of allTds) {
          if (/^\d+$/.test(td.textContent.trim()) && !td.querySelector('input,button,span')) {
            if (td.textContent.trim() !== String(i + 1)) td.textContent = i + 1;
            break;
          }
        }
        // Ensure data-filename is set
        existingRow.setAttribute('data-filename', name);
        prevRow = existingRow;
      } else {
        // Insert new row at correct position
        const tempDiv = document.createElement('tbody');
        tempDiv.innerHTML = newRowHTML;
        const newRow = tempDiv.firstElementChild;
        if (prevRow && prevRow.nextSibling) {
          tbody.insertBefore(newRow, prevRow.nextSibling);
        } else if (!prevRow) {
          tbody.insertBefore(newRow, tbody.firstChild);
        } else {
          tbody.appendChild(newRow);
        }
        prevRow = newRow;
      }
    });

    // Reorder rows in the DOM to match newFiles order (critical for sort to take effect).
    // We append each row in newFiles order; the browser moves them if already in tbody.
    newFiles.forEach((f) => {
      const name = f.name || f.filename || 'unknown';
      // Find the row in the current DOM (may have been updated above)
      const existingRow = tbody.querySelector(`tr[data-filename="${CSS.escape(name)}"]`);
      if (existingRow) tbody.appendChild(existingRow);
    });

    // Restore scroll position (task 4.2)
    if (tableWrap) tableWrap.scrollTop = savedScrollTop;
  }

  // === File count helper ===

  function _updateFileCount(metrics) {
    // Store metrics for output folder count; actual rendering is done in _applyFilters
    if (metrics) _lastMetrics = metrics;
  }

  function switchFolder(folder) {
    _activeFolder = folder;
    _previewFile = null;
    // Clear pending data and hide banner when switching folders (task 6.1)
    _pendingData = null;
    _hideBanner();
    // Clear selection and metadata cache when switching folders
    _selectedFiles.clear();
    _expandedRows.clear();
    _metadataCache.clear();
    // Reset search / filter / sort state when switching tabs
    _searchQuery = '';
    _statusFilter = 'all';
    _sortCol = 'modified';
    _sortDir = 'desc';
    _allFiles = [];
    _lastMetrics = null;
    const searchInput = document.getElementById('fcb-search-input');
    if (searchInput) searchInput.value = '';
    const statusSelect = document.getElementById('fcb-status-filter');
    if (statusSelect) statusSelect.value = 'all';

    document.querySelectorAll('#file-tabs .tab-item').forEach(t => {
      t.classList.toggle('active', t.dataset.folder === folder);
    });

    document.getElementById('preview-panel')?.classList.add('hidden');
    loadFiles();
    startRefresh();
    // Re-bind interaction tracking for the table
    _unbindInteractionTracking();
    _bindInteractionTracking();
  }

  async function loadFiles(silent = false) {
    const tbody = document.getElementById('file-tbody');
    const countEl = document.getElementById('file-count');
    if (!tbody) return;

    const isInput = _activeFolder === 'input';
    const isAdmin = isAdminRole();
    const colSpan = isInput ? (isAdmin ? 8 : 7) : (isAdmin ? 7 : 6);

    const uploadBtn = document.getElementById('btn-upload-file');
    if (uploadBtn) {
      uploadBtn.style.display = isInput ? 'inline-block' : 'none';
    }

    const uploadHint = document.getElementById('upload-hint-container');
    if (uploadHint) {
      uploadHint.style.display = isInput && isAdmin ? 'flex' : 'none';
    }

    // Render sortable thead (updated via _refreshSortHeaders)
    _refreshSortHeaders();

    // Show loading spinner only on initial (non-silent) load (task 1.5)
    if (!silent) {
      tbody.innerHTML = `<tr><td colspan="${colSpan}"><div class="text-center" style="padding:30px;"><span class="spinner"></span></div></td></tr>`;
    }

    try {
      const promises = [API.getFiles(_activeFolder)];
      if (_activeFolder === 'output') {
        promises.push(API.getMetrics().catch(() => null));
      }

      const [data, metrics] = await Promise.all(promises);
      let newFiles = Array.isArray(data) ? data : (data.files || []);

      // Filter keyword tab: only show business config files; hide technical ML assets (.pkl, thresholds.json)
      if (_activeFolder === 'keyword') {
        const KW_WHITELIST = new Set(['kw_map.json', 'system_prompt.txt']);
        newFiles = newFiles.filter(f => {
          const lower = (f.name || f.filename || '').toLowerCase();
          return KW_WHITELIST.has(lower)
            || lower.includes('phân chia nhóm sản phẩm v2')
            || lower.includes('phan chia nhom san pham v2');
        });
      }

      // Smart refresh: skip re-render if data unchanged
      const newHash = JSON.stringify(newFiles);
      if (silent && newHash === _lastFilesHash) {
        return; // No changes, skip DOM update
      }
      _lastFilesHash = newHash;

      // Interaction-aware: defer DOM update if user is interacting (task 2.4)
      if (silent && (_isUserInteracting || _previewFile)) {
        _pendingData = { newFiles, isInput, metrics };
        _showBanner(); // task 3.3
        return;
      }

      _allFiles = newFiles;
      _updateFileCount(metrics);
      _applyFilters(silent);
    } catch (e) {
      if (!silent) {
        tbody.innerHTML = `<tr><td colspan="${colSpan}"><div class="text-center text-red" style="padding:30px;">Lỗi tải file: ${escHtml(e.message)}</div></td></tr>`;
      }
    }
  }

  function getStatusBadge(status) {
    const map = {
      new:        '<span class="badge badge-amber">🆕 Mới</span>',
      processing: '<span class="badge badge-blue">🔄 Đang xử lý</span>',
      done:       '<span class="badge badge-green">✅ Hoàn thành</span>',
      retry:      '<span class="badge badge-purple">🔁 Thử lại</span>',
      failed:     '<span class="badge badge-red">❌ Thất bại</span>',
    };
    return map[status] || '<span class="badge badge-muted">—</span>';
  }

  // ================================================================
  // Search / Filter / Sort helpers  (file-manager-ui-overhaul)
  // ================================================================

  /** Strip Vietnamese diacritics and lowercase for accent-insensitive matching. */
  function _normalizeVN(str) {
    return String(str || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  }

  /** Render a sort-direction indicator icon for a column header. */
  function _sortIcon(col) {
    if (_sortCol !== col) return '<span class="sort-icon">↕</span>';
    return _sortDir === 'asc'
      ? '<span class="sort-icon active">↑</span>'
      : '<span class="sort-icon active">↓</span>';
  }

  /** Rewrite the file table thead with sortable, clickable column headers. */
  function _refreshSortHeaders() {
    const thead = document.querySelector('#file-table thead');
    if (!thead) return;
    const isAdmin = isAdminRole();
    const isInput = _activeFolder === 'input';
    thead.innerHTML = `
      <tr>
        ${isAdmin ? '<th style="width:30px;"><input type="checkbox" class="file-checkbox" id="select-all-cb" onchange="FilesPage.toggleSelectAll()"></th>' : ''}
        <th style="width:30px;"></th>
        <th style="width:40px;">#</th>
        <th class="sortable${_sortCol === 'name' ? ' sorted' : ''}" onclick="FilesPage.onSort('name')">Tên file ${_sortIcon('name')}</th>
        <th class="sortable${_sortCol === 'size' ? ' sorted' : ''}" onclick="FilesPage.onSort('size')">Kích thước ${_sortIcon('size')}</th>
        <th class="sortable${_sortCol === 'modified' ? ' sorted' : ''}" onclick="FilesPage.onSort('modified')">Ngày sửa đổi ${_sortIcon('modified')}</th>
        ${isInput ? '<th>Trạng thái</th>' : ''}
        <th style="width:120px;">Hành động</th>
      </tr>
    `;
  }

  /**
   * Filter + sort _allFiles into _files, update the count badge, refresh
   * column headers and re-render the tbody.  Pass silent=true to use DOM-diff.
   */
  function _applyFilters(silent = false) {
    const query = _normalizeVN(_searchQuery);
    let filtered = _allFiles.filter(f => {
      const name = _normalizeVN(f.name || f.filename || '');
      if (query && !name.includes(query)) return false;
      if (_statusFilter !== 'all' && (f.status || null) !== _statusFilter) return false;
      return true;
    });

    // Sort filtered list
    filtered.sort((a, b) => {
      let valA, valB;
      if (_sortCol === 'name') {
        valA = _normalizeVN(a.name || a.filename || '');
        valB = _normalizeVN(b.name || b.filename || '');
      } else if (_sortCol === 'size') {
        valA = a.size || 0;
        valB = b.size || 0;
      } else { // 'modified' (default)
        valA = a.modified || '';
        valB = b.modified || '';
      }
      if (valA < valB) return _sortDir === 'asc' ? -1 : 1;
      if (valA > valB) return _sortDir === 'asc' ? 1 : -1;
      return 0;
    });

    _files = filtered;

    // Update file count display
    const countEl = document.getElementById('file-count');
    if (countEl) {
      const total = _allFiles.length;
      const shown = _files.length;
      if (_activeFolder === 'output' && _lastMetrics) {
        const totalProcessed = _lastMetrics.total_files || 0;
        countEl.innerHTML = `${shown} file <span style="font-size:12px;color:var(--text-muted);margin-left:8px;">💡 Thư mục Kết quả chứa ${total} file vật lý trên SharePoint (bao gồm cả các bản nháp/chạy lại), trong đó Dashboard ghi nhận ${totalProcessed} file input gốc đã được xử lý hoàn tất.</span>`;
      } else if (query || _statusFilter !== 'all') {
        countEl.textContent = `${shown}/${total} file (đang lọc)`;
      } else {
        countEl.textContent = `${total} file`;
      }
    }

    // Refresh sortable column headers
    _refreshSortHeaders();

    // Re-render tbody
    const tbody = document.getElementById('file-tbody');
    if (!tbody) return;
    const isInput = _activeFolder === 'input';
    const isAdmin = isAdminRole();
    const colSpan = isInput ? (isAdmin ? 8 : 7) : (isAdmin ? 7 : 6);

    if (_files.length === 0) {
      const hasFilter = query || _statusFilter !== 'all';
      tbody.innerHTML = `
        <tr><td colspan="${colSpan}">
          <div class="empty-state">
            <div class="empty-state-icon">${hasFilter ? '🔍' : '📭'}</div>
            <p class="empty-state-text">${hasFilter ? 'Không có file nào khớp bộ lọc' : 'Thư mục trống'}</p>
            <p class="empty-state-hint">${hasFilter ? 'Thử xóa từ khóa hoặc thay đổi bộ lọc trạng thái' : 'Chưa có file nào trong thư mục ' + _activeFolder}</p>
          </div>
        </td></tr>
      `;
      return;
    }

    if (silent) {
      diffFileRows(tbody, _files, isInput, true);
    } else {
      tbody.innerHTML = _files.map((f, i) => buildRowHTML(f, i, isInput, false)).join('');
    }
  }

  /** Called by File Control Bar search input oninput. */
  function onSearch(value) {
    _searchQuery = value;
    _applyFilters(true);
  }

  /** Called by File Control Bar status dropdown onchange. */
  function onFilterStatus(value) {
    _statusFilter = value;
    _applyFilters(true);
  }

  /** Called when user clicks a sortable column header. Toggles direction if same column. */
  function onSort(col) {
    if (_sortCol === col) {
      _sortDir = _sortDir === 'asc' ? 'desc' : 'asc';
    } else {
      _sortCol = col;
      // Default: modified → desc (newest first), others → asc
      _sortDir = col === 'modified' ? 'desc' : 'asc';
    }
    // Full re-render (not DOM-diff) so rows are reordered instantly in one pass.
    // Sort is a deliberate user action — losing scroll pos is acceptable.
    _applyFilters(false);
  }

  async function preview(filename) {
    const panel = document.getElementById('preview-panel');
    const body = document.getElementById('preview-body');
    const nameEl = document.getElementById('preview-filename');
    if (!panel || !body) return;

    panel.classList.remove('hidden');
    document.addEventListener('keydown', _escHandler);
    nameEl.textContent = `📄 ${filename}`;
    body.innerHTML = '<div class="text-center" style="padding:20px;"><span class="spinner"></span> Đang tải...</div>';

    try {
      const data = await API.getFilePreview(_activeFolder, filename);

      // Type-based rendering from backend response
      if (data && data.type === 'table' && data.data && data.columns) {
        let html = renderPreviewTable(data.data, data.columns);
        if (data.truncated) {
          html += '<p class="text-muted text-center mt-2" style="font-size:11px;">⚠️ File đã bị cắt bớt do kích thước lớn</p>';
        }
        body.innerHTML = html;
      } else if (data && data.type === 'json') {
        const formatted = typeof data.content === 'string'
          ? escHtml(data.content)
          : escHtml(JSON.stringify(data.content, null, 2));
        let html = `<pre style="white-space:pre-wrap;word-break:break-word;color:var(--accent-blue);line-height:1.6;font-family:var(--font-mono);font-size:12px;background:rgba(0,0,0,0.2);padding:16px;border-radius:6px;max-height:400px;overflow-y:auto;">${formatted}</pre>`;
        if (data.parse_error) {
          html = `<div style="margin-bottom:8px;padding:8px 12px;border-radius:4px;background:rgba(239,68,68,0.1);border:1px solid rgba(239,68,68,0.3);color:var(--accent-red);font-size:11px;">⚠️ JSON parse error: ${escHtml(data.parse_error)}</div>` + html;
        }
        if (data.truncated) {
          html += '<p class="text-muted text-center mt-2" style="font-size:11px;">⚠️ File quá lớn — chỉ hiển thị 500KB đầu</p>';
        }
        body.innerHTML = html;
      } else if (data && data.type === 'text') {
        let html = `<pre style="white-space:pre-wrap;word-break:break-word;color:var(--text-primary);line-height:1.6;font-family:var(--font-mono);font-size:12px;background:rgba(0,0,0,0.2);padding:16px;border-radius:6px;max-height:400px;overflow-y:auto;">${escHtml(data.content)}</pre>`;
        if (data.truncated) {
          html += `<p class="text-muted text-center mt-2" style="font-size:11px;">⚠️ Hiển thị ${MAX_TEXT_LINES || 200} dòng đầu / ${data.total_lines} dòng</p>`;
        }
        body.innerHTML = html;
      } else if (data && data.type === 'unsupported') {
        body.innerHTML = `
          <div class="empty-state" style="padding:30px;">
            <div class="empty-state-icon">📦</div>
            <p class="empty-state-text">Không hỗ trợ xem trước file ${escHtml(data.extension || '')}</p>
            <p class="empty-state-hint">Định dạng file này chưa được hỗ trợ preview</p>
          </div>
        `;
      } else if (typeof data === 'string') {
        body.innerHTML = `<pre style="white-space:pre-wrap;word-break:break-word;color:var(--text-primary);line-height:1.6;">${escHtml(data)}</pre>`;
      } else {
        body.innerHTML = `<pre style="white-space:pre-wrap;color:var(--text-primary);">${escHtml(JSON.stringify(data, null, 2))}</pre>`;
      }
    } catch (e) {
      body.innerHTML = `<div class="text-center text-red" style="padding:20px;">Không thể xem trước: ${escHtml(e.message)}</div>`;
    }
  }

  function renderPreviewTable(rows, headers) {
    if (!rows || rows.length === 0) return '<p class="text-muted text-center">Không có dữ liệu</p>';

    const hdr = headers || Object.keys(rows[0]);
    let html = '<div class="table-preview-wrapper"><table class="table table-preview">';
    html += '<thead><tr>' + hdr.map(h => `<th>${escHtml(String(h))}</th>`).join('') + '</tr></thead>';
    html += '<tbody>';
    rows.forEach(row => {
      html += '<tr>';
      hdr.forEach(h => {
        const val = Array.isArray(row) ? row[hdr.indexOf(h)] : (row[h] ?? '');
        const isContentCol = String(h).toLowerCase().includes('nội dung') || String(h).toLowerCase().includes('noi dung');
        const tdClass = isContentCol ? 'wrap' : '';
        html += `<td class="${tdClass}">${escHtml(String(val))}</td>`;
      });
      html += '</tr>';
    });
    html += '</tbody></table></div>';
    html += `<p class="text-muted text-center mt-3" style="font-size:12px;">Đã tải xem trước ${rows.length} dòng</p>`;
    return html;
  }

  function closePreview() {
    document.getElementById('preview-panel')?.classList.add('hidden');
    document.removeEventListener('keydown', _escHandler);
    _previewFile = null;
  }

  /** Close modal when clicking the dark backdrop (not the inner dialog). */
  function _onBackdropClick(e) {
    if (e.target === document.getElementById('preview-panel')) closePreview();
  }

  /** ESC key handler — attached while a preview is open. */
  function _escHandler(e) {
    if (e.key === 'Escape') closePreview();
  }

  function previewTemplate() {
    const panel = document.getElementById('preview-panel');
    const body = document.getElementById('preview-body');
    const nameEl = document.getElementById('preview-filename');
    if (!panel || !body) return;

    panel.classList.remove('hidden');
    nameEl.textContent = `📄 Cấu trúc file Excel mẫu (Template)`;
    _previewFile = 'template';

    const columns = ["Nội dung phản hồi", "Người gửi", "Ghi chú (Tùy chọn)"];
    const rows = [
      {
        "Nội dung phản hồi": "Ứng dụng chạy rất mượt nhưng đôi khi bị lag nhẹ khi tải dữ liệu lớn.",
        "Người gửi": "Nguyễn Văn A",
        "Ghi chú (Tùy chọn)": "Góp ý giao diện"
      },
      {
        "Nội dung phản hồi": "Tôi không thể đăng nhập vào tài khoản từ sáng nay, báo lỗi kết nối.",
        "Người gửi": "Trần Thị B",
        "Ghi chú (Tùy chọn)": "Lỗi kỹ thuật"
      }
    ];

    let html = `
      <div style="margin-bottom:16px; font-size:13px; color:var(--text-secondary); line-height:1.6; background:rgba(255,255,255,0.02); padding:14px; border-radius:var(--radius-md); border:1px solid var(--border);">
        <p style="margin:0 0 8px 0; font-weight:600; color:var(--text-primary);">📌 Hướng dẫn chuẩn bị file Excel tải lên:</p>
        <ul style="margin:0; padding-left:20px; display:flex; flex-direction:column; gap:6px;">
          <li>Bắt buộc phải chứa <strong>cột nội dung</strong> có tiêu đề chứa từ <code style="color:var(--accent-amber);background:rgba(245,158,11,0.1);padding:2px 4px;border-radius:4px;font-family:var(--font-mono);">"Nội dung"</code> hoặc <code style="color:var(--accent-amber);background:rgba(245,158,11,0.1);padding:2px 4px;border-radius:4px;font-family:var(--font-mono);">"noi dung"</code> (Ví dụ: <i>Nội dung phản hồi, Nội dung vấn đề, noi_dung...</i>).</li>
          <li>Các cột thông tin bổ sung đi kèm (như <i>Người gửi, Ngày tháng, ID, Chi nhánh...</i>) <strong>sẽ được hệ thống tự động giữ nguyên và xuất ra trong file kết quả phân loại</strong>.</li>
          <li>File tải lên phải thuộc định dạng Excel <code style="color:var(--accent-blue);background:rgba(59,130,246,0.1);padding:2px 4px;border-radius:4px;font-family:var(--font-mono);">.xlsx</code>.</li>
        </ul>
      </div>
    `;
    
    html += renderPreviewTable(rows, columns);
    
    html += `
      <div style="margin-top:16px; display:flex; gap:10px; align-items:center;">
        <button class="btn btn-primary btn-sm" onclick="FilesPage.downloadTemplate()">📥 Tải file mẫu (.xlsx)</button>
        <button class="btn btn-secondary btn-sm" onclick="FilesPage.closePreview()">✕ Đóng</button>
      </div>
    `;

    body.innerHTML = html;
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function startRefresh() {
    stopRefresh();
    // Interval changed from 30s to 60s (task 5.1)
    _refreshInterval = setInterval(() => {
      // Auto-refresh if user is not previewing — interaction check is inside loadFiles
      if (!_previewFile) {
        loadFiles(true);
      }
    }, 60000);
  }

  function stopRefresh() {
    if (_refreshInterval) {
      clearInterval(_refreshInterval);
      _refreshInterval = null;
    }
  }

  function refresh() {
    // Clear pending data before manual refresh to avoid race condition (task 6.3)
    _pendingData = null;
    _hideBanner();
    // Save and restore scroll position (task 4.3)
    const tableWrap = document.getElementById('file-table-wrap');
    const savedScroll = tableWrap ? tableWrap.scrollTop : 0;
    loadFiles().then(() => {
      if (tableWrap) tableWrap.scrollTop = savedScroll;
    });
    Toast.info('Đã làm mới danh sách file');
  }

  async function handleUpload(inputEl) {
    if (!isAdminRole()) {
      Toast.error('Bạn không có quyền tải file lên');
      inputEl.value = '';
      return;
    }
    const files = Array.from(inputEl.files);
    if (files.length === 0) return;

    // Filter valid files
    const validFiles = files.filter(f => f.name.toLowerCase().endsWith('.xlsx'));
    if (validFiles.length === 0) {
      Toast.error('Chỉ chấp nhận file .xlsx');
      inputEl.value = '';
      return;
    }
    if (validFiles.length > 10) {
      Toast.error('Tối đa 10 file mỗi lần upload');
      inputEl.value = '';
      return;
    }

    inputEl.value = '';
    _doMultiUpload(validFiles);
  }

  async function _doMultiUpload(files) {
    if (!isAdminRole()) {
      Toast.error('Bạn không có quyền tải file lên');
      return;
    }
    // Show upload progress overlay
    const overlay = document.createElement('div');
    overlay.className = 'upload-progress-overlay';
    overlay.id = 'upload-progress-overlay';
    overlay.innerHTML = `
      <div class="upload-progress-panel">
        <h3 style="margin:0 0 16px;font-size:15px;">📤 Đang tải ${files.length} file...</h3>
        <div id="upload-progress-list">
          ${files.map((f, i) => `
            <div class="upload-progress-item" id="upload-item-${i}">
              <span class="file-name">${escHtml(f.name)}</span>
              <div class="progress-bar-wrap"><div class="progress-bar-fill" id="upload-bar-${i}" style="width:0%;"></div></div>
              <span class="progress-pct" id="upload-pct-${i}">0%</span>
            </div>
          `).join('')}
        </div>
      </div>
    `;
    document.body.appendChild(overlay);

    let successCount = 0;
    let failCount = 0;
    let ingestFailCount = 0;

    for (let i = 0; i < files.length; i++) {
      const formData = new FormData();
      formData.append('file', files[i]);
      try {
        const result = await uploadWithProgress(formData, (pct) => {
          const bar = document.getElementById(`upload-bar-${i}`);
          const pctEl = document.getElementById(`upload-pct-${i}`);
          if (bar) bar.style.width = pct + '%';
          if (pctEl) pctEl.textContent = pct + '%';
        });
        const item = document.getElementById(`upload-item-${i}`);
        if (result.ingest_error) {
          ingestFailCount++;
          if (item) { item.classList.add('error'); item.querySelector('.progress-pct').textContent = '⚠️'; }
        } else if (item) {
          item.classList.add('success');
          item.querySelector('.progress-pct').textContent = '✅';
        }
        successCount++;
      } catch (e) {
        const item = document.getElementById(`upload-item-${i}`);
        if (item) { item.classList.add('error'); item.querySelector('.progress-pct').textContent = '❌'; }
        failCount++;
      }
    }

    // Show result for 1.5s then remove overlay
    setTimeout(() => {
      document.getElementById('upload-progress-overlay')?.remove();
      if (failCount > 0) {
        Toast.error(`Tải ${successCount}/${files.length} file thành công, ${failCount} thất bại`);
      } else if (ingestFailCount > 0) {
        Toast.error(`${successCount} file đã tải lên nhưng chưa đưa được vào phân tích: ${ingestFailCount} file`);
      } else {
        Toast.success(`Đã tải ${successCount} file thành công`);
      }
      refresh();
    }, 1500);
  }

  async function syncSharePoint() {
    const btn = document.getElementById('btn-sync-sharepoint');
    const origText = btn ? btn.innerHTML : '';
    try {
      if (btn) { btn.disabled = true; btn.innerHTML = '⏳ Đang đồng bộ...'; }
      Toast.info('☁️ Đang đồng bộ SharePoint...');
      const res = await API.syncSharePoint();
      Toast.success(res.message || 'Đồng bộ SharePoint thành công');
      refresh();
    } catch (e) {
      Toast.error('Lỗi đồng bộ: ' + e.message);
    } finally {
      if (btn) { btn.disabled = false; btn.innerHTML = origText; }
    }
  }

  // === Multi-file upload (tasks 3.1-4.5) ===

  function uploadWithProgress(formData, onProgress) {
    return API.uploadWithProgress('/files/upload', formData, onProgress);
  }

  // === Drag & Drop (tasks 5.1-5.4) ===

  function _initDragDrop() {
    const container = document.getElementById('file-table-wrap');
    if (!container) return;

    container.addEventListener('dragenter', (e) => {
      e.preventDefault();
      _dragCounter++;
      if (_activeFolder === 'input' && isAdminRole()) {
        const zone = document.getElementById('file-drop-zone');
        if (zone) zone.classList.add('active');
      }
    });

    container.addEventListener('dragleave', (e) => {
      e.preventDefault();
      _dragCounter--;
      if (_dragCounter <= 0) {
        _dragCounter = 0;
        const zone = document.getElementById('file-drop-zone');
        if (zone) zone.classList.remove('active');
      }
    });

    container.addEventListener('dragover', (e) => {
      e.preventDefault();
    });

    container.addEventListener('drop', (e) => {
      e.preventDefault();
      _dragCounter = 0;
      const zone = document.getElementById('file-drop-zone');
      if (zone) zone.classList.remove('active');

      if (!isAdminRole()) {
        Toast.error('Bạn không có quyền tải file lên');
        return;
      }

      if (_activeFolder !== 'input') {
        Toast.error('Chỉ có thể tải file vào thư mục Đầu vào');
        return;
      }

      const files = Array.from(e.dataTransfer.files).filter(f => f.name.toLowerCase().endsWith('.xlsx'));
      if (files.length === 0) {
        Toast.error('Chỉ chấp nhận file .xlsx');
        return;
      }
      if (files.length > 10) {
        Toast.error('Tối đa 10 file mỗi lần upload');
        return;
      }

      _doMultiUpload(files);
    });
  }

  // === Row detail / metadata (tasks 6.1-6.4) ===

  function toggleRowDetail(filename) {
    const tbody = document.getElementById('file-tbody');
    if (!tbody) return;

    if (_expandedRows.has(filename)) {
      _expandedRows.delete(filename);
      const detailRow = tbody.querySelector(`tr.detail-row[data-detail-for="${CSS.escape(filename)}"]`);
      if (detailRow) detailRow.remove();
      const icon = tbody.querySelector(`tr[data-filename="${CSS.escape(filename)}"] .expand-icon`);
      if (icon) icon.classList.remove('expanded');
      return;
    }

    _expandedRows.add(filename);
    const icon = tbody.querySelector(`tr[data-filename="${CSS.escape(filename)}"] .expand-icon`);
    if (icon) icon.classList.add('expanded');

    const fileRow = tbody.querySelector(`tr[data-filename="${CSS.escape(filename)}"]`);
    if (!fileRow) return;

    const isInput = _activeFolder === 'input';
    const isAdmin = isAdminRole();
    const colSpan = isInput ? (isAdmin ? 8 : 7) : (isAdmin ? 7 : 6);
    const detailTr = document.createElement('tr');
    detailTr.className = 'detail-row';
    detailTr.setAttribute('data-detail-for', filename);
    detailTr.innerHTML = `<td colspan="${colSpan}"><div class="metadata-grid"><span class="text-muted" style="font-size:12px;"><span class="spinner" style="width:12px;height:12px;"></span> Đang tải metadata...</span></div></td>`;
    fileRow.after(detailTr);

    _loadMetadata(filename, detailTr);
  }

  async function _loadMetadata(filename, detailTr) {
    const cacheKey = `${_activeFolder}/${filename}`;
    let metadata = _metadataCache.get(cacheKey);

    if (!metadata) {
      try {
        metadata = await API.get(`/files/${_activeFolder}/${encodeURIComponent(filename)}/metadata`);
        _metadataCache.set(cacheKey, metadata);
      } catch (e) {
        const td = detailTr.querySelector('td');
        if (td) td.innerHTML = `<div class="text-muted" style="font-size:12px;">⚠️ Không thể tải metadata: ${escHtml(e.message)}</div>`;
        return;
      }
    }

    const td = detailTr.querySelector('td');
    if (!td) return;

    const cols = metadata.columns || [];
    const colDisplay = cols.length > 5
      ? cols.slice(0, 5).map(c => escHtml(c)).join(', ') + ` ... +${cols.length - 5} cột khác`
      : cols.map(c => escHtml(c)).join(', ') || '—';
    const source = metadata.source === 'sharepoint' ? '☁️ SharePoint' : '📤 Local';
    const classifiedPct = metadata.classified_pct ?? null;

    td.innerHTML = `
      <div class="metadata-grid">
        <div><span class="meta-label">Số dòng</span><div class="meta-value">${metadata.row_count ?? '—'}</div></div>
        <div><span class="meta-label">Nguồn</span><div class="meta-value">${source}</div></div>
        <div><span class="meta-label">Cột dữ liệu</span><div class="meta-value" style="font-size:11px;">${colDisplay}</div></div>
        ${classifiedPct !== null ? `<div><span class="meta-label">Phân loại</span><div class="meta-value"><div style="display:flex;align-items:center;gap:6px;"><div style="width:80px;height:6px;background:var(--bg-tertiary);border-radius:3px;overflow:hidden;"><div style="height:100%;background:var(--accent-green);width:${classifiedPct}%;"></div></div><span style="font-size:11px;">${classifiedPct}%</span></div></div></div>` : ''}
      </div>
    `;
  }

  // === File selection / bulk (tasks 7.1-7.5) ===

  function toggleFileSelection(filename) {
    if (_selectedFiles.has(filename)) {
      _selectedFiles.delete(filename);
    } else {
      _selectedFiles.add(filename);
    }
    _updateSelectionUI();
  }

  function toggleSelectAll() {
    const headerCb = document.getElementById('select-all-cb');
    if (headerCb && headerCb.checked) {
      _files.forEach(f => _selectedFiles.add(f.name || f.filename || 'unknown'));
    } else {
      _selectedFiles.clear();
    }
    _updateSelectionUI();
  }

  function selectAllFiles() {
    _files.forEach(f => _selectedFiles.add(f.name || f.filename || 'unknown'));
    _updateSelectionUI();
  }

  function clearSelection() {
    _selectedFiles.clear();
    _updateSelectionUI();
  }

  function _updateSelectionUI() {
    // Update row checkboxes and selected class
    const tbody = document.getElementById('file-tbody');
    if (tbody) {
      tbody.querySelectorAll('tr[data-filename]').forEach(row => {
        const fname = row.getAttribute('data-filename');
        const cb = row.querySelector('.file-checkbox');
        const isSelected = _selectedFiles.has(fname);
        row.classList.toggle('selected', isSelected);
        if (cb) cb.checked = isSelected;
      });
    }

    // Update header checkbox
    const headerCb = document.getElementById('select-all-cb');
    if (headerCb) {
      const total = _files.length;
      const selected = _selectedFiles.size;
      headerCb.checked = total > 0 && selected === total;
      headerCb.indeterminate = selected > 0 && selected < total;
    }

    // Show/hide bulk toolbar
    const toolbar = document.getElementById('bulk-toolbar');
    const countEl = document.getElementById('bulk-count');
    if (toolbar) {
      toolbar.classList.toggle('visible', _selectedFiles.size > 0);
    }
    if (countEl) {
      countEl.textContent = _selectedFiles.size;
    }
  }

  // === Bulk delete (tasks 8.1-8.4) ===

  function bulkDelete() {
    if (_selectedFiles.size === 0) return;
    const filenames = [..._selectedFiles];

    const overlay = document.createElement('div');
    overlay.className = 'bulk-confirm-overlay';
    overlay.id = 'bulk-confirm-overlay';
    overlay.innerHTML = `
      <div class="bulk-confirm-dialog">
        <h3>🗑️ Giải phóng bộ nhớ: ${filenames.length} file</h3>
        <p style="font-size:13px;color:var(--text-secondary);margin:0 0 8px;">
          Hành động này chỉ xóa bản sao đang lưu trên ổ đĩa máy chủ (local cache).
          File gốc trên SharePoint Cloud <strong>không bị xóa</strong>.
          Sau khi xóa, badge của file sẽ chuyển sang <em>☁️ Chỉ trên Cloud</em>.
        </p>
        <ul class="file-list">
          ${filenames.map(f => `<li>📄 ${escHtml(f)}</li>`).join('')}
        </ul>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px;">
          <button class="btn btn-secondary btn-sm" onclick="document.getElementById('bulk-confirm-overlay')?.remove()">Hủy</button>
          <button class="btn btn-danger btn-sm" id="btn-confirm-bulk-delete">🗑️ Xóa local/cache</button>
        </div>
      </div>
    `;
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) overlay.remove();
    });
    document.body.appendChild(overlay);

    document.getElementById('btn-confirm-bulk-delete')?.addEventListener('click', async () => {
      overlay.remove();
      Toast.info('🗑️ Đang xóa...');
      try {
        const res = await API.post('/files/bulk-delete', { folder: _activeFolder, filenames });
        const deleted = res.deleted || [];
        const failed = res.failed || [];
        const deleteScope = res.delete_scope || 'local_cache';
        if (failed.length > 0) {
          Toast.error(`Xóa ${deleteScope} ${deleted.length}/${filenames.length} file. ${failed.length} file thất bại.`);
        } else {
          Toast.success(res.message || `Đã xóa local/cache ${deleted.length} file`);
        }
        clearSelection();
        refresh();
      } catch (e) {
        Toast.error('Lỗi xóa file: ' + e.message);
      }
    });
  }

  function bulkDeleteSharePoint() {
    if (_selectedFiles.size === 0) return;
    const items = selectedFileItems();
    const cloudItems = items.filter(item => item.id);
    if (cloudItems.length === 0) {
      Toast.error('Các file đang chọn không có SharePoint item id. Hãy bấm Làm mới/Đồng bộ SharePoint rồi thử lại.');
      return;
    }

    const overlay = document.createElement('div');
    overlay.className = 'bulk-confirm-overlay';
    overlay.id = 'bulk-confirm-overlay';
    overlay.innerHTML = `
      <div class="bulk-confirm-dialog">
        <h3>☁️ Xóa trên SharePoint ${cloudItems.length} file</h3>
        <p style="font-size:13px;color:var(--text-secondary);margin:0 0 8px;">
          Hành động này xóa file trên SharePoint Cloud. Bản local/cache không tự xóa trừ khi bạn chọn Xóa local/cache riêng.
        </p>
        <ul class="file-list">
          ${cloudItems.map(f => `<li>☁️ ${escHtml(f.name)}</li>`).join('')}
        </ul>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px;">
          <button class="btn btn-secondary btn-sm" onclick="document.getElementById('bulk-confirm-overlay')?.remove()">Hủy</button>
          <button class="btn btn-danger btn-sm" id="btn-confirm-sp-delete">☁️ Xóa trên SharePoint</button>
        </div>
      </div>
    `;
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) overlay.remove();
    });
    document.body.appendChild(overlay);

    document.getElementById('btn-confirm-sp-delete')?.addEventListener('click', async () => {
      overlay.remove();
      Toast.info('🗑️ Đang xóa...');
      try {
        const res = await API.post('/files/sharepoint-delete', { folder: _activeFolder, items: cloudItems });
        const deleted = res.remote_deleted || [];
        const failed = res.failed || [];
        const deleteScope = res.delete_scope || 'sharepoint';
        if (failed.length > 0) {
          Toast.error(`Xóa ${deleteScope} ${deleted.length}/${cloudItems.length} file. ${failed.length} file thất bại.`);
        } else {
          Toast.success(res.message || `Đã xóa SharePoint ${deleted.length} file`);
        }
        clearSelection();
        refresh();
      } catch (e) {
        Toast.error('Lỗi xóa SharePoint: ' + e.message);
      }
    });
  }

  function destroy() {
    _previewFile = null;
    // Clean up pending data and interaction tracking (task 6.2)
    _pendingData = null;
    _isUserInteracting = false;
    _unbindInteractionTracking();
    stopRefresh();
  }

  function formatSize(bytes) {
    if (bytes === 0) return '—';
    const units = ['B', 'KB', 'MB', 'GB'];
    let idx = 0;
    let size = bytes;
    while (size >= 1024 && idx < units.length - 1) { size /= 1024; idx++; }
    return `${size.toFixed(idx > 0 ? 1 : 0)} ${units[idx]}`;
  }

  function formatDate(isoString) {
    if (!isoString || isoString === '—') return '—';
    try {
      const d = new Date(isoString);
      if (isNaN(d.getTime())) return '—';
      const now = new Date();
      const pad = n => String(n).padStart(2, '0');
      const day = pad(d.getDate());
      const month = pad(d.getMonth() + 1);
      const hours = pad(d.getHours());
      const mins = pad(d.getMinutes());
      if (d.getFullYear() !== now.getFullYear()) {
        return `${day}/${month}/${d.getFullYear()} ${hours}:${mins}`;
      }
      return `${day}/${month} ${hours}:${mins}`;
    } catch { return '—'; }
  }

  function escHtml(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
  function escAttr(s) { return s.replace(/'/g, "\\'").replace(/"/g, '&quot;'); }

  return {
    render, destroy, switchFolder, loadFiles, preview, closePreview, previewTemplate,
    downloadTemplate, downloadFile, ingestInputFile,
    refresh, handleUpload, syncSharePoint,
    applyPendingData,
    toggleFileSelection, toggleSelectAll, selectAllFiles, clearSelection, bulkDelete, bulkDeleteSharePoint,
    toggleRowDetail, editKeywordAsset,
    onSearch, onFilterStatus, onSort,
    _onBackdropClick,

  };
})();
