/* ============================================================
   Feedback Analytics Page — authenticated business analytics
   ============================================================ */

window.AnalyticsPage = (() => {
  const DEFAULT_PAGE_SIZE = 25;
  const _state = {
    filters: { from: '', to: '', compare_from: '', compare_to: '' },
    issueFilters: { source: '', unit: '', label: '', product: '', status: '' },
    issuePage: 1,
    issues: null,
    units: [],
    requestId: 0,
  };

  function render() {
    const app = document.getElementById('app');
    if (!app) return;
    app.innerHTML = `
      <div class="page-header">
        <h2>📊 Phân tích phản hồi</h2>
        <p>Chỉ số nghiệp vụ từ dữ liệu phản hồi đã được phân loại</p>
      </div>

      <section class="card analytics-filter-card" aria-label="Bộ lọc thời gian phân tích">
        <div class="card-header">
          <span class="card-title"><span class="icon">🗓️</span> Khoảng thời gian</span>
          <button class="btn btn-ghost btn-sm" type="button" onclick="AnalyticsPage.refresh()">🔄 Làm mới</button>
        </div>
        <div class="analytics-filter-grid">
          ${dateField('analytics-date-from', 'Từ ngày', _state.filters.from)}
          ${dateField('analytics-date-to', 'Đến ngày', _state.filters.to)}
          ${dateField('analytics-compare-from', 'So sánh từ ngày', _state.filters.compare_from)}
          ${dateField('analytics-compare-to', 'So sánh đến ngày', _state.filters.compare_to)}
          <div class="analytics-filter-actions">
            <button class="btn btn-primary btn-sm" type="button" onclick="AnalyticsPage.applyFilters()">Áp dụng</button>
            <button class="btn btn-ghost btn-sm" type="button" onclick="AnalyticsPage.resetFilters()">Đặt lại</button>
          </div>
        </div>
        <p class="analytics-filter-hint">Để trống để xem toàn bộ dữ liệu. Mỗi khoảng thời gian phải có đủ ngày bắt đầu và kết thúc.</p>
        <div id="analytics-filter-error" class="analytics-inline-alert" role="alert" hidden></div>
      </section>

      <div id="analytics-page-status" class="analytics-page-status" aria-live="polite"></div>

      <section aria-labelledby="analytics-overview-title">
        <div class="section-heading"><h3 id="analytics-overview-title">Tổng quan</h3><span class="text-muted">KPI tính theo Mã vấn đề khác rỗng</span></div>
        <div id="analytics-overview" class="stat-grid">${renderMetricSkeletons(8)}</div>
      </section>

      <div class="grid-2 analytics-panel-grid">
        ${panel('analytics-source-title', '📡 Nguồn thông tin', 'analytics-sources')}
        ${panel('analytics-unit-title', '🏢 Theo đơn vị', 'analytics-units')}
        ${panel('analytics-group-title', '🏷️ Nhóm vấn đề & cảm xúc', 'analytics-groups')}
        ${panel('analytics-product-title', '📦 Sản phẩm & chất lượng', 'analytics-products')}
      </div>

      <section class="card" aria-labelledby="analytics-quality-title">
        <div class="card-header"><span id="analytics-quality-title" class="card-title">🔎 Chất lượng dữ liệu</span></div>
        <div id="analytics-data-quality" class="analytics-panel-body">${renderPanelLoading()}</div>
      </section>

      <section class="card" aria-labelledby="analytics-issues-title">
        <div class="card-header"><span id="analytics-issues-title" class="card-title">📋 Chi tiết vấn đề</span></div>
        <div class="analytics-issue-filter-grid" aria-label="Bộ lọc chi tiết vấn đề">
          ${textField('analytics-issue-source', 'Nguồn', _state.issueFilters.source)}
          ${textField('analytics-issue-unit', 'Đơn vị', _state.issueFilters.unit)}
          ${textField('analytics-issue-label', 'Nhãn', _state.issueFilters.label)}
          ${textField('analytics-issue-product', 'Sản phẩm', _state.issueFilters.product)}
          ${textField('analytics-issue-status', 'Trạng thái', _state.issueFilters.status)}
          <div class="analytics-filter-actions"><button class="btn btn-primary btn-sm" type="button" onclick="AnalyticsPage.applyIssueFilters()">Lọc</button><button class="btn btn-ghost btn-sm" type="button" onclick="AnalyticsPage.clearIssueFilters()">Bỏ lọc</button></div>
        </div>
        <div id="analytics-issues" class="analytics-panel-body">${renderPanelLoading()}</div>
      </section>
    `;
    refresh();
  }

  function panel(titleId, title, bodyId) {
    return `<section class="card" aria-labelledby="${titleId}"><div class="card-header"><span id="${titleId}" class="card-title">${title}</span></div><div id="${bodyId}" class="analytics-panel-body">${renderPanelLoading()}</div></section>`;
  }

  function dateField(id, label, value) {
    return `<label class="analytics-field" for="${id}"><span>${label}</span><input id="${id}" class="form-input" type="date" value="${escHtml(value)}"></label>`;
  }

  function textField(id, label, value) {
    return `<label class="analytics-field" for="${id}"><span>${label}</span><input id="${id}" class="form-input" value="${escHtml(value)}"></label>`;
  }

  function renderMetricSkeletons(count) {
    return Array(count).fill(0).map((_, index) => `<div class="stat-card animate-in animate-in-delay-${(index % 5) + 1}"><div class="skeleton skeleton-text xs" style="margin-bottom:12px;"></div><div class="skeleton skeleton-text short" style="height:28px;margin-bottom:8px;"></div><div class="skeleton skeleton-text xs"></div></div>`).join('');
  }

  function renderPanelLoading() {
    return '<div class="text-muted" style="padding:16px 0;"><span class="spinner"></span> Đang tải dữ liệu...</div>';
  }

  function readGlobalFilters() {
    return {
      from: document.getElementById('analytics-date-from')?.value || '',
      to: document.getElementById('analytics-date-to')?.value || '',
      compare_from: document.getElementById('analytics-compare-from')?.value || '',
      compare_to: document.getElementById('analytics-compare-to')?.value || '',
    };
  }

  function validateRanges(filters) {
    for (const [from, to, label] of [[filters.from, filters.to, 'Khoảng thời gian chính'], [filters.compare_from, filters.compare_to, 'Khoảng thời gian so sánh']]) {
      if (Boolean(from) !== Boolean(to)) return `${label} cần có đủ ngày bắt đầu và kết thúc.`;
      if (from && from > to) return `${label} có ngày kết thúc trước ngày bắt đầu.`;
    }
    return null;
  }

  function applyFilters() {
    const filters = readGlobalFilters();
    const error = validateRanges(filters);
    setFilterError(error);
    if (error) return;
    _state.filters = filters;
    _state.issuePage = 1;
    refresh();
  }

  function resetFilters() {
    _state.filters = { from: '', to: '', compare_from: '', compare_to: '' };
    const inputIds = { from: 'analytics-date-from', to: 'analytics-date-to', compare_from: 'analytics-compare-from', compare_to: 'analytics-compare-to' };
    Object.entries(inputIds).forEach(([key, id]) => {
      const input = document.getElementById(id);
      if (input) input.value = _state.filters[key];
    });
    _state.issuePage = 1;
    setFilterError(null);
    refresh();
  }

  function setFilterError(message) {
    const element = document.getElementById('analytics-filter-error');
    if (!element) return;
    element.hidden = !message;
    element.textContent = message || '';
  }

  function readIssueFilters() {
    return {
      source: document.getElementById('analytics-issue-source')?.value.trim() || '',
      unit: document.getElementById('analytics-issue-unit')?.value.trim() || '',
      label: document.getElementById('analytics-issue-label')?.value.trim() || '',
      product: document.getElementById('analytics-issue-product')?.value.trim() || '',
      status: document.getElementById('analytics-issue-status')?.value.trim() || '',
    };
  }

  function applyIssueFilters() {
    _state.issueFilters = readIssueFilters();
    _state.issuePage = 1;
    loadIssues();
  }

  function clearIssueFilters() {
    _state.issueFilters = { source: '', unit: '', label: '', product: '', status: '' };
    for (const key of Object.keys(_state.issueFilters)) {
      const input = document.getElementById(`analytics-issue-${key}`);
      if (input) input.value = '';
    }
    _state.issuePage = 1;
    loadIssues();
  }

  function filterIssuesByUnit(index) {
    const unit = _state.units[index]?.label;
    if (!unit) return;
    _state.issueFilters.unit = unit;
    const input = document.getElementById('analytics-issue-unit');
    if (input) input.value = unit;
    _state.issuePage = 1;
    loadIssues();
  }

  function changeIssuePage(delta) {
    const totalPages = _state.issues?.total_pages || 0;
    const page = _state.issuePage + delta;
    if (page < 1 || (totalPages > 0 && page > totalPages)) return;
    _state.issuePage = page;
    loadIssues();
  }

  function globalQueryParams() {
    return { from: _state.filters.from || undefined, to: _state.filters.to || undefined };
  }

  function issueQueryParams() {
    return { ...globalQueryParams(), ..._state.issueFilters, page: _state.issuePage, page_size: DEFAULT_PAGE_SIZE };
  }

  async function refresh() {
    const requestId = ++_state.requestId;
    const status = document.getElementById('analytics-page-status');
    if (status) status.textContent = 'Đang cập nhật dữ liệu phân tích...';
    const overviewParams = { ...globalQueryParams(), compare_from: _state.filters.compare_from || undefined, compare_to: _state.filters.compare_to || undefined };
    const requests = {
      overview: API.getAnalyticsOverview(overviewParams),
      sources: API.getAnalyticsSources(globalQueryParams()),
      units: API.getAnalyticsUnits(globalQueryParams()),
      groups: API.getAnalyticsGroups(globalQueryParams()),
      products: API.getAnalyticsProducts(globalQueryParams()),
      dataQuality: API.getAnalyticsDataQuality(globalQueryParams()),
      issues: API.getAnalyticsIssues(issueQueryParams()),
    };
    const entries = Object.entries(requests);
    const settled = await Promise.allSettled(entries.map(([, request]) => request));
    if (requestId !== _state.requestId) return;
    const results = Object.fromEntries(entries.map(([key], index) => [key, settled[index]]));
    renderResult(results.overview, renderOverview, 'analytics-overview');
    renderResult(results.sources, renderSources, 'analytics-sources');
    renderResult(results.units, renderUnits, 'analytics-units');
    renderResult(results.groups, renderGroups, 'analytics-groups');
    renderResult(results.products, renderProducts, 'analytics-products');
    renderResult(results.dataQuality, renderDataQuality, 'analytics-data-quality');
    renderResult(results.issues, (element, data) => { _state.issues = data; renderIssues(element, data); }, 'analytics-issues');
    const failures = settled.filter(result => result.status === 'rejected').length;
    if (status) {
      status.textContent = failures ? `Không thể tải ${failures} phần dữ liệu. Các phần khác vẫn hiển thị.` : 'Dữ liệu phân tích đã được cập nhật.';
      status.classList.toggle('analytics-page-status-error', failures > 0);
    }
  }

  async function loadIssues() {
    const element = document.getElementById('analytics-issues');
    if (!element) return;
    const requestId = ++_state.requestId;
    element.innerHTML = renderPanelLoading();
    try {
      const data = await API.getAnalyticsIssues(issueQueryParams());
      if (requestId !== _state.requestId) return;
      _state.issues = data;
      renderIssues(element, data);
    } catch (error) {
      if (requestId !== _state.requestId) return;
      renderFailure(element, error, 'Không thể tải chi tiết vấn đề');
    }
  }

  function renderResult(result, renderer, elementId) {
    const element = document.getElementById(elementId);
    if (!element) return;
    if (result.status === 'fulfilled') renderer(element, result.value);
    else renderFailure(element, result.reason);
  }

  function renderFailure(element, error, title = 'Không thể tải dữ liệu') {
    element.innerHTML = `<div class="empty-state"><div class="empty-state-icon">⚠️</div><p class="empty-state-text">${escHtml(title)}</p><p class="empty-state-hint">${escHtml(error?.message || 'Vui lòng thử lại.')}</p></div>`;
  }

  function renderOverview(element, data) {
    const metrics = [
      ['total_issues', 'Tổng số vấn đề', '📌', 'blue', false],
      ['processed_issues', 'Vấn đề đã xử lý', '✅', 'green', false],
      ['label_coverage', 'Tỷ lệ phủ nhãn', '🏷️', 'purple', true],
      ['multi_label_rate', 'Phản hồi đa nhãn', '🔖', 'amber', true],
      ['sentiment_coverage', 'Hoàn thiện cảm xúc', '😊', 'orange', true],
      ['product_coverage', 'Nhận diện sản phẩm', '📦', 'blue', true],
      ['duplicate_issue_rate', 'Tỷ lệ vấn đề trùng', '♻️', 'red', true],
      ['model_accuracy', 'Độ chính xác mô hình', '🎯', 'gray', true],
    ];
    element.innerHTML = metrics.map(([key, label, icon, color, percent]) => {
      const metric = data?.[key] || {};
      const unavailable = metric.available === false;
      const hint = unavailable ? metric.reason || 'Chỉ số chưa khả dụng.' : metricHint(metric);
      return `<div class="stat-card ${color} animate-in" title="${escHtml(hint)}"><div class="stat-card-top"><div><div class="stat-card-value">${escHtml(unavailable ? '—' : formatMetricValue(metric.value, percent))}</div><div class="stat-card-label">${escHtml(label)}</div></div><div class="stat-card-icon">${icon}</div></div><div class="analytics-metric-hint">${escHtml(hint)}</div>${key === 'total_issues' ? renderComparison(metric.comparison) : ''}</div>`;
    }).join('');
  }

  function renderComparison(comparison) {
    if (!comparison) return '';
    if (!comparison.available) return `<div class="analytics-metric-hint">${escHtml(comparison.reason || 'Không thể so sánh kỳ.')}</div>`;
    const direction = comparison.direction === 'up' ? 'up' : comparison.direction === 'down' ? 'down' : 'unchanged';
    return `<div class="stat-card-delta ${direction}">${comparison.change_percent > 0 ? '+' : ''}${formatPercent(comparison.change_percent)} so với kỳ so sánh</div>`;
  }

  function renderSources(element, data) {
    const items = data?.items || [];
    if (!items.length) return renderEmpty(element, 'Chưa có dữ liệu phân bổ.');
    element.innerHTML = `<div class="analytics-chart-wrap"><canvas id="analytics-sources-chart"></canvas></div>${renderDistribution(items)}<p class="analytics-panel-note">Các vấn đề có thể thuộc nhiều nguồn; tổng tỷ trọng không nhất thiết bằng 100%.</p>`;
    Charts.createBarChart('analytics-sources-chart', items.map(item => item.label), items.map(item => item.issue_count), { label: 'Số vấn đề', chartOptions: { indexAxis: 'y' } });
  }

  function renderUnits(element, data) {
    const items = data?.items || [];
    if (!items.length) return renderEmpty(element, 'Chưa có dữ liệu phân bổ.');
    _state.units = items;
    element.innerHTML = `<div class="analytics-chart-wrap"><canvas id="analytics-units-chart"></canvas></div><div class="analytics-distribution" role="list">${items.map((item, index) => `<button class="analytics-distribution-row analytics-drilldown" type="button" onclick="AnalyticsPage.filterIssuesByUnit(${index})" role="listitem"><span class="analytics-distribution-label">${escHtml(item.label)}</span><span class="analytics-distribution-value">${formatNumber(item.issue_count)} (${formatPercent(item.percentage)})</span></button>`).join('')}</div><p class="analytics-panel-note">Chọn một đơn vị để lọc bảng chi tiết. Các vấn đề có thể thuộc nhiều đơn vị.</p>`;
    Charts.createDoughnutChart('analytics-units-chart', items.map(item => item.label), items.map(item => item.issue_count));
  }

  function renderDistribution(items) {
    return `<div class="analytics-distribution" role="list">${items.map(item => `<div class="analytics-distribution-row" role="listitem"><div class="analytics-distribution-label">${escHtml(item.label)}</div><div class="analytics-distribution-bar"><span style="width:${Math.min(Number(item.percentage) || 0, 100)}%"></span></div><div class="analytics-distribution-value">${formatNumber(item.issue_count)} (${formatPercent(item.percentage)})</div></div>`).join('')}</div>`;
  }

  function renderGroups(element, data) {
    const items = data?.items || [];
    if (!items.length) return renderEmpty(element, 'Chưa có nhóm vấn đề được phân loại.');
    element.innerHTML = `<div class="analytics-distribution" role="list">${items.map(item => {
      const known = Object.values(item.sentiment_counts || {}).reduce((sum, value) => sum + Number(value || 0), 0);
      const missing = Math.max(0, Number(item.issue_count || 0) - known);
      const text = [...Object.entries(item.sentiment_counts || {}).map(([label, count]) => `${label}: ${formatNumber(count)}`), `Chưa gán cảm xúc: ${formatNumber(missing)}`].join(' · ');
      return `<div class="analytics-distribution-row" role="listitem"><div class="analytics-distribution-label">${escHtml(item.label)}</div><div class="analytics-distribution-value">${formatNumber(item.issue_count)} · ${escHtml(text)}</div></div>`;
    }).join('')}</div><p class="analytics-panel-note">Phân bổ cảm xúc tính theo từng nhóm; một vấn đề có thể có nhiều nhãn.</p>`;
  }

  function renderProducts(element, data) {
    const items = data?.items || [];
    if (!items.length) return renderEmpty(element, 'Chưa có dữ liệu sản phẩm.');
    element.innerHTML = `<div class="table-wrap"><table class="table" aria-label="Phân bổ phản hồi theo sản phẩm"><thead><tr><th>Sản phẩm</th><th>Vấn đề</th><th>Báo lỗi</th><th>Báo CL tốt</th><th>Y/c cải tiến</th><th>Đề xuất SPM</th></tr></thead><tbody>${items.map(item => `<tr><td>${escHtml(item.label)}</td><td>${formatNumber(item.issue_count)} (${formatPercent(item.percentage)})</td><td>${formatNumber(item.quality_labels?.['Báo lỗi'])}</td><td>${formatNumber(item.quality_labels?.['Báo CL tốt'])}</td><td>${formatNumber(item.quality_labels?.['Y/c cải tiến'])}</td><td>${formatNumber(item.quality_labels?.['Đề xuất SPM'])}</td></tr>`).join('')}</tbody></table></div>`;
  }

  function renderDataQuality(element, data) {
    const labels = { issue_code: 'Mã vấn đề', source: 'Nguồn', unit_name: 'Đơn vị', business_status: 'Trạng thái', issue_date: 'Ngày ghi nhận' };
    const rows = Object.entries(data?.fields || {});
    if (!rows.length) return renderEmpty(element, 'Chưa có dữ liệu chất lượng.');
    element.innerHTML = `<div class="table-wrap"><table class="table" aria-label="Chất lượng dữ liệu phản hồi"><thead><tr><th>Trường</th><th>Có dữ liệu</th><th>Thiếu</th><th>Không hợp lệ</th></tr></thead><tbody>${rows.map(([field, counts]) => `<tr><td>${escHtml(labels[field] || field)}</td><td>${formatNumber(counts.present)}</td><td>${formatNumber(counts.missing)}</td><td>${formatNumber(counts.invalid)}</td></tr>`).join('')}</tbody></table></div>`;
  }

  function renderIssues(element, data) {
    const items = data?.items || [];
    if (!items.length) return renderEmpty(element, 'Không có vấn đề nào trong khoảng thời gian và bộ lọc đã chọn.');
    element.innerHTML = `<div class="table-wrap"><table class="table" aria-label="Danh sách chi tiết vấn đề"><thead><tr><th>Mã vấn đề</th><th>Ngày</th><th>Nguồn</th><th>Đơn vị</th><th>Trạng thái</th><th>Nhãn</th><th>Sản phẩm</th><th>Nội dung</th><th>Chi tiết</th></tr></thead><tbody>${items.map((item, index) => `<tr><td>${escHtml(item.issue_code || '—')}</td><td>${escHtml(item.issue_date || '—')}</td><td>${escHtml(item.source || 'Chưa xác định')}</td><td>${escHtml(item.unit_name || 'Chưa xác định')}</td><td>${escHtml(item.business_status || '—')}</td><td class="wrap">${escHtml((item.labels || []).join(', ') || '—')}</td><td>${escHtml(item.product || 'Chưa xác định')}</td><td class="wrap">${escHtml(item.content || '—')}</td><td><button class="btn btn-ghost btn-sm" type="button" onclick="AnalyticsPage.showIssueDetail(${index})">Xem</button></td></tr>`).join('')}</tbody></table></div><div class="analytics-pagination"><span class="analytics-panel-note">Hiển thị ${formatNumber(items.length)} / ${formatNumber(data.total)} vấn đề.</span><div><button class="btn btn-ghost btn-sm" type="button" ${data.page <= 1 ? 'disabled' : ''} onclick="AnalyticsPage.changeIssuePage(-1)" aria-label="Trang vấn đề trước">← Trước</button><span class="analytics-page-number">Trang ${formatNumber(data.page)} / ${formatNumber(data.total_pages || 1)}</span><button class="btn btn-ghost btn-sm" type="button" ${data.page >= data.total_pages ? 'disabled' : ''} onclick="AnalyticsPage.changeIssuePage(1)" aria-label="Trang vấn đề tiếp theo">Tiếp →</button></div></div>`;
  }

  function showIssueDetail(index) {
    const item = _state.issues?.items?.[index];
    if (!item || !window.App?.showModal) return;
    App.showModal(`<div class="analytics-detail-modal"><div class="card-header"><span class="card-title">Chi tiết vấn đề</span><button class="btn btn-ghost btn-sm" type="button" onclick="App.closeModal()">Đóng</button></div><dl class="analytics-detail-list">${detailRow('Mã vấn đề', item.issue_code)}${detailRow('Ngày', item.issue_date)}${detailRow('Nguồn', item.source)}${detailRow('Đơn vị', item.unit_name)}${detailRow('Trạng thái', item.business_status)}${detailRow('Nhãn', (item.labels || []).join(', '))}${detailRow('Sản phẩm', item.product)}${detailRow('File nguồn', item.source_file_name)}${detailRow('Dòng Excel', item.source_row_number)}${detailRow('Job', item.job_id)}${detailRow('Trạng thái phân loại', item.classification_state)}${detailRow('Nội dung', item.content)}</dl></div>`);
  }

  function detailRow(label, value) {
    return `<div><dt>${escHtml(label)}</dt><dd>${escHtml(value || '—')}</dd></div>`;
  }

  function renderEmpty(element, message) {
    element.innerHTML = `<div class="empty-state"><div class="empty-state-icon">📊</div><p class="empty-state-text">${escHtml(message)}</p></div>`;
  }

  function formatMetricValue(value, percent) {
    if (value === null || value === undefined) return '—';
    return percent ? formatPercent(value) : formatNumber(value);
  }

  function metricHint(metric) {
    if (metric?.denominator === undefined) return '';
    return `Mẫu số: ${formatNumber(metric.denominator)}${metric.excluded_missing_issue_code ? ` · Loại trừ ${formatNumber(metric.excluded_missing_issue_code)} dòng thiếu Mã vấn đề` : ''}`;
  }

  function formatNumber(value) { return Number(value || 0).toLocaleString('vi-VN'); }
  function formatPercent(value) { return `${Number(value || 0).toLocaleString('vi-VN', { maximumFractionDigits: 2 })}%`; }

  function escHtml(value) {
    const element = document.createElement('div');
    element.textContent = String(value ?? '');
    return element.innerHTML;
  }

  function destroy() {
    _state.requestId += 1;
    _state.issues = null;
    Charts.destroy('analytics-sources-chart');
    Charts.destroy('analytics-units-chart');
  }

  return { render, destroy, applyFilters, resetFilters, refresh, applyIssueFilters, clearIssueFilters, changeIssuePage, showIssueDetail, filterIssuesByUnit };
})();
