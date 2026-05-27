/* ============================================================
   Pipeline Viewer Page — Flow diagram, labels, keywords
   ============================================================ */

window.PipelinePage = (() => {
  let _labels = null;
  let _keywords = null;
  let _selectedNode = null;

  const PIPELINE_NODES = [
    { id: 'input',    icon: '📥', label: 'Excel Input',         desc: 'Đọc file Excel đầu vào, phát hiện cột phản hồi' },
    { id: 'header',   icon: '🔍', label: 'Header Detection',    desc: 'Tự động nhận diện cột chứa phản hồi khách hàng' },
    { id: 'rag',      icon: '🤖', label: 'RAG Pipeline',        desc: 'LLM trích xuất sản phẩm + BM25 matching' },
    { id: 'classify', icon: '🏷️', label: 'Issue Classification', desc: 'Phân loại 20 nhãn vấn đề bằng LLM + Rules' },
    { id: 'output',   icon: '📤', label: 'Excel Output',        desc: 'Ghi kết quả ra file Excel đầu ra' },
  ];

  const LABEL_HIERARCHY = [
    {
      name: 'Sản phẩm',
      color: '#3b82f6',
      labels: [
        { name: 'Chất lượng sản phẩm', def: 'Vấn đề về chất lượng, lỗi sản phẩm, độ bền, hiệu suất' },
        { name: 'Bao bì & nhãn mác', def: 'Vấn đề bao bì, đóng gói, nhãn mác sản phẩm' },
        { name: 'Giao hàng & đóng gói', def: 'Vấn đề giao hàng, vận chuyển, đóng gói khi ship' },
      ]
    },
    {
      name: 'YC Công cụ BH',
      color: '#22c55e',
      labels: [
        { name: 'Công cụ bán hàng', def: 'Yêu cầu về catalogue, mẫu sản phẩm, tài liệu bán hàng' },
        { name: 'POSM / Trưng bày', def: 'Point-of-sale materials, vật phẩm trưng bày, banner' },
        { name: 'Chương trình KM', def: 'Chương trình khuyến mại, ưu đãi, quà tặng' },
      ]
    },
    {
      name: 'Giá & Cơ chế',
      color: '#f59e0b',
      labels: [
        { name: 'Giá bán', def: 'Phản hồi về giá bán lẻ, giá sỉ, giá cạnh tranh' },
        { name: 'Chiết khấu / Hoa hồng', def: 'Chiết khấu, commission, hoa hồng đại lý' },
        { name: 'Công nợ / Thanh toán', def: 'Công nợ, điều khoản thanh toán, trả chậm' },
        { name: 'Cơ chế bán hàng', def: 'Chính sách bán hàng, quy trình đặt hàng' },
      ]
    },
    {
      name: 'Dịch vụ',
      color: '#a855f7',
      labels: [
        { name: 'Thái độ nhân viên', def: 'Phản hồi về thái độ phục vụ, giao tiếp' },
        { name: 'Tốc độ xử lý', def: 'Tốc độ phản hồi, xử lý đơn hàng, khiếu nại' },
        { name: 'Hỗ trợ kỹ thuật', def: 'Hỗ trợ kỹ thuật, tư vấn sử dụng sản phẩm' },
        { name: 'Chăm sóc khách hàng', def: 'Chăm sóc sau bán hàng, follow-up' },
      ]
    },
    {
      name: 'Đối thủ',
      color: '#ef4444',
      labels: [
        { name: 'So sánh đối thủ', def: 'So sánh với sản phẩm/dịch vụ đối thủ cạnh tranh' },
        { name: 'Sản phẩm đối thủ', def: 'Đề cập cụ thể sản phẩm đối thủ' },
        { name: 'Giá đối thủ', def: 'So sánh giá với đối thủ cạnh tranh' },
      ]
    },
    {
      name: 'Khác',
      color: '#64748b',
      labels: [
        { name: 'Góp ý chung', def: 'Góp ý chung về doanh nghiệp, định hướng' },
        { name: 'Khen ngợi', def: 'Phản hồi tích cực, khen ngợi, hài lòng' },
        { name: 'Khác', def: 'Các phản hồi không thuộc nhóm nào ở trên' },
      ]
    },
  ];

  function render() {
    const app = document.getElementById('app');
    app.innerHTML = `
      <div class="page-header">
        <h2>🔬 Pipeline</h2>
        <p>Xem chi tiết luồng xử lý phân loại phản hồi</p>
      </div>

      <!-- Pipeline Flow Diagram -->
      <div class="card animate-in mb-6">
        <div class="card-header">
          <span class="card-title"><span class="icon">⚙️</span> Luồng xử lý</span>
        </div>
        <div class="pipeline-flow" id="pipeline-flow">
          ${renderPipelineFlow()}
        </div>
        <!-- Node detail -->
        <div id="pipeline-node-detail" class="hidden" style="margin-top:16px;padding:16px;background:var(--bg-tertiary);border-radius:var(--radius-md);border:1px solid var(--border);">
        </div>
      </div>

      <!-- Label Hierarchy -->
      <div class="card animate-in animate-in-delay-1 mb-6">
        <div class="card-header">
          <span class="card-title"><span class="icon">🏷️</span> Phân cấp nhãn (20 nhãn)</span>
          <span class="text-muted" style="font-size:12px;">Nhấn vào nhãn để xem định nghĩa</span>
        </div>
        <div class="label-grid">
          ${renderLabelHierarchy()}
        </div>
      </div>

      <!-- Label Detail Modal -->
      <div id="label-detail" class="hidden" style="margin-bottom:20px;">
        <div class="card animate-in">
          <div class="card-header">
            <span class="card-title" id="label-detail-name">—</span>
            <button class="btn btn-ghost btn-sm" onclick="document.getElementById('label-detail').classList.add('hidden')">✕</button>
          </div>
          <div id="label-detail-body"></div>
        </div>
      </div>

      <!-- Keywords & Brands -->
      <div class="grid-2">
        <div class="card animate-in animate-in-delay-2">
          <div class="card-header">
            <span class="card-title"><span class="icon">🔑</span> Từ khóa theo nhãn</span>
            <button class="btn btn-ghost btn-sm" onclick="PipelinePage.loadKeywords()">🔄</button>
          </div>
          <div id="keywords-list" style="max-height:400px;overflow-y:auto;">
            <div class="text-center text-muted" style="padding:20px;"><span class="spinner"></span></div>
          </div>
        </div>

        <div class="card animate-in animate-in-delay-3">
          <div class="card-header">
            <span class="card-title"><span class="icon">🏢</span> Thương hiệu đã biết</span>
          </div>
          <div id="brands-list" style="max-height:400px;overflow-y:auto;">
            <div class="text-center text-muted" style="padding:20px;"><span class="spinner"></span></div>
          </div>
        </div>
      </div>
    `;

    loadData();
  }

  function renderPipelineFlow() {
    return PIPELINE_NODES.map((node, i) => {
      const arrow = i < PIPELINE_NODES.length - 1
        ? '<div class="pipeline-arrow"></div>'
        : '';
      return `
        <div class="pipeline-node" data-node="${node.id}" onclick="PipelinePage.selectNode('${node.id}')">
          <div class="pipeline-node-icon">${node.icon}</div>
          <div class="pipeline-node-label">${node.label}</div>
          <div class="pipeline-node-status">${node.desc.substring(0, 30)}...</div>
        </div>
        ${arrow}
      `;
    }).join('');
  }

  function selectNode(nodeId) {
    _selectedNode = nodeId;
    const node = PIPELINE_NODES.find(n => n.id === nodeId);
    if (!node) return;

    // Highlight active
    document.querySelectorAll('.pipeline-node').forEach(el => {
      el.classList.toggle('active', el.dataset.node === nodeId);
    });

    const detail = document.getElementById('pipeline-node-detail');
    if (detail) {
      detail.classList.remove('hidden');
      detail.innerHTML = `
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;">
          <span style="font-size:28px;">${node.icon}</span>
          <div>
            <div style="font-size:16px;font-weight:700;">${node.label}</div>
            <div class="text-secondary" style="font-size:13px;">Bước ${PIPELINE_NODES.indexOf(node) + 1} / ${PIPELINE_NODES.length}</div>
          </div>
        </div>
        <p style="font-size:14px;color:var(--text-secondary);line-height:1.7;">${node.desc}</p>
        ${renderNodeDetails(nodeId)}
      `;
    }
  }

  function renderNodeDetails(nodeId) {
    const details = {
      input: `
        <div class="mt-4">
          <div class="result-row"><span class="result-key">Định dạng</span><span class="result-value">.xlsx (Excel)</span></div>
          <div class="result-row"><span class="result-key">Tự động phát hiện</span><span class="result-value">Header row, cột phản hồi</span></div>
          <div class="result-row"><span class="result-key">Encoding</span><span class="result-value">UTF-8 / UTF-16</span></div>
        </div>
      `,
      header: `
        <div class="mt-4">
          <div class="result-row"><span class="result-key">Thuật toán</span><span class="result-value">Fuzzy matching headers</span></div>
          <div class="result-row"><span class="result-key">Từ khóa tìm kiếm</span><span class="result-value">phản hồi, góp ý, nhận xét, feedback</span></div>
        </div>
      `,
      rag: `
        <div class="mt-4">
          <div class="result-row"><span class="result-key">Bước 1</span><span class="result-value">LLM trích xuất tên sản phẩm</span></div>
          <div class="result-row"><span class="result-key">Bước 2</span><span class="result-value">BM25 tìm kiếm trong catalog</span></div>
          <div class="result-row"><span class="result-key">Fallback</span><span class="result-value">Keyword matching</span></div>
        </div>
      `,
      classify: `
        <div class="mt-4">
          <div class="result-row"><span class="result-key">Số nhãn</span><span class="result-value">20 nhãn / 6 nhóm</span></div>
          <div class="result-row"><span class="result-key">Phương pháp</span><span class="result-value">LLM + Rule-based guardrails</span></div>
          <div class="result-row"><span class="result-key">Output</span><span class="result-value">Multi-label + sentiment + brand</span></div>
        </div>
      `,
      output: `
        <div class="mt-4">
          <div class="result-row"><span class="result-key">Định dạng</span><span class="result-value">.xlsx (Excel)</span></div>
          <div class="result-row"><span class="result-key">Nội dung</span><span class="result-value">Cột gốc + cột phân loại mới</span></div>
          <div class="result-row"><span class="result-key">Upload</span><span class="result-value">Tự động upload lên SharePoint</span></div>
        </div>
      `,
    };
    return details[nodeId] || '';
  }

  function renderLabelHierarchy() {
    return LABEL_HIERARCHY.map(group => `
      <div class="label-group">
        <div class="label-group-header" style="border-left:3px solid ${group.color};">
          ${group.name}
          <span class="text-muted" style="font-size:10px;margin-left:8px;">${group.labels.length} nhãn</span>
        </div>
        <div class="label-group-items">
          ${group.labels.map(l => `
            <div class="label-item" style="cursor:pointer;" onclick="PipelinePage.showLabelDetail('${escAttr(l.name)}', '${escAttr(l.def)}', '${group.name}')">
              <div class="label-check" style="border-color:${group.color};background:${group.color}20;">
                <span style="color:${group.color};font-size:10px;">●</span>
              </div>
              <span>${esc(l.name)}</span>
            </div>
          `).join('')}
        </div>
      </div>
    `).join('');
  }

  function showLabelDetail(name, definition, group) {
    const el = document.getElementById('label-detail');
    const nameEl = document.getElementById('label-detail-name');
    const body = document.getElementById('label-detail-body');
    if (!el || !body) return;

    el.classList.remove('hidden');
    nameEl.innerHTML = `🏷️ ${esc(name)}`;
    body.innerHTML = `
      <div class="result-row">
        <span class="result-key">Nhóm</span>
        <span class="badge badge-blue">${esc(group)}</span>
      </div>
      <div class="result-row">
        <span class="result-key">Định nghĩa</span>
        <span class="result-value" style="text-align:left;color:var(--text-secondary);">${esc(definition)}</span>
      </div>
    `;
    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  async function loadData() {
    await Promise.allSettled([loadKeywords(), loadBrands()]);
  }

  async function loadKeywords() {
    const el = document.getElementById('keywords-list');
    if (!el) return;

    try {
      const data = await API.getKeywords();
      _keywords = data;

      if (!data || Object.keys(data).length === 0) {
        el.innerHTML = '<p class="text-muted text-center" style="padding:16px;">Không có từ khóa</p>';
        return;
      }

      const entries = typeof data === 'object' && !Array.isArray(data) ? Object.entries(data) : [];
      if (entries.length === 0 && Array.isArray(data)) {
        el.innerHTML = data.map(k => `<span class="chip" style="margin:3px;">${esc(String(k))}</span>`).join('');
        return;
      }

      el.innerHTML = entries.map(([label, keywords]) => `
        <div style="margin-bottom:12px;">
          <div style="font-size:12px;font-weight:600;color:var(--text-secondary);margin-bottom:6px;">${esc(label)}</div>
          <div style="display:flex;flex-wrap:wrap;gap:4px;">
            ${(Array.isArray(keywords) ? keywords : [keywords]).map(k => `<span class="chip">${esc(String(k))}</span>`).join('')}
          </div>
        </div>
      `).join('');
    } catch (e) {
      el.innerHTML = '<p class="text-muted text-center" style="padding:16px;">Không thể tải từ khóa</p>';
    }
  }

  async function loadBrands() {
    const el = document.getElementById('brands-list');
    if (!el) return;

    try {
      const data = await API.getBrands();
      const brands = Array.isArray(data) ? data : (data.brands || []);

      if (brands.length === 0) {
        el.innerHTML = '<p class="text-muted text-center" style="padding:16px;">Không có dữ liệu thương hiệu</p>';
        return;
      }

      el.innerHTML = `
        <div style="display:flex;flex-wrap:wrap;gap:6px;padding:4px 0;">
          ${brands.map(b => {
            const name = typeof b === 'string' ? b : b.name || b.brand;
            return `<span class="chip clickable" style="font-size:12px;">${esc(name)}</span>`;
          }).join('')}
        </div>
      `;
    } catch (e) {
      el.innerHTML = '<p class="text-muted text-center" style="padding:16px;">Không thể tải thương hiệu</p>';
    }
  }

  function esc(s) {
    if (s == null) return '';
    const d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
  }

  function escAttr(s) {
    return String(s || '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
  }

  function destroy() {}

  return { render, destroy, selectNode, showLabelDetail, loadKeywords };
})();
