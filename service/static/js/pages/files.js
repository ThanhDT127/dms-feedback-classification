/* ============================================================
   File Manager Page — browse, upload, preview files
   ============================================================ */

window.FilesPage = (() => {
  const FOLDERS = [
    { id: 'input',      label: 'input',   icon: '📥' },
    { id: 'output',     label: 'output',    icon: '📤' },
    { id: 'checkpoint', label: 'checkpoint', icon: '💾' },
    { id: 'keyword',    label: 'keyword',   icon: '🔑' },
    { id: 'model',      label: 'model',      icon: '🤖' },
  ];

  let _activeFolder = 'input';
  let _files = [];
  let _previewFile = null;
  let _refreshInterval = null;

  function render() {
    const app = document.getElementById('app');
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

      <!-- Toolbar -->
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;flex-wrap:wrap;gap:8px;">
        <div style="display:flex;align-items:center;gap:8px;">
          <span id="file-count" class="text-secondary" style="font-size:13px;"></span>
        </div>
        <div class="btn-group">
          <button class="btn btn-secondary btn-sm" onclick="FilesPage.refresh()">🔄 Làm mới</button>
        </div>
      </div>

      <!-- File Table -->
      <div class="card" style="padding:0;overflow:hidden;">
        <div class="table-wrap" style="max-height:450px;overflow-y:auto;">
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
              <tr><td colspan="6"><div class="text-center text-muted" style="padding:30px;">Đang tải...</div></td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Preview Panel -->
      <div id="preview-panel" class="hidden mt-6">
        <div class="preview-panel">
          <div class="preview-header">
            <span id="preview-filename">📄 Preview</span>
            <button class="btn btn-ghost btn-sm" onclick="FilesPage.closePreview()">✕ Đóng</button>
          </div>
          <div class="preview-body" id="preview-body"></div>
        </div>
      </div>

      <!-- Folder Tree -->
      <div class="card mt-6 animate-in animate-in-delay-3">
        <div class="card-header" style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
          <span class="card-title"><span class="icon">🌳</span> Cấu trúc thư mục</span>
          <div style="display:flex; gap:6px;">
            <button class="btn btn-ghost btn-sm" onclick="FilesPage.expandAllTree(true)" style="font-size:11px; padding: 2px 6px;">➕ Mở rộng hết</button>
            <button class="btn btn-ghost btn-sm" onclick="FilesPage.expandAllTree(false)" style="font-size:11px; padding: 2px 6px;">➖ Thu gọn hết</button>
            <button class="btn btn-ghost btn-sm" onclick="FilesPage.loadTree()" title="Làm mới">🔄</button>
          </div>
        </div>
        <div id="folder-tree" style="font-family:var(--font-mono);font-size:12px;color:var(--text-secondary);max-height:300px;overflow-y:auto;">
          Đang tải...
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
            <p style="margin:4px 0 0 0;color:var(--text-muted);">Hệ thống hoạt động theo mô hình Cloud-first. Để phân loại phản hồi, anh chỉ cần kéo thả/tải file Excel lên trực tiếp thư mục <span style="color:var(--accent-blue);font-weight:500;">Input</span> trên SharePoint của anh. Watcher của hệ thống sẽ quét tự động, tải tạm về máy ảo để xử lý và đẩy kết quả lên thư mục <span style="color:var(--accent-green);font-weight:500;">Output</span> trên SharePoint Cloud. Không hỗ trợ tải file lên local tại đây để tránh xung đột luồng.</p>
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
    loadTree();
    startRefresh();
  }

  function switchFolder(folder) {
    _activeFolder = folder;
    _previewFile = null;

    document.querySelectorAll('#file-tabs .tab-item').forEach(t => {
      t.classList.toggle('active', t.dataset.folder === folder);
    });

    document.getElementById('preview-panel')?.classList.add('hidden');
    loadFiles();
    startRefresh();
  }

  async function loadFiles(silent = false) {
    const tbody = document.getElementById('file-tbody');
    const countEl = document.getElementById('file-count');
    if (!tbody) return;

    const isInput = _activeFolder === 'input';
    const colSpan = isInput ? 6 : 5;

    // Update the thead dynamically
    const thead = document.querySelector('#file-table thead');
    if (thead) {
      thead.innerHTML = `
        <tr>
          <th style="width:40px;">#</th>
          <th>Tên file</th>
          <th>Kích thước</th>
          <th>Ngày sửa đổi</th>
          ${isInput ? '<th>Trạng thái</th>' : ''}
          <th style="width:100px;">Hành động</th>
        </tr>
      `;
    }

    if (!silent) {
      tbody.innerHTML = `<tr><td colspan="${colSpan}"><div class="text-center" style="padding:30px;"><span class="spinner"></span></div></td></tr>`;
    }

    try {
      const data = await API.getFiles(_activeFolder);
      _files = Array.isArray(data) ? data : (data.files || []);

      if (countEl) countEl.textContent = `${_files.length} file`;

      if (_files.length === 0) {
        tbody.innerHTML = `
          <tr><td colspan="${colSpan}">
            <div class="empty-state">
              <div class="empty-state-icon">📭</div>
              <p class="empty-state-text">Thư mục trống</p>
              <p class="empty-state-hint">Chưa có file nào trong thư mục ${_activeFolder}</p>
            </div>
          </td></tr>
        `;
        return;
      }

      tbody.innerHTML = _files.map((f, i) => {
        const name = f.name || f.filename || 'unknown';
        const size = formatSize(f.size || 0);
        const date = f.modified || f.date || '—';
        const status = getStatusBadge(f.status);

        return `
          <tr class="animate-in" style="animation-delay:${i * 30}ms">
            <td class="text-muted">${i + 1}</td>
            <td>
              <span style="cursor:pointer;color:var(--accent-blue);" onclick="FilesPage.preview('${escAttr(name)}')">
                ${escHtml(name)}
              </span>
            </td>
            <td class="text-muted text-mono" style="font-size:12px;">${size}</td>
            <td class="text-muted" style="font-size:12px;">${escHtml(date)}</td>
            ${isInput ? `<td>${status}</td>` : ''}
            <td>
              <button class="btn btn-ghost btn-sm" title="Xem" onclick="FilesPage.preview('${escAttr(name)}')">👁️</button>
            </td>
          </tr>
        `;
      }).join('');
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

  async function preview(filename) {
    const panel = document.getElementById('preview-panel');
    const body = document.getElementById('preview-body');
    const nameEl = document.getElementById('preview-filename');
    if (!panel || !body) return;

    panel.classList.remove('hidden');
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

    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function renderPreviewTable(rows, headers) {
    if (!rows || rows.length === 0) return '<p class="text-muted text-center">Không có dữ liệu</p>';

    const hdr = headers || Object.keys(rows[0]);
    let html = '<div style="overflow-x:auto;"><table class="table" style="font-size:12px;">';
    html += '<thead><tr>' + hdr.map(h => `<th>${escHtml(String(h))}</th>`).join('') + '</tr></thead>';
    html += '<tbody>';
    rows.slice(0, 20).forEach(row => {
      html += '<tr>';
      hdr.forEach(h => {
        const val = Array.isArray(row) ? row[hdr.indexOf(h)] : (row[h] ?? '');
        html += `<td class="wrap" style="max-width:300px;">${escHtml(String(val))}</td>`;
      });
      html += '</tr>';
    });
    html += '</tbody></table></div>';
    if (rows.length > 20) {
      html += `<p class="text-muted text-center mt-4" style="font-size:12px;">Hiển thị 20/${rows.length} dòng</p>`;
    }
    return html;
  }

  function closePreview() {
    document.getElementById('preview-panel')?.classList.add('hidden');
    _previewFile = null;
  }

  async function loadTree() {
    const el = document.getElementById('folder-tree');
    if (!el) return;

    try {
      const data = await API.getFileTree();
      if (typeof data === 'string') {
        el.innerHTML = `<pre style="line-height:1.8;padding:4px 0;">${escHtml(data)}</pre>`;
      } else if (data && typeof data === 'object') {
        el.innerHTML = renderTreeObj(data);
      } else {
        el.textContent = 'Không có dữ liệu';
      }
    } catch (e) {
      el.innerHTML = `<span class="text-muted">Không thể tải cấu trúc thư mục</span>`;
    }
  }

  function expandAllTree(isOpen) {
    const details = document.querySelectorAll('#folder-tree details');
    details.forEach(d => {
      if (isOpen) {
        d.setAttribute('open', '');
      } else {
        d.removeAttribute('open');
      }
    });
  }

  function renderTreeObj(obj) {
    let html = '<div class="tree-root" style="padding: 4px; display: flex; flex-direction: column; gap: 8px;">';
    const keys = Object.keys(obj);
    keys.forEach(key => {
      const val = obj[key];
      const allFiles = [];
      if (Array.isArray(val)) {
        val.forEach(item => {
          if (item && typeof item === 'object' && Array.isArray(item.files)) {
            item.files.forEach(f => allFiles.push(f.name || f));
          } else {
            allFiles.push(item);
          }
        });
      }
      
      const isInput = key === 'input';
      html += `
        <details ${isInput ? 'open' : ''} style="cursor: pointer; background: rgba(255,255,255,0.01); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 8px;">
          <summary style="font-weight: 600; color: var(--text-primary); font-size: 13px; display: flex; align-items: center; gap: 6px; user-select: none;">
            <span style="font-size: 14px;">📁</span> ${escHtml(key)} <span style="font-size: 11px; color: var(--text-muted); font-weight: normal;">(${allFiles.length} file)</span>
          </summary>
          <ul style="list-style: none; padding-left: 20px; margin: 8px 0 0 0; border-left: 1px dashed var(--border); display: flex; flex-direction: column; gap: 6px;">
            ${allFiles.length > 0 
              ? allFiles.map(file => `
                  <li style="font-family: var(--font-mono); font-size: 12px; color: var(--text-secondary); display: flex; align-items: center; gap: 6px;">
                    <span>📄</span> <span class="tree-file-link" style="color: var(--accent-blue); text-decoration: underline;" onclick="FilesPage.preview('${escAttr(String(file))}')">${escHtml(String(file))}</span>
                  </li>
                `).join('')
              : `<li style="font-style: italic; color: var(--text-muted); font-size: 12px; padding: 2px 0;">Thư mục trống</li>`
            }
          </ul>
        </details>
      `;
    });
    html += '</div>';
    return html;
  }



  function startRefresh() {
    stopRefresh();
    _refreshInterval = setInterval(() => {
      // Chỉ tự động tải lại nếu người dùng không mở xem trước file
      if (!_previewFile) {
        loadFiles(true);
      }
    }, 5000);
  }

  function stopRefresh() {
    if (_refreshInterval) {
      clearInterval(_refreshInterval);
      _refreshInterval = null;
    }
  }

  function refresh() {
    loadFiles();
    loadTree();
    Toast.info('Đã làm mới danh sách file');
  }

  function destroy() {
    _previewFile = null;
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

  function escHtml(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
  function escAttr(s) { return s.replace(/'/g, "\\'").replace(/"/g, '&quot;'); }

  return {
    render, destroy, switchFolder, loadFiles, preview, closePreview,
    loadTree, refresh, expandAllTree
  };
})();
