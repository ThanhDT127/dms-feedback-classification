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

  let _labelHierarchy = [];

  const GROUP_COLORS = {
    'Sản phẩm': '#3b82f6',
    'Yêu cầu công cụ BH': '#22c55e',
    'Giá, cơ chế RD': '#f59e0b',
    'Dịch vụ': '#a855f7',
    'Hàng giả': '#ec4899',
    'Website': '#06b6d4',
    'Đối thủ cạnh tranh': '#ef4444',
    'Tin trung lập': '#64748b'
  };

  async function loadLabelHierarchy() {
    try {
      const data = await API.getLabels();
      const groupsMap = {};
      const minorOrder = data.minor_order || Object.keys(data.minor_to_major || {});
      const minorToMajor = data.minor_to_major || {};
      const labelDefs = data.label_definitions || {};

      for (const minor of minorOrder) {
        const major = minorToMajor[minor];
        if (major) {
          if (!groupsMap[major]) {
            groupsMap[major] = [];
          }
          groupsMap[major].push({
            name: minor,
            def: labelDefs[minor] || ''
          });
        }
      }

      _labelHierarchy = Object.entries(groupsMap).map(([name, labels]) => ({
        name,
        color: GROUP_COLORS[name] || '#64748b',
        labels
      }));
    } catch (e) {
      console.error("Failed to load pipeline labels", e);
      _labelHierarchy = [
        { name: 'Sản phẩm', color: '#3b82f6', labels: [
          { name: 'Báo lỗi', def: 'Sản phẩm vật lý bị lỗi kỹ thuật, hỏng, cháy, không sáng, không hoạt động, lệch ren, rò điện, nứt vỡ thực tế. KHÔNG dùng cho phàn nàn về thiết kế, kích thước, độ dày/mỏng vỏ nhựa/thanh đồng, phích to vướng (các phàn nàn thiết kế này thuộc Y/c cải tiến).' },
          { name: 'Báo CL tốt', def: 'Khen chất lượng sản phẩm tốt, bền, sáng tốt, ổn định, khách hài lòng.' },
          { name: 'Y/c cải tiến', def: 'Yêu cầu chỉnh sửa hoặc phàn nàn về thiết kế, kích thước, tính năng, độ dày/mỏng của vỏ nhựa/thanh đồng, bao bì, mẫu mã, phích to vướng, kết cấu của sản phẩm HIỆN CÓ đang bán (như vỏ mỏng cần làm dày hơn, phích to cần làm nhỏ lại, thanh đồng mỏng cần làm dày).' },
          { name: 'Đề xuất SPM', def: 'Đề xuất sản xuất SP MỚI chưa có: mã mới, kích thước mới, loại mới (\'ra thêm\', \'sản xuất thêm\', \'thêm loại\', \'có thêm\', \'mã mới\').' }
        ]},
        { name: 'Yêu cầu công cụ BH', color: '#22c55e', labels: [
          { name: 'Bảng giá, Catalogue', def: 'Yêu cầu cung cấp bảng giá, báo giá, catalogue, tài liệu bán hàng.' },
          { name: 'Bảng biển', def: 'Yêu cầu hỗ trợ biển hiệu, biển quảng cáo, bảng hiệu cửa hàng, POSM dạng biển.' },
          { name: 'Kệ bóng, thử đèn,…', def: 'Yêu cầu kệ trưng bày, kệ bóng, tủ thử bóng, bộ test đèn, dụng cụ demo.' },
          { name: 'Khác', def: 'Yêu cầu công cụ BH CỤ THỂ khác (áo đồng phục, tờ rơi, sổ tay, POSM) mà KHÔNG phải bảng giá, biển hiệu, hay kệ. KHÔNG dùng làm nhãn mặc định.' }
        ]},
        { name: 'Giá, cơ chế RD', color: '#f59e0b', labels: [
          { name: 'Tốt/ ko tốt', def: 'Nhận xét về giá/cơ chế của RẠNG ĐÔNG: giá tốt/cao/rẻ, khó bán, dễ bán, cạnh tranh. Từ khóa: \'giá rẻ\', \'giá cao\', \'đắt hơn\', \'chiết khấu\', \'cơ chế\', \'khó bán\'.' },
          { name: 'Trả thưởng', def: 'Nhắc CỤ THỂ đến tiền thưởng, quay số, gói quay, c2td, trả thưởng chậm của Rạng Đông.' },
          { name: 'Đề xuất', def: 'ĐỀ NGHỊ thay đổi chính sách giá, cơ chế, chiết khấu, khuyến mãi CHUNG của RĐ. Khác Trả thưởng (hỏi thưởng cụ thể).' }
        ]},
        { name: 'Dịch vụ', color: '#a855f7', labels: [
          { name: 'Bảo hành', def: 'Nói về QUY TRÌNH bảo hành, đổi trả, thời gian BH, hậu mãi — tức DỊCH VỤ. Khác Báo lỗi (nói về SP hỏng).' },
          { name: 'HTPP', def: 'Hệ thống phân phối: xung đột kênh C1/C2, tràn vùng, nhà phân phối, đại lý.' },
          { name: 'Hàng hoá', def: 'Logistics: tồn kho, thiếu hàng, giao hàng chậm, vận chuyển, đóng gói.' }
        ]},
        { name: 'Hàng giả', color: '#ec4899', labels: [{ name: 'Hàng giả', def: 'Nghi ngờ hàng GIẢ/NHÁI, giả mạo thương hiệu. KHÔNG dùng cho SP kém CL chính hãng (đó là Báo lỗi).' }]},
        { name: 'Website', color: '#06b6d4', labels: [{ name: 'Website', def: 'Lỗi PHẦN MỀM: web, app, portal, DMS, đăng nhập, hệ thống chậm/đơ. KHÔNG dùng cho lỗi SP vật lý.' }]},
        { name: 'Đối thủ cạnh tranh', color: '#ef4444', labels: [
          { name: 'Hãng', def: 'Có nhắc đến hãng khác ngoài Rạng Đông (đối thủ cạnh tranh). Ghi tên hãng vào brand.' },
          { name: 'Hoạt động', def: 'Hoạt động marketing, trưng bày, tặng kệ, event, roadshow, tài trợ CỦA ĐỐO THỦ.' },
          { name: 'CTKM, giá, cơ chế', def: 'Giá bán, khuyến mãi, chiết khấu, chính sách bán hàng CỦA ĐỐI THỦ cạnh tranh.' },
          { name: 'TT SP', def: 'Thông tin sản phẩm, mẫu mã, tính năng, thông số, catalogue CỦA ĐỐI THỦ cạnh tranh.' }
        ]},
        { name: 'Tin trung lập', color: '#64748b', labels: [{ name: 'Tin trung lập', def: 'Câu hoàn toàn trung tính, không khen/chê/đề xuất/yêu cầu gì. CHỈ gán khi không có nhãn nào khác.' }]}
      ];
    }
  }

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
        <div class="label-grid" id="pipeline-label-grid">
          <div class="text-center text-muted" style="padding:20px;grid-column: span 3;"><span class="spinner"></span> Đang tải phân cấp nhãn...</div>
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
    return _labelHierarchy.map(group => `
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
    await loadLabelHierarchy();
    const grid = document.getElementById('pipeline-label-grid');
    if (grid) {
      grid.innerHTML = renderLabelHierarchy();
    }
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
