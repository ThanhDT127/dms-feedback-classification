/* ============================================================
   Feedback Analytics Page — authenticated business analytics
   ============================================================ */

window.AnalyticsPage = (() => {
  const DEFAULT_PAGE_SIZE = 25;
  let _cache = null;
  const _state = {
    filters: { from: '', to: '', district: '', unit: '' },
    filterOptions: { districts: [], units: [] },
    issueFilters: { source: '', unit: '', label: '', product: '', status: '' },
    issuePage: 1,
    duplicatePage: 1,
    issues: null,
    duplicates: null,
    units: [],
    requestId: 0,
    optionsRequestId: 0,
  };

  function getFilterKey() {
    return JSON.stringify({
      filters: _state.filters,
      issueFilters: _state.issueFilters,
      issuePage: _state.issuePage,
      duplicatePage: _state.duplicatePage,
    });
  }

  function render() {
    const app = document.getElementById('app');
    if (!app) return;
    app.classList.add('page-container-wide');
    app.innerHTML = `
      <div class="analytics-page">
      <div class="page-header analytics-page-header">
        <div class="analytics-header-icon" aria-hidden="true">▥</div>
        <div class="analytics-header-copy">
          <h2>Dashboard</h2>
          <p>Chỉ số nghiệp vụ từ dữ liệu Excel đã đưa vào phân tích</p>
        </div>
        <div class="analytics-header-waves" aria-hidden="true"></div>
      </div>

      <section class="card analytics-filter-card" aria-label="Bộ lọc thời gian phân tích">
        <div class="card-header">
          <span class="card-title"><span class="analytics-filter-icon" aria-hidden="true">⌄</span> Bộ lọc phân tích</span>
          <button class="btn btn-ghost btn-sm" type="button" onclick="AnalyticsPage.refresh()">🔄 Làm mới</button>
        </div>
        <div class="analytics-filter-grid">
          ${dateField('analytics-date-from', 'Từ ngày', _state.filters.from)}
          ${dateField('analytics-date-to', 'Đến ngày', _state.filters.to)}
          ${selectField('analytics-unit', 'Đơn vị', _state.filters.unit, _state.filterOptions.units, 'AnalyticsPage.onUnitChange()')}
          ${selectField('analytics-district', 'Quận/huyện', _state.filters.district, _state.filterOptions.districts)}
          <div class="analytics-filter-actions">
            <button class="btn btn-primary btn-sm" type="button" onclick="AnalyticsPage.applyFilters()">Áp dụng</button>
            <button class="btn btn-ghost btn-sm" type="button" onclick="AnalyticsPage.resetFilters()">Đặt lại</button>
          </div>
        </div>
        <div class="analytics-filter-footer">
          <p class="analytics-filter-hint">Để trống để xem toàn bộ dữ liệu. Mỗi khoảng thời gian phải có đủ ngày bắt đầu và kết thúc.</p>
          <div id="analytics-active-filters" class="analytics-active-filters" aria-live="polite"></div>
        </div>
        <div id="analytics-filter-options-error" class="analytics-inline-alert" role="alert" hidden></div>
        <div id="analytics-filter-error" class="analytics-inline-alert" role="alert" hidden></div>
      </section>

      <div id="analytics-page-status" class="analytics-page-status" aria-live="polite"></div>

      <section aria-labelledby="analytics-overview-title">
        <div class="section-heading"><h3 id="analytics-overview-title">Tổng quan</h3><span class="text-muted">KPI tính theo Mã vấn đề khác rỗng</span></div>
        <div id="analytics-overview" class="stat-grid">${renderMetricSkeletons(5)}</div>
      </section>

      <!-- Hàng 1: Vấn đề theo ngày + Tỷ trọng theo nguồn thông tin -->
      <div class="analytics-primary-grid">
        <section class="card analytics-daily-card" aria-labelledby="analytics-daily-title">
          <div class="card-header"><span id="analytics-daily-title" class="card-title">📅 Vấn đề theo ngày</span></div>
          <div id="analytics-daily-trend" class="analytics-panel-body">${renderPanelLoading()}</div>
        </section>
        ${panel('analytics-source-title', '📡 Tỷ trọng theo nguồn thông tin', 'analytics-sources', 'analytics-compact-card')}
      </div>

      <!-- Hàng 2: Tỷ trọng theo đơn vị + Nhóm vấn đề × Cảm xúc -->
      <div class="analytics-secondary-grid">
        ${panel('analytics-unit-title', '👥 Tỷ trọng vấn đề theo đơn vị', 'analytics-units', 'analytics-compact-card')}
        ${panel('analytics-group-title', '🏷️ Nhóm vấn đề × Cảm xúc', 'analytics-groups', 'analytics-groups-card')}
      </div>

      <!-- Hàng 3: Sản phẩm × Loại vấn đề + Tình trạng xử lý + Cơ cấu loại vấn đề -->
      <div class="analytics-tertiary-grid">
        ${panel('analytics-product-title', '📦 Sản phẩm × Loại vấn đề', 'analytics-products', 'analytics-product-card')}
        ${panel('analytics-status-title', '⏱️ Tình trạng xử lý', 'analytics-processing-status', 'analytics-compact-card')}
        ${panel('analytics-issue-type-title', '🧭 Cơ cấu loại vấn đề', 'analytics-issue-types', 'analytics-compact-card')}
      </div>

      <section class="card" aria-labelledby="analytics-geography-title">
        <div class="card-header"><span id="analytics-geography-title" class="card-title">🗺️ Phân bổ địa lý</span></div>
        <div id="analytics-geography" class="analytics-panel-body">${renderPanelLoading()}</div>
      </section>

      <section class="card" aria-labelledby="analytics-unit-matrix-title">
        <div class="card-header"><span id="analytics-unit-matrix-title" class="card-title">🧩 Ma trận đơn vị × loại vấn đề</span></div>
        <div id="analytics-unit-issue-type-matrix" class="analytics-panel-body">${renderPanelLoading()}</div>
      </section>

      <section class="card" aria-labelledby="analytics-duplicates-title">
        <div class="card-header"><span id="analytics-duplicates-title" class="card-title">♻️ Nội dung phản hồi trùng</span></div>
        <div id="analytics-duplicates" class="analytics-panel-body">${renderPanelLoading()}</div>
      </section>

      <section class="card" aria-labelledby="analytics-issues-title">
        <div class="card-header"><span id="analytics-issues-title" class="card-title">📋 Chi tiết vấn đề</span></div>
        <div class="analytics-issue-filter-grid" aria-label="Bộ lọc chi tiết vấn đề">
          ${textField('analytics-issue-source', 'Nguồn', _state.issueFilters.source)}
          ${textField('analytics-issue-unit', 'Đơn vị', _state.issueFilters.unit)}
          ${textField('analytics-issue-label', 'Kết quả phân loại', _state.issueFilters.label)}
          ${textField('analytics-issue-product', 'Sản phẩm', _state.issueFilters.product)}
          ${textField('analytics-issue-status', 'Trạng thái', _state.issueFilters.status)}
          <div class="analytics-filter-actions"><button class="btn btn-primary btn-sm" type="button" onclick="AnalyticsPage.applyIssueFilters()">Lọc</button><button class="btn btn-ghost btn-sm" type="button" onclick="AnalyticsPage.clearIssueFilters()">Bỏ lọc</button></div>
        </div>
        <div id="analytics-issues" class="analytics-panel-body">${renderPanelLoading()}</div>
      </section>
      </div>
    `;
    renderActiveFilters();
    loadFilterOptions();
    const filterKey = getFilterKey();
    if (_cache && _cache.key === filterKey) {
      renderAllPanels(_cache.results);
      const status = document.getElementById('analytics-page-status');
      if (status) status.textContent = 'Dữ liệu phân tích đã được tải từ bộ nhớ đệm.';
      return;
    }
    refresh(false);
  }

  function panel(titleId, title, bodyId, className = '') {
    return `<section class="card ${className}" aria-labelledby="${titleId}"><div class="card-header"><span id="${titleId}" class="card-title">${title}</span></div><div id="${bodyId}" class="analytics-panel-body">${renderPanelLoading()}</div></section>`;
  }

  function dateField(id, label, value) {
    return `<label class="analytics-field" for="${id}"><span>${label}</span><input id="${id}" class="form-input" type="date" value="${escHtml(value)}"></label>`;
  }

  function textField(id, label, value) {
    return `<label class="analytics-field" for="${id}"><span>${label}</span><input id="${id}" class="form-input" value="${escHtml(value)}"></label>`;
  }

  function selectField(id, label, value, options, changeHandler = '') {
    const onchange = changeHandler ? ` onchange="${changeHandler}"` : '';
    return `<label class="analytics-field" for="${id}"><span>${label}</span><select id="${id}" class="form-input"${onchange}><option value="">Tất cả</option>${(options || []).map(option => `<option value="${escAttr(option)}"${option === value ? ' selected' : ''}>${escHtml(option)}</option>`).join('')}</select></label>`;
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
      district: document.getElementById('analytics-district')?.value.trim() || '',
      unit: document.getElementById('analytics-unit')?.value.trim() || '',
    };
  }

  function validateRanges(filters) {
    for (const [from, to, label] of [[filters.from, filters.to, 'Khoảng thời gian chính']]) {
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
    _state.duplicatePage = 1;
    _cache = null;
    renderActiveFilters();
    loadFilterOptions();
    refresh(true);
  }

  function resetFilters() {
    _state.filters = { from: '', to: '', district: '', unit: '' };
    const inputIds = {
      from: 'analytics-date-from',
      to: 'analytics-date-to',
      district: 'analytics-district',
      unit: 'analytics-unit',
    };
    Object.entries(inputIds).forEach(([key, id]) => {
      const input = document.getElementById(id);
      if (input) input.value = _state.filters[key];
    });
    _state.issuePage = 1;
    _state.duplicatePage = 1;
    setFilterError(null);
    _cache = null;
    renderActiveFilters();
    loadFilterOptions();
    refresh(true);
  }

  async function onUnitChange() {
    _state.filters.unit = document.getElementById('analytics-unit')?.value || '';
    _state.filters.district = '';
    resetSelect('analytics-district');
    await loadFilterOptions();
  }

  function resetSelect(id) {
    const select = document.getElementById(id);
    if (!select) return;
    select.innerHTML = '<option value="">Tất cả</option>';
    select.value = '';
    select.disabled = true;
  }

  async function loadFilterOptions() {
    const optionsRequestId = ++_state.optionsRequestId;
    setFilterOptionsError(null);
    try {
      const options = await API.getAnalyticsFilterOptions({
        from: _state.filters.from || undefined,
        to: _state.filters.to || undefined,
        unit: _state.filters.unit || undefined,
      });
      if (optionsRequestId !== _state.optionsRequestId) return;
      _state.filterOptions = {
        districts: options?.districts || [],
        units: options?.units || [],
      };
      updateSelect('analytics-unit', _state.filterOptions.units, _state.filters.unit);
      updateSelect('analytics-district', _state.filterOptions.districts, _state.filters.district);
    } catch (error) {
      if (optionsRequestId !== _state.optionsRequestId) return;
      console.warn('Không thể tải lựa chọn bộ lọc analytics:', error.message);
      setFilterOptionsError('Không thể tải danh sách Đơn vị và Quận/huyện. Vui lòng thử lại.');
    }
  }

  function updateSelect(id, options, value) {
    const select = document.getElementById(id);
    if (!select) return;
    select.innerHTML = `<option value="">Tất cả</option>${options.map(option => `<option value="${escAttr(option)}"${option === value ? ' selected' : ''}>${escHtml(option)}</option>`).join('')}`;
    select.disabled = false;
  }

  function setFilterError(message) {
    const element = document.getElementById('analytics-filter-error');
    if (!element) return;
    element.hidden = !message;
    element.textContent = message || '';
  }

  function setFilterOptionsError(message) {
    const element = document.getElementById('analytics-filter-options-error');
    if (!element) return;
    element.hidden = !message;
    element.textContent = message || '';
  }

  function renderActiveFilters() {
    const element = document.getElementById('analytics-active-filters');
    if (!element) return;
    const labels = [];
    if (_state.filters.from && _state.filters.to) labels.push(`${_state.filters.from} đến ${_state.filters.to}`);
    if (_state.filters.unit) labels.push(`Đơn vị: ${_state.filters.unit}`);
    if (_state.filters.district) labels.push(`Quận/huyện: ${_state.filters.district}`);
    element.textContent = labels.length ? `Đang lọc: ${labels.join(' · ')}` : 'Toàn bộ dữ liệu';
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
    return {
      from: _state.filters.from || undefined,
      to: _state.filters.to || undefined,
      district: _state.filters.district || undefined,
      unit: _state.filters.unit || undefined,
    };
  }

  function issueQueryParams() {
    const globalParams = globalQueryParams();
    return { ...globalParams, ..._state.issueFilters, unit: globalParams.unit || _state.issueFilters.unit, page: _state.issuePage, page_size: DEFAULT_PAGE_SIZE };
  }

  function duplicateQueryParams() {
    return { ...globalQueryParams(), page: _state.duplicatePage, page_size: DEFAULT_PAGE_SIZE };
  }

  function renderAllPanels(results) {
    renderResult(results.overview, renderOverview, 'analytics-overview');
    renderResult(results.dailyTrend, renderDailyTrend, 'analytics-daily-trend');
    renderResult(results.issueTypes, renderIssueTypes, 'analytics-issue-types');
    renderResult(results.unitIssueTypeMatrix, renderUnitIssueTypeMatrix, 'analytics-unit-issue-type-matrix');
    renderResult(results.geography, renderGeography, 'analytics-geography');
    renderResult(results.duplicates, (element, data) => { _state.duplicates = data; renderDuplicates(element, data); }, 'analytics-duplicates');
    renderResult(results.sources, renderSources, 'analytics-sources');
    renderResult(results.units, renderUnits, 'analytics-units');
    renderResult(results.groups, renderGroups, 'analytics-groups');
    renderResult(results.products, renderProducts, 'analytics-products');
    renderResult(results.status, renderProcessingStatus, 'analytics-processing-status');
    renderResult(results.issues, (element, data) => { _state.issues = data; renderIssues(element, data); }, 'analytics-issues');
  }

  async function refresh(force = true) {
    const filterKey = getFilterKey();
    if (!force && _cache && _cache.key === filterKey) {
      renderAllPanels(_cache.results);
      const status = document.getElementById('analytics-page-status');
      if (status) status.textContent = 'Dữ liệu phân tích đã được tải từ bộ nhớ đệm.';
      return;
    }

    const requestId = ++_state.requestId;
    const status = document.getElementById('analytics-page-status');
    if (status) status.textContent = 'Đang cập nhật dữ liệu phân tích...';
    const requests = {
      overview: API.getAnalyticsOverview(globalQueryParams()),
      dailyTrend: API.getAnalyticsDailyTrend(globalQueryParams()),
      issueTypes: API.getAnalyticsIssueTypes(globalQueryParams()),
      unitIssueTypeMatrix: API.getAnalyticsUnitIssueTypeMatrix(globalQueryParams()),
      geography: API.getAnalyticsGeography(globalQueryParams()),
      duplicates: API.getAnalyticsDuplicates(duplicateQueryParams()),
      sources: API.getAnalyticsSources(globalQueryParams()),
      units: API.getAnalyticsUnits(globalQueryParams()),
      groups: API.getAnalyticsGroups(globalQueryParams()),
      products: API.getAnalyticsProducts(globalQueryParams()),
      status: API.getAnalyticsStatusBacklog(globalQueryParams()),
      issues: API.getAnalyticsIssues(issueQueryParams()),
    };
    const entries = Object.entries(requests);
    const settled = await Promise.allSettled(entries.map(([, request]) => request));
    if (requestId !== _state.requestId) return;
    const results = Object.fromEntries(entries.map(([key], index) => [key, settled[index]]));
    _cache = { key: filterKey, results };
    renderAllPanels(results);
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
      if (_cache?.results) {
        _cache.results.issues = { status: 'fulfilled', value: data };
        _cache.key = getFilterKey();
      }
      renderIssues(element, data);
    } catch (error) {
      if (requestId !== _state.requestId) return;
      renderFailure(element, error, 'Không thể tải chi tiết vấn đề');
    }
  }

  async function loadDuplicates() {
    const element = document.getElementById('analytics-duplicates');
    if (!element) return;
    element.innerHTML = renderPanelLoading();
    try {
      const data = await API.getAnalyticsDuplicates(duplicateQueryParams());
      _state.duplicates = data;
      if (_cache?.results) {
        _cache.results.duplicates = { status: 'fulfilled', value: data };
        _cache.key = getFilterKey();
      }
      renderDuplicates(element, data);
    } catch (error) {
      renderFailure(element, error, 'Không thể tải nội dung trùng');
    }
  }

  function changeDuplicatePage(delta) {
    const totalPages = _state.duplicates?.total_pages || 0;
    const page = _state.duplicatePage + delta;
    if (page < 1 || (totalPages > 0 && page > totalPages)) return;
    _state.duplicatePage = page;
    loadDuplicates();
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
      ['sentiment_coverage', 'Hoàn thiện cảm xúc', '😊', 'orange', true],
      ['product_coverage', 'Nhận diện sản phẩm', '📦', 'blue', true],
      ['duplicate_issue_rate', 'Tỷ lệ vấn đề trùng', '♻️', 'red', true],
      ['model_accuracy', 'Độ chính xác mô hình', '🎯', 'gray', true],
    ];
    element.innerHTML = metrics.map(([key, label, icon, color, percent]) => {
      const metric = data?.[key] || {};
      const unavailable = metric.available === false;
      const hint = metricInsight(key, metric);
      const displayValue = unavailable ? '—' : formatMetricValue(metric.value, percent);
      return `<div class="stat-card ${color} analytics-kpi analytics-kpi-${key}${unavailable ? ' analytics-kpi-unavailable' : ''} animate-in" title="${escAttr(hint)}" aria-label="${escAttr(`${label}: ${displayValue}. ${hint}`)}"><div class="stat-card-top"><div class="analytics-kpi-copy"><div class="stat-card-value">${escHtml(displayValue)}</div><div class="stat-card-label">${escHtml(label)}</div></div><div class="stat-card-icon" aria-hidden="true">${icon}</div></div><div class="analytics-metric-hint">${escHtml(hint)}</div></div>`;
    }).join('');
  }

  function metricInsight(key, metric) {
    const denominator = Number(metric?.denominator || 0);
    if (metric?.available === false) {
      if (key === 'model_accuracy') return 'Chưa có nhãn đối chứng do con người xác nhận.';
      return metric?.reason || 'Chỉ số chưa khả dụng.';
    }
    if (key === 'total_issues') {
      const excluded = Number(metric?.excluded_missing_issue_code || 0);
      return excluded ? `Đã loại trừ ${formatNumber(excluded)} dòng thiếu Mã vấn đề.` : 'Toàn bộ vấn đề trong phạm vi bộ lọc hiện tại.';
    }
    if (key === 'sentiment_coverage') {
      return `Đã có cảm xúc cho ${formatNumber(metric?.numerator)} / ${formatNumber(denominator)} vấn đề.`;
    }
    if (key === 'product_coverage') {
      return `Đã nhận diện sản phẩm cho ${formatNumber(metric?.numerator)} / ${formatNumber(denominator)} vấn đề.`;
    }
    if (key === 'duplicate_issue_rate') {
      return `${formatNumber(metric?.duplicate_issue_codes)} mã vấn đề có nội dung trùng.`;
    }
    return metricHint(metric);
  }


  function renderDailyTrend(element, data) {
    const items = data?.items || [];
    if (!items.length) return renderEmpty(element, 'Chưa có dữ liệu xu hướng theo ngày.');
    const hasSentiment = items.some(item => item.sentiment_counts);
    if (hasSentiment && typeof Charts.createStackedDatasetChart === 'function') {
      element.innerHTML = '<div class="analytics-chart-wrap"><canvas id="analytics-daily-trend-chart"></canvas></div><p class="analytics-panel-note">Biểu đồ thể hiện lượt cảm xúc theo ngày. Một vấn đề có thể có nhiều cảm xúc trong cùng ngày, tổng lượt cảm xúc có thể vượt số vấn đề duy nhất theo ngày.</p>';
      Charts.createStackedDatasetChart('analytics-daily-trend-chart', items.map(item => item.date), [
        { label: 'Tích cực', data: items.map(item => Number(item.sentiment_counts?.['Tích cực'] || 0)), backgroundColor: '#22c55e' },
        { label: 'Trung lập', data: items.map(item => Number(item.sentiment_counts?.['Trung lập'] || 0)), backgroundColor: '#3b82f6' },
        { label: 'Tiêu cực', data: items.map(item => Number(item.sentiment_counts?.['Tiêu cực'] || 0)), backgroundColor: '#ef4444' },
        { label: 'Chưa gán', data: items.map(item => Number(item.sentiment_counts?.['Chưa gán'] || 0)), backgroundColor: '#94a3b8' },
      ]);
    } else {
      element.innerHTML = '<div class="analytics-chart-wrap"><canvas id="analytics-daily-trend-chart"></canvas></div>';
      Charts.createLineChart('analytics-daily-trend-chart', items.map(item => item.date), [{
        label: 'Số vấn đề',
        data: items.map(item => item.issue_count),
        borderColor: '#22c55e',
        backgroundColor: 'rgba(34, 197, 94, 0.12)',
        fill: true,
      }]);
    }
  }

  function renderIssueTypes(element, data) {
    const items = data?.items || [];
    if (!items.length) return renderEmpty(element, 'Chưa có dữ liệu loại vấn đề.');
    const totalIssues = Number(data?.total_issues || 0);
    const colorMap = {
      'Báo lỗi': '#ef4444',
      'Báo CL tốt': '#22c55e',
      'Y/c cải tiến': '#1e40af',
      'Ý/cải tiến': '#1e40af',
      'Đề xuất SPM': '#f97316',
    };
    const colors = items.map(it => colorMap[it.label] || '#94a3b8');
    element.innerHTML = `
      <div class="analytics-donut-shell">
        <div class="analytics-donut-circle-wrap">
          <div class="analytics-chart-wrap"><canvas id="analytics-issue-types-chart"></canvas></div>
          <div class="analytics-donut-total"><strong>${formatNumber(totalIssues)}</strong><span>Vấn đề</span></div>
        </div>
        <div class="analytics-donut-legend-panel">
          ${items.map(it => `
            <div class="analytics-donut-legend-row" role="listitem">
              <span class="analytics-donut-legend-dot" style="background:${colorMap[it.label] || '#94a3b8'};" aria-hidden="true"></span>
              <span class="analytics-donut-legend-name" title="${escAttr(it.label)}">${escHtml(it.label)}</span>
              <span class="analytics-donut-legend-val"><strong>${formatNumber(it.issue_count)}</strong> <span class="text-muted">(${formatPercent(it.percentage)})</span></span>
            </div>
          `).join('')}
        </div>
      </div>
    `;
    const chart = Charts.createDoughnutChart('analytics-issue-types-chart', items.map(item => item.label), items.map(item => item.issue_count), colors, { showLegend: false });
    if (chart) {
      chart.options.plugins.legend.display = false;
      chart.update('none');
    }
  }

  function renderProcessingStatus(element, data) {
    const statuses = data?.statuses || [];
    if (!statuses.length) return renderEmpty(element, 'Chưa có dữ liệu tình trạng xử lý.');
    const totalIssues = Number(data?.total_issues || 0);
    const colorMap = {
      'Chờ xử lý': '#ef4444',
      'Đang xử lý': '#1e40af',
      'Đã xử lý': '#22c55e',
    };
    const colors = statuses.map(s => colorMap[s.label] || '#94a3b8');
    element.innerHTML = `
      <div class="analytics-donut-shell">
        <div class="analytics-donut-circle-wrap">
          <div class="analytics-chart-wrap"><canvas id="analytics-status-chart"></canvas></div>
          <div class="analytics-donut-total"><strong>${formatNumber(totalIssues)}</strong><span>Vấn đề</span></div>
        </div>
        <div class="analytics-donut-legend-panel">
          ${statuses.map(s => `
            <div class="analytics-donut-legend-row" role="listitem">
              <span class="analytics-donut-legend-dot" style="background:${colorMap[s.label] || '#94a3b8'};" aria-hidden="true"></span>
              <span class="analytics-donut-legend-name" title="${escAttr(s.label)}">${escHtml(s.label)}</span>
              <span class="analytics-donut-legend-val"><strong>${formatNumber(s.issue_count)}</strong> <span class="text-muted">(${formatPercent(s.percentage)})</span></span>
            </div>
          `).join('')}
        </div>
      </div>
    `;
    const chart = Charts.createDoughnutChart('analytics-status-chart', statuses.map(s => s.label), statuses.map(s => s.issue_count), colors, { showLegend: false });
    if (chart) {
      chart.options.plugins.legend.display = false;
      chart.update('none');
    }
  }

  function renderSources(element, data) {
    const items = data?.items || [];
    if (!items.length) return renderEmpty(element, 'Chưa có dữ liệu phân bổ.');
    const totalIssues = Number(data?.total_issues || 0);
    const defaultColors = [
      '#22c55e', '#f59e0b', '#c084fc', '#3b82f6', '#ef4444',
      '#06b6d4', '#ec4899', '#84cc16', '#f97316', '#6366f1',
    ];
    element.innerHTML = `
      <div class="analytics-donut-shell">
        <div class="analytics-donut-circle-wrap">
          <div class="analytics-chart-wrap"><canvas id="analytics-sources-chart"></canvas></div>
          <div class="analytics-donut-total"><strong>${formatNumber(totalIssues)}</strong><span>Vấn đề</span></div>
        </div>
        <div class="analytics-donut-legend-panel">
          ${items.map((item, index) => {
            const color = defaultColors[index % defaultColors.length];
            return `
              <div class="analytics-donut-legend-row" role="listitem">
                <span class="analytics-donut-legend-dot" style="background:${color};" aria-hidden="true"></span>
                <span class="analytics-donut-legend-name" title="${escAttr(item.label)}">${escHtml(item.label)}</span>
                <span class="analytics-donut-legend-val"><strong>${formatNumber(item.issue_count)}</strong> <span class="text-muted">(${formatPercent(item.percentage)})</span></span>
              </div>
            `;
          }).join('')}
        </div>
      </div>
      <p class="analytics-panel-note">Vòng biểu diễn lượt thuộc nguồn; số ở tâm là số vấn đề duy nhất. Các vấn đề có thể thuộc nhiều nguồn; tổng tỷ trọng không nhất thiết bằng 100%.</p>
    `;
    const sourceChart = Charts.createDoughnutChart('analytics-sources-chart', items.map(item => item.label), items.map(item => item.issue_count), null, { showLegend: false });
    if (sourceChart) {
      sourceChart.options.plugins.legend.display = false;
      sourceChart.update('none');
    }
  }

  function renderUnits(element, data) {
    const items = data?.items || [];
    if (!items.length) return renderEmpty(element, 'Chưa có dữ liệu phân bổ.');
    _state.units = items;
    element.innerHTML = `<div class="analytics-chart-wrap"><canvas id="analytics-units-chart"></canvas></div><div class="analytics-distribution analytics-unit-list" role="list">${items.map((item, index) => `<button class="analytics-distribution-row analytics-drilldown" type="button" onclick="AnalyticsPage.filterIssuesByUnit(${index})" role="listitem"><span class="analytics-distribution-label">${escHtml(item.label)}</span><span class="analytics-distribution-value">${formatNumber(item.issue_count)} (${formatPercent(item.percentage)})</span></button>`).join('')}</div><p class="analytics-panel-note">Chọn một đơn vị để lọc bảng chi tiết. Các vấn đề có thể thuộc nhiều đơn vị.</p>`;
    Charts.createBarChart('analytics-units-chart', items.map(item => item.label), items.map(item => item.issue_count), { label: 'Số vấn đề', chartOptions: { indexAxis: 'y' } });
  }

  function renderDistribution(items) {
    const defaultColors = [
      '#22c55e', '#f59e0b', '#c084fc', '#3b82f6', '#ef4444',
      '#06b6d4', '#ec4899', '#84cc16', '#f97316', '#6366f1',
    ];
    return `<div class="analytics-distribution" role="list">${items.map((item, index) => {
      const color = defaultColors[index % defaultColors.length];
      return `<div class="analytics-distribution-row" role="listitem"><div class="analytics-distribution-label">${escHtml(item.label)}</div><div class="analytics-distribution-bar"><span style="width:${Math.min(Number(item.percentage) || 0, 100)}%;background:${color};"></span></div><div class="analytics-distribution-value">${formatNumber(item.issue_count)} (${formatPercent(item.percentage)})</div></div>`;
    }).join('')}</div>`;
  }

  function renderGroups(element, data) {
    const items = data?.items || [];
    if (!items.length) return renderEmpty(element, 'Chưa có nhóm vấn đề được phân loại.');
    const posData = [];
    const neuData = [];
    const negData = [];
    items.forEach(item => {
      const counts = item.sentiment_counts || {};
      const pos = Number(counts['Tích cực'] || 0);
      const neu = Number(counts['Trung lập'] || 0);
      const neg = Number(counts['Tiêu cực'] || 0);
      const sum = pos + neu + neg;
      const pPos = sum > 0 ? Math.round((pos / sum) * 100) : 0;
      const pNeu = sum > 0 ? Math.round((neu / sum) * 100) : 0;
      const pNeg = sum > 0 ? Math.max(0, 100 - pPos - pNeu) : 0;
      posData.push(pPos);
      neuData.push(pNeu);
      negData.push(pNeg);
    });
    element.innerHTML = `<div class="analytics-chart-wrap" style="height: 180px;"><canvas id="analytics-groups-chart"></canvas></div><div class="analytics-legend-bar"><span class="analytics-legend-item"><span class="analytics-legend-dot" style="background:#22c55e;"></span> Tích cực</span><span class="analytics-legend-item"><span class="analytics-legend-dot" style="background:#1e40af;"></span> Trung tính</span><span class="analytics-legend-item"><span class="analytics-legend-dot" style="background:#ef4444;"></span> Tiêu cực</span></div><div class="table-wrap" style="margin-top: 12px;"><table class="table" aria-label="Nhóm vấn đề và cảm xúc"><thead><tr><th>Nhóm vấn đề</th><th>Vấn đề</th><th>Tích cực</th><th>Tiêu cực</th><th>Trung lập</th><th>Chưa gán cảm xúc</th></tr></thead><tbody>${items.map(item => {
      const counts = item.sentiment_counts || {};
      const known = Number(counts['Tích cực'] || 0) + Number(counts['Tiêu cực'] || 0) + Number(counts['Trung lập'] || 0);
      const missing = Math.max(0, Number(item.issue_count || 0) - known);
      return `<tr><td>${escHtml(item.label)}</td><td>${formatNumber(item.issue_count)}</td><td>${formatNumber(counts['Tích cực'])}</td><td>${formatNumber(counts['Tiêu cực'])}</td><td>${formatNumber(counts['Trung lập'])}</td><td>${formatNumber(missing)}</td></tr>`;
    }).join('')}</tbody></table></div><p class="analytics-panel-note">Phân bổ cảm xúc tính theo từng nhóm; một vấn đề có thể có nhiều nhãn.</p>`;
    if (typeof Charts.createStackedDatasetChart === 'function') {
      Charts.createStackedDatasetChart(
        'analytics-groups-chart',
        items.map(it => it.label),
        [
          { label: 'Tích cực', data: posData, backgroundColor: '#22c55e', stack: 'sentiment', barPercentage: 0.65 },
          { label: 'Trung tính', data: neuData, backgroundColor: '#1e40af', stack: 'sentiment', barPercentage: 0.65 },
          { label: 'Tiêu cực', data: negData, backgroundColor: '#ef4444', stack: 'sentiment', barPercentage: 0.65 },
        ],
        {
          indexAxis: 'y',
          plugins: { legend: { display: false } },
          scales: {
            x: {
              stacked: true,
              max: 100,
              ticks: { callback: v => v + '%' }
            },
            y: {
              stacked: true
            }
          }
        }
      );
    }
  }

  function renderProducts(element, data) {
    const items = data?.items || [];
    if (!items.length) return renderEmpty(element, 'Chưa có dữ liệu sản phẩm.');
    const qualityLabels = ['Báo lỗi', 'Báo CL tốt', 'Y/c cải tiến', 'Đề xuất SPM'];
    const maxQualityCount = Math.max(1, ...items.flatMap(item => qualityLabels.map(label => Number(item.quality_labels?.[label] || 0))));
    const columnTotals = Object.fromEntries(qualityLabels.map(label => [label, items.reduce((total, item) => total + Number(item.quality_labels?.[label] || 0), 0)]));
    const totalMemberships = items.reduce((total, item) => total + Number(item.issue_count || 0), 0);
    const rows = items.map(item => `<tr><th scope="row">${escHtml(item.label)}</th><td>${formatNumber(item.issue_count)} (${formatPercent(item.percentage)})</td>${qualityLabels.map(label => {
      const count = Number(item.quality_labels?.[label] || 0);
      const strength = count === 0 ? 0 : Math.min(0.85, Math.max(0.14, count / maxQualityCount));
      const cellLabel = `${item.label} · ${label}: ${formatNumber(count)} lượt`;
      return `<td class="analytics-product-heat-cell${count === 0 ? ' analytics-product-heat-cell-zero' : ''}" style="--heat-strength:${strength}" title="${escAttr(cellLabel)}" aria-label="${escAttr(cellLabel)}">${formatNumber(count)}</td>`;
    }).join('')}</tr>`).join('');
    const totals = qualityLabels.map(label => `<td><strong>${formatNumber(columnTotals[label])}</strong></td>`).join('');
    element.innerHTML = `<div class="table-wrap"><table class="table analytics-product-matrix" aria-label="Phân bổ phản hồi theo sản phẩm"><thead><tr><th>Sản phẩm</th><th>Vấn đề</th>${qualityLabels.map(label => `<th>${escHtml(label)}</th>`).join('')}</tr></thead><tbody>${rows}<tr class="analytics-product-total"><th scope="row">Tổng lượt</th><td><strong>${formatNumber(totalMemberships)}</strong></td>${totals}</tr></tbody></table></div>`;
  }

  function renderGeography(element, data) {
    const provinces = (data?.provinces || []).slice(0, 10);
    const districts = (data?.districts || []).slice(0, 10);
    const topProvince = data?.top_province;
    const topDistrict = data?.top_district;
    const missingProvince = Number(data?.missing_province_count || 0);
    const missingDistrict = Number(data?.missing_district_count || 0);
    if (!provinces.length) {
      element.innerHTML = `<div class="empty-state"><div class="empty-state-icon">🗺️</div><p class="empty-state-text">Chưa có dữ liệu tỉnh/thành.</p><p class="empty-state-hint">Thiếu Tỉnh/TP: ${formatNumber(missingProvince)} · Thiếu Quận/huyện: ${formatNumber(missingDistrict)}</p></div>`;
      return;
    }
    const insight = topProvince
      ? `Địa phương nổi bật: ${topProvince.label} có ${formatNumber(topProvince.issue_count)} vấn đề (${formatPercent(topProvince.percentage)}).${topDistrict ? ` Quận/huyện nổi bật: ${topDistrict.label}.` : ''}`
      : 'Chưa xác định địa phương nổi bật.';
    const districtRows = districts.map(item => `<tr><td>${escHtml(item.label)}</td><td>${formatNumber(item.issue_count)}</td><td>${formatPercent(item.percentage)}</td></tr>`).join('');
    element.innerHTML = `<div class="analytics-geography-insight">${escHtml(insight)}</div><div class="analytics-geography-layout"><div><h4>Top tỉnh/thành phố</h4><div class="analytics-chart-wrap"><canvas id="analytics-provinces-chart"></canvas></div></div><div><h4>Top quận/huyện</h4>${districtRows ? `<div class="table-wrap"><table class="table" aria-label="Phân bổ vấn đề theo quận huyện"><thead><tr><th>Quận/huyện</th><th>Số vấn đề</th><th>Tỷ trọng</th></tr></thead><tbody>${districtRows}</tbody></table></div>` : '<p class="analytics-panel-note">Chưa có dữ liệu quận/huyện.</p>'}</div></div><div class="analytics-geography-missing"><span>Thiếu Tỉnh/TP: <strong>${formatNumber(missingProvince)}</strong></span><span>Thiếu Quận/huyện: <strong>${formatNumber(missingDistrict)}</strong></span></div>`;
    Charts.createBarChart('analytics-provinces-chart', provinces.map(item => item.label), provinces.map(item => item.issue_count), { label: 'Số vấn đề', chartOptions: { indexAxis: 'y' } });
  }

  function renderUnitIssueTypeMatrix(element, data) {
    const rows = data?.rows || [];
    const issueTypes = data?.issue_types || [];
    if (!rows.length || !issueTypes.length) return renderEmpty(element, 'Chưa có dữ liệu cho ma trận đơn vị và loại vấn đề.');
    const columnTotals = data?.column_totals || {};
    const grandTotal = Number(data?.grand_total || 0);
    const topUnit = data?.top_unit;
    const topIssueType = data?.top_issue_type;
    const maxCount = Math.max(1, ...rows.flatMap(row => issueTypes.map(issueType => Number(row.counts?.[issueType] || 0))));
    const insightParts = [];
    if (topUnit) insightParts.push(`Đơn vị nổi bật: ${topUnit.label} (${formatNumber(topUnit.issue_count)} vấn đề)`);
    if (topIssueType) insightParts.push(`Loại vấn đề nổi bật: ${topIssueType.label} (${formatNumber(topIssueType.issue_count)} vấn đề)`);
    const bodyRows = rows.map(row => `<tr><th scope="row">${escHtml(row.unit)}</th>${issueTypes.map(issueType => {
      const count = Number(row.counts?.[issueType] || 0);
      const strength = count === 0 ? 0 : Math.min(0.85, Math.max(0.14, count / maxCount));
      const cellLabel = `${row.unit} · ${issueType}: ${formatNumber(count)} vấn đề`;
      return `<td class="analytics-heat-cell${count === 0 ? ' analytics-heat-cell-zero' : ''}" style="--heat-strength:${strength}" title="${escAttr(cellLabel)}" aria-label="${escAttr(cellLabel)}">${formatNumber(count)}</td>`;
    }).join('')}<td class="analytics-matrix-total"><strong>${formatNumber(row.total)}</strong></td></tr>`).join('');
    const totalCells = issueTypes.map(issueType => `<td class="analytics-matrix-total"><strong>${formatNumber(columnTotals[issueType])}</strong></td>`).join('');
    element.innerHTML = `${insightParts.length ? `<div class="analytics-matrix-insight">${escHtml(insightParts.join(' · '))}</div>` : ''}<div class="analytics-matrix-wrap"><table class="table analytics-matrix" aria-label="Ma trận đơn vị theo loại vấn đề"><thead><tr><th>Đơn vị</th>${issueTypes.map(issueType => `<th>${escHtml(issueType)}</th>`).join('')}<th>Tổng</th></tr></thead><tbody>${bodyRows}<tr class="analytics-matrix-grand-total"><th scope="row">Tổng cộng</th>${totalCells}<td><strong>${formatNumber(grandTotal)}</strong></td></tr></tbody></table></div>`;
  }

  function renderDuplicates(element, data) {
    const items = data?.items || [];
    if (!items.length) return renderEmpty(element, 'Không có nhóm nội dung trùng trong khoảng thời gian đã chọn.');
    element.innerHTML = `<div class="table-wrap"><table class="table" aria-label="Danh sách nội dung phản hồi trùng"><thead><tr><th>Nội dung đại diện</th><th>Bản ghi</th><th>Dòng trùng</th><th>Mã vấn đề</th><th>Đơn vị</th></tr></thead><tbody>${items.map(item => `<tr><td class="wrap">${escHtml(item.content)}</td><td>${formatNumber(item.record_count)}</td><td>${formatNumber(item.duplicate_rows)}</td><td class="wrap">${escHtml((item.issue_codes || []).join(', '))}</td><td class="wrap">${escHtml((item.units || []).join(', ') || 'Chưa xác định')}</td></tr>`).join('')}</tbody></table></div><div class="analytics-pagination"><span class="analytics-panel-note">${formatNumber(data.total)} nhóm nội dung trùng.</span><div><button class="btn btn-ghost btn-sm" type="button" ${data.page <= 1 ? 'disabled' : ''} onclick="AnalyticsPage.changeDuplicatePage(-1)">← Trước</button><span class="analytics-page-number">Trang ${formatNumber(data.page)} / ${formatNumber(data.total_pages || 1)}</span><button class="btn btn-ghost btn-sm" type="button" ${data.page >= data.total_pages ? 'disabled' : ''} onclick="AnalyticsPage.changeDuplicatePage(1)">Tiếp →</button></div></div>`;
  }

  function renderIssues(element, data) {
    const items = data?.items || [];
    if (!items.length) return renderEmpty(element, 'Không có vấn đề nào trong khoảng thời gian và bộ lọc đã chọn.');
    element.innerHTML = `<div class="table-wrap"><table class="table analytics-issues-table" aria-label="Danh sách chi tiết vấn đề"><thead><tr><th>Mã vấn đề</th><th>Ngày</th><th>Nguồn</th><th>Đơn vị</th><th>Trạng thái</th><th>Kết quả phân loại</th><th>Sản phẩm</th><th>Nội dung</th><th>Chi tiết</th></tr></thead><tbody>${items.map((item, index) => `<tr><td>${escHtml(item.issue_code || '—')}</td><td>${escHtml(item.issue_date || '—')}</td><td>${escHtml(item.source || 'DMS')}</td><td>${escHtml(item.unit_name || 'Chưa xác định')}</td><td><span class="badge badge-muted analytics-issue-status">${escHtml(item.business_status || '—')}</span></td><td class="wrap"><div class="analytics-issue-classification"><span>${escHtml(classificationLabel(item))}</span>${renderIssueSentiment(item)}</div></td><td>${escHtml(item.classification_state === 'pending' ? 'Chưa phân loại' : item.product || 'Chưa xác định')}</td><td><div class="analytics-issue-summary">${escHtml(item.content || '—')}</div></td><td><button class="btn btn-ghost btn-sm" type="button" onclick="AnalyticsPage.showIssueDetail(${index})" aria-label="${escAttr(`Xem chi tiết vấn đề ${item.issue_code || '—'}`)}">Xem</button></td></tr>`).join('')}</tbody></table></div><div class="analytics-pagination"><span class="analytics-panel-note">Hiển thị ${formatNumber(items.length)} / ${formatNumber(data.total)} vấn đề.</span><div><button class="btn btn-ghost btn-sm" type="button" ${data.page <= 1 ? 'disabled' : ''} onclick="AnalyticsPage.changeIssuePage(-1)" aria-label="Trang vấn đề trước">← Trước</button><span class="analytics-page-number">Trang ${formatNumber(data.page)} / ${formatNumber(data.total_pages || 1)}</span><button class="btn btn-ghost btn-sm" type="button" ${data.page >= data.total_pages ? 'disabled' : ''} onclick="AnalyticsPage.changeIssuePage(1)" aria-label="Trang vấn đề tiếp theo">Tiếp →</button></div></div>`;
  }

  function issueSentiment(item) {
    return item.classification_state === 'pending' ? 'Chưa gán cảm xúc' : item.sentiment || 'Chưa gán cảm xúc';
  }

  function renderIssueSentiment(item) {
    const sentiment = issueSentiment(item);
    const color = sentiment === 'Tích cực' ? 'green' : sentiment === 'Tiêu cực' ? 'red' : 'muted';
    return `<span class="badge badge-${color} analytics-issue-sentiment" aria-label="${escAttr(`Cảm xúc: ${sentiment}`)}">${escHtml(sentiment)}</span>`;
  }

  function classificationLabel(item) {
    if (item.classification_state === 'pending') return 'Chưa có nhãn';
    return (item.labels || []).join(', ') || 'Chưa có nhãn';
  }

  function classificationDetailLabel(item) {
    return classificationLabel(item) === 'Chưa có nhãn' ? 'Trạng thái nhãn' : 'Nhãn phân loại do AI';
  }

  function showIssueDetail(index) {
    const item = _state.issues?.items?.[index];
    if (!item || !window.App?.showModal) return;
    App.showModal(`<div class="analytics-detail-modal"><div class="card-header"><span class="card-title">Chi tiết vấn đề</span><button class="btn btn-ghost btn-sm" type="button" onclick="App.closeModal()">Đóng</button></div><dl class="analytics-detail-list">${detailRow('Mã vấn đề', item.issue_code)}${detailRow('Ngày', item.issue_date)}${detailRow('Nguồn', item.source || 'DMS')}${detailRow('Đơn vị', item.unit_name)}${detailRow('Trạng thái', item.business_status)}${detailRow('Cảm xúc', issueSentiment(item))}${detailRow(classificationDetailLabel(item), classificationLabel(item))}${detailRow('Sản phẩm', item.classification_state === 'pending' ? 'Chưa phân loại' : item.product)}${detailRow('File nguồn', item.source_file_name)}${detailRow('Dòng Excel', item.source_row_number)}${detailRow('Job', item.job_id)}${detailRow('Trạng thái phân loại', item.classification_state)}${detailRow('Nội dung', item.content)}</dl></div>`);
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

  function escAttr(value) {
    return escHtml(value).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function destroy() {
    _state.requestId += 1;
    _state.optionsRequestId += 1;
    const app = document.getElementById('app');
    if (app) app.classList.remove('page-container-wide');
    Charts.destroy('analytics-daily-trend-chart');
    Charts.destroy('analytics-issue-types-chart');
    Charts.destroy('analytics-provinces-chart');
    Charts.destroy('analytics-sources-chart');
    Charts.destroy('analytics-units-chart');
    Charts.destroy('analytics-groups-chart');
    Charts.destroy('analytics-status-chart');
  }

  return { render, destroy, applyFilters, resetFilters, refresh, onUnitChange, applyIssueFilters, clearIssueFilters, changeIssuePage, changeDuplicatePage, showIssueDetail, filterIssuesByUnit };
})();
