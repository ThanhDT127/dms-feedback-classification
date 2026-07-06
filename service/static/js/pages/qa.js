/* ============================================================
   Hướng dẫn sử dụng (HDSD) — User Guide Page
   Replaces Visual QA page. Route: #qa, module: QAPage
   ============================================================ */

window.QAPage = (() => {

  // === Guide content sections (tasks 3.2-3.7) ===

  const GUIDE_SECTIONS = [
    {
      id: 'overview',
      icon: '🏠',
      title: 'Tổng quan hệ thống',
      content: `
        <div class="hdsd-content">
          <p><strong>Phân loại phản hồi tiếp thị</strong> là hệ thống tự động phân loại phản hồi khách hàng, giúp phân tích và xử lý dữ liệu tiếp thị nhanh chóng, chính xác.</p>
          <p>Hệ thống hoạt động theo 4 bước xử lý chính:</p>
          <div class="hdsd-step">
            <span class="hdsd-step-num">1</span>
            <div class="hdsd-step-text"><strong>Quét dữ liệu</strong> — Hệ thống tự động quét file Excel từ SharePoint Cloud hoặc nhận file tải lên trực tiếp.</div>
          </div>
          <div class="hdsd-step">
            <span class="hdsd-step-num">2</span>
            <div class="hdsd-step-text"><strong>Trích xuất sản phẩm</strong> — Sử dụng RAG (Retrieval-Augmented Generation) để so khớp nội dung phản hồi với danh mục sản phẩm bằng từ khóa.</div>
          </div>
          <div class="hdsd-step">
            <span class="hdsd-step-num">3</span>
            <div class="hdsd-step-text"><strong>Phân loại</strong> — LLM (Gemini) phân tích nội dung và gán nhãn phân loại vấn đề theo cấu hình prompt hệ thống.</div>
          </div>
          <div class="hdsd-step">
            <span class="hdsd-step-num">4</span>
            <div class="hdsd-step-text"><strong>Báo cáo</strong> — Kết quả được xuất ra file Excel, đẩy lên SharePoint Cloud và gửi thông báo qua Teams/Email.</div>
          </div>
        </div>
      `
    },
    {
      id: 'classify',
      icon: '⚡',
      title: 'Hướng dẫn phân loại phản hồi',
      content: `
        <div class="hdsd-content">
          <p>Trang <strong>Phân loại</strong> hỗ trợ 3 chế độ phân loại:</p>

          <h4 style="color:var(--text-primary);font-size:13px;margin:16px 0 8px;">📝 Phân loại văn bản đơn lẻ</h4>
          <ol>
            <li>Chuyển sang tab <strong>"Văn bản"</strong></li>
            <li>Nhập nội dung phản hồi vào ô văn bản</li>
            <li>Chọn model AI (Gemini Flash / Pro)</li>
            <li>Nhấn <strong>"⚡ Phân loại"</strong></li>
            <li>Kết quả hiển thị ngay bên dưới: nhãn phân loại, sản phẩm liên quan, mức độ tin cậy</li>
          </ol>

          <h4 style="color:var(--text-primary);font-size:13px;margin:16px 0 8px;">📄 Phân loại file Excel</h4>
          <ol>
            <li>Chuyển sang tab <strong>"File"</strong></li>
            <li>Kéo thả hoặc chọn file <code>.xlsx</code> để tải lên</li>
            <li>Hệ thống tự động phát hiện cột nội dung (chứa chữ "Nội dung" hoặc "noi dung")</li>
            <li>Chọn các cột bổ sung muốn giữ lại trong kết quả</li>
            <li>Nhấn <strong>"Bắt đầu phân loại"</strong></li>
            <li>Theo dõi tiến trình real-time qua thanh tiến trình WebSocket</li>
          </ol>

          <h4 style="color:var(--text-primary);font-size:13px;margin:16px 0 8px;">📦 Xử lý hàng loạt (Batch)</h4>
          <ol>
            <li>Chuyển sang tab <strong>"Hàng loạt"</strong></li>
            <li>Thêm nhiều file vào hàng đợi</li>
            <li>Hệ thống xử lý tuần tự từng file, hiển thị kết quả live sau mỗi dòng</li>
            <li>Có thể <strong>tạm dừng / tiếp tục / hủy</strong> bất kỳ lúc nào</li>
          </ol>

          <p style="margin-top:12px;padding:10px 14px;background:rgba(59,130,246,0.05);border-radius:var(--radius-sm);border:1px solid rgba(59,130,246,0.15);font-size:12px;">
            💡 <strong>Mẹo:</strong> Khi đang phân loại, bạn có thể chuyển sang tab khác rồi quay lại — tiến trình không bị mất.
          </p>
        </div>
      `
    },
    {
      id: 'files',
      icon: '📂',
      title: 'Quản lý file',
      content: `
        <div class="hdsd-content">
          <p>Trang <strong>Quản lý file</strong> cho phép duyệt và xem trước file trong các thư mục hệ thống.</p>

          <h4 style="color:var(--text-primary);font-size:13px;margin:12px 0 8px;">📥 Tải file lên</h4>
          <ul>
            <li>Nhấn <strong>"📤 Tải file lên"</strong> hoặc kéo thả file <code>.xlsx</code> vào khu vực tải lên</li>
            <li>File phải chứa cột nội dung có tiêu đề chứa từ "Nội dung" hoặc "noi dung"</li>
            <li>Các cột thông tin bổ sung (Tên, Ngày, Mã phản hồi...) sẽ được giữ nguyên</li>
          </ul>

          <h4 style="color:var(--text-primary);font-size:13px;margin:12px 0 8px;">📁 Duyệt thư mục</h4>
          <ul>
            <li><strong>Đầu vào (Input)</strong> — File Excel gốc chờ phân loại</li>
            <li><strong>Kết quả (Output)</strong> — File đã phân loại xong</li>
            <li><strong>Lưu vết (Checkpoint)</strong> — Bản sao lưu tiến trình xử lý</li>
            <li><strong>Từ khóa (Keyword)</strong> — File cấu hình từ khóa sản phẩm</li>
            <li><strong>Mô hình (Model)</strong> — File cấu hình model AI</li>
          </ul>

          <h4 style="color:var(--text-primary);font-size:13px;margin:12px 0 8px;">☁️ Đồng bộ SharePoint</h4>
          <p>Nhấn <strong>"☁️ Đồng bộ SharePoint"</strong> để tải file từ SharePoint Cloud về hệ thống. Watcher tự động quét và đồng bộ theo chu kỳ.</p>

          <h4 style="color:var(--text-primary);font-size:13px;margin:12px 0 8px;">👁️ Xem trước file</h4>
          <p>Nhấn vào tên file hoặc nút 👁️ để xem trước nội dung trực tiếp. Hỗ trợ xem Excel (bảng), JSON, và file văn bản.</p>
        </div>
      `
    },
    {
      id: 'settings',
      icon: '⚙️',
      title: 'Cài đặt hệ thống',
      content: `
        <div class="hdsd-content">
          <p>Trang <strong>Cài đặt</strong> cho phép điều chỉnh cấu hình hệ thống phân loại.</p>

          <h4 style="color:var(--text-primary);font-size:13px;margin:12px 0 8px;">📝 System Prompt</h4>
          <p>Chỉnh sửa prompt hệ thống dùng để hướng dẫn AI phân loại. Prompt định nghĩa các nhãn phân loại, cách thức phân tích, và format kết quả đầu ra.</p>

          <h4 style="color:var(--text-primary);font-size:13px;margin:12px 0 8px;">🔑 Từ khóa sản phẩm</h4>
          <p>Quản lý danh sách từ khóa để RAG so khớp sản phẩm. Tổ chức theo nhóm, thêm/xóa/sắp xếp trực tiếp. Hỗ trợ tự động hoàn thành và phát hiện trùng lặp.</p>

          <h4 style="color:var(--text-primary);font-size:13px;margin:12px 0 8px;">🤖 Chọn Model AI</h4>
          <ul>
            <li><strong>Gemini Flash</strong> — Nhanh, phù hợp xử lý hàng loạt số lượng lớn</li>
            <li><strong>Gemini Pro</strong> — Chính xác hơn, phù hợp phân loại phức tạp</li>
          </ul>

          <h4 style="color:var(--text-primary);font-size:13px;margin:12px 0 8px;">🔔 Cấu hình thông báo</h4>
          <ul>
            <li><strong>Microsoft Teams</strong> — Nhập Webhook URL để nhận thông báo khi phân loại hoàn tất</li>
            <li><strong>Email</strong> — Cấu hình SMTP để gửi email kết quả</li>
          </ul>

          <h4 style="color:var(--text-primary);font-size:13px;margin:12px 0 8px;">📊 Nhãn phân loại</h4>
          <p>Xem và quản lý danh sách nhãn phân loại. Theo dõi lịch sử thay đổi nhãn qua timeline.</p>
        </div>
      `
    },
    {
      id: 'faq',
      icon: '❓',
      title: 'FAQ — Câu hỏi thường gặp',
      content: `
        <div class="hdsd-content">
          <div class="hdsd-faq-item">
            <div class="hdsd-faq-q">💬 File không upload được, báo lỗi "Chỉ chấp nhận file .xlsx"?</div>
            <div class="hdsd-faq-a">Hệ thống chỉ hỗ trợ file Excel định dạng <code>.xlsx</code>. File <code>.xls</code> (Excel 97-2003) cần được chuyển đổi sang <code>.xlsx</code> trước khi tải lên. Mở file trong Excel → Save As → chọn định dạng <code>.xlsx</code>.</div>
          </div>
          <div class="hdsd-faq-item">
            <div class="hdsd-faq-q">💬 Phân loại kết quả sai hoặc không chính xác?</div>
            <div class="hdsd-faq-a">Kiểm tra và cập nhật System Prompt trong phần Cài đặt. Đảm bảo các nhãn phân loại được mô tả rõ ràng. Thử chuyển sang model <strong>Gemini Pro</strong> để cải thiện độ chính xác. Bổ sung từ khóa sản phẩm liên quan để tăng khả năng so khớp.</div>
          </div>
          <div class="hdsd-faq-item">
            <div class="hdsd-faq-q">💬 Lỗi kết nối "Failed to fetch" hoặc "Network Error"?</div>
            <div class="hdsd-faq-a">Kiểm tra kết nối mạng của bạn. Đảm bảo server backend đang chạy. Nếu dùng VPN, thử tắt VPN và kết nối lại. Kiểm tra API key Gemini trong cấu hình hệ thống.</div>
          </div>
          <div class="hdsd-faq-item">
            <div class="hdsd-faq-q">💬 Thời gian xử lý file lớn mất bao lâu?</div>
            <div class="hdsd-faq-a">Thời gian phụ thuộc vào số lượng dòng dữ liệu và model AI được chọn. Gemini Flash: ~0.5-1s/dòng. Gemini Pro: ~1-2s/dòng. File 1000 dòng mất khoảng 10-30 phút. Hệ thống hỗ trợ checkpoint nên không mất tiến trình nếu bị gián đoạn.</div>
          </div>
          <div class="hdsd-faq-item">
            <div class="hdsd-faq-q">💬 Checkpoint hoạt động như thế nào?</div>
            <div class="hdsd-faq-a">Hệ thống tự động lưu checkpoint sau mỗi batch xử lý (thường 10-20 dòng). Nếu quá trình bị gián đoạn (mất mạng, restart server), khi chạy lại hệ thống sẽ tiếp tục từ checkpoint cuối cùng, không xử lý lại các dòng đã hoàn thành.</div>
          </div>
          <div class="hdsd-faq-item">
            <div class="hdsd-faq-q">💬 SharePoint không đồng bộ được?</div>
            <div class="hdsd-faq-a">Kiểm tra cấu hình Azure AD (Client ID, Tenant ID, Client Secret) trong file <code>.env</code>. Đảm bảo ứng dụng Azure có quyền <code>Sites.ReadWrite.All</code>. Thử nhấn "☁️ Đồng bộ SharePoint" thủ công để xem chi tiết lỗi.</div>
          </div>
          <div class="hdsd-faq-item">
            <div class="hdsd-faq-q">💬 Làm sao để thêm nhãn phân loại mới?</div>
            <div class="hdsd-faq-a">Vào phần <strong>Cài đặt → System Prompt</strong>, thêm nhãn mới vào danh sách nhãn trong prompt. Mô tả rõ ràng tiêu chí phân loại cho nhãn mới. Sau khi lưu, nhãn mới sẽ được áp dụng cho các lần phân loại tiếp theo.</div>
          </div>
        </div>
      `
    }
  ];

  // === Search text cache for filtering ===
  let _searchableCache = null;

  function _buildSearchCache() {
    _searchableCache = GUIDE_SECTIONS.map(s => ({
      id: s.id,
      text: (s.title + ' ' + s.content.replace(/<[^>]+>/g, '')).toLowerCase()
    }));
  }

  // === Render (task 4.1) ===

  function render() {
    const app = document.getElementById('app');
    if (!app) return;

    app.innerHTML = `
      <div class="page-header animate-in">
        <h2>📖 Hướng dẫn sử dụng</h2>
        <p>Tài liệu hướng dẫn sử dụng hệ thống Phân loại phản hồi tiếp thị</p>
      </div>

      <!-- Search bar -->
      <div class="hdsd-search-bar animate-in animate-in-delay-1">
        <span class="hdsd-search-icon">🔍</span>
        <input type="text" id="hdsd-search" placeholder="Tìm kiếm hướng dẫn..." oninput="QAPage.handleSearch(this.value)">
      </div>

      <!-- No results message -->
      <div id="hdsd-no-results" class="hdsd-no-results" style="display:none;">
        <div class="icon">🔍</div>
        <p>Không tìm thấy kết quả phù hợp</p>
      </div>

      <!-- Guide sections -->
      <div id="hdsd-sections">
        ${GUIDE_SECTIONS.map((section, i) => `
          <div class="hdsd-section${section.id === 'overview' ? ' open' : ''} animate-in" style="animation-delay:${(i + 2) * 60}ms" data-id="${section.id}">
            <div class="hdsd-section-header" onclick="QAPage.toggleSection('${section.id}')">
              <h3><span>${section.icon}</span> ${section.title}</h3>
              <span class="hdsd-chevron">▼</span>
            </div>
            <div class="hdsd-section-body">
              ${section.content}
            </div>
          </div>
        `).join('')}
      </div>
    `;

    _buildSearchCache();
  }

  // === Toggle section (task 4.2) ===

  function toggleSection(sectionId) {
    const el = document.querySelector(`.hdsd-section[data-id="${sectionId}"]`);
    if (el) {
      el.classList.toggle('open');
    }
  }

  // === Search functionality (tasks 4.3-4.5) ===

  function handleSearch(query) {
    const trimmed = query.trim().toLowerCase();
    const sections = document.querySelectorAll('.hdsd-section');
    const noResults = document.getElementById('hdsd-no-results');

    // Reset if empty
    if (!trimmed) {
      sections.forEach(s => {
        s.style.display = '';
        // Restore original content (remove highlights)
        const id = s.getAttribute('data-id');
        const section = GUIDE_SECTIONS.find(gs => gs.id === id);
        if (section) {
          const body = s.querySelector('.hdsd-section-body');
          if (body) body.innerHTML = section.content;
        }
      });
      // Reset: only overview open
      sections.forEach(s => {
        if (s.getAttribute('data-id') === 'overview') {
          s.classList.add('open');
        } else {
          s.classList.remove('open');
        }
      });
      if (noResults) noResults.style.display = 'none';
      return;
    }

    if (!_searchableCache) _buildSearchCache();

    let matchCount = 0;
    sections.forEach(s => {
      const id = s.getAttribute('data-id');
      const cached = _searchableCache.find(c => c.id === id);
      const section = GUIDE_SECTIONS.find(gs => gs.id === id);

      if (cached && cached.text.includes(trimmed)) {
        s.style.display = '';
        s.classList.add('open');
        matchCount++;

        // Highlight matching text in body
        if (section) {
          const body = s.querySelector('.hdsd-section-body');
          if (body) body.innerHTML = highlightText(section.content, trimmed);
        }
      } else {
        s.style.display = 'none';
        s.classList.remove('open');
      }
    });

    if (noResults) {
      noResults.style.display = matchCount === 0 ? '' : 'none';
    }
  }

  // === Highlight matching text (task 4.4) ===

  function highlightText(html, query) {
    if (!query) return html;

    // Only highlight text content, not HTML tags
    const parts = html.split(/(<[^>]+>)/);
    return parts.map(part => {
      // If it's an HTML tag, keep as-is
      if (part.startsWith('<')) return part;
      // Highlight matches in text content
      const regex = new RegExp(`(${escapeRegex(query)})`, 'gi');
      return part.replace(regex, '<mark>$1</mark>');
    }).join('');
  }

  function escapeRegex(str) {
    return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  // === Module export (task 4.6) ===

  return { render, toggleSection, handleSearch };
})();
