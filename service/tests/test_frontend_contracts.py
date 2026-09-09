from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"


def _read(rel: str) -> str:
    return (STATIC / rel).read_text(encoding="utf-8")


def test_classify_config_uses_product_list_endpoint():
    classify_js = _read("js/pages/classify.js")

    assert "API.get('/pipeline/products/list')" in classify_js
    assert "API.get('/pipeline/products')" not in classify_js


def test_protected_downloads_do_not_use_plain_internal_anchors():
    combined = "\n".join(
        [
            _read("js/pages/classify.js"),
            _read("js/pages/files.js"),
        ]
    )

    assert 'href="/api/files/template' not in combined
    assert 'href="/api/classify' not in combined
    assert "window.open('/api/classify" not in combined


def test_raw_xml_http_request_is_centralized_in_api_client():
    files_js = _read("js/pages/files.js")
    api_js = _read("js/api.js")

    assert "XMLHttpRequest" not in files_js
    assert "XMLHttpRequest" in api_js


def test_websocket_auth_close_refresh_contract():
    ws_js = _read("js/ws.js")
    api_js = _read("js/api.js")

    assert "event.code === 4001" in ws_js
    assert "API.refreshToken" in ws_js
    assert "refreshToken" in api_js


def _page_refs(js: str, page: str) -> set[str]:
    return set(re.findall(rf"\b{page}\.([A-Za-z_][A-Za-z0-9_]*)", js))


def _page_exports(js: str) -> set[str]:
    marker = "return {"
    start = js.rfind(marker)
    assert start != -1
    end = js.find("};", start)
    assert end != -1
    body = js[start + len(marker) : end]
    return set(re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\b(?=\s*(?:,|:|$))", body))


def test_page_handler_references_are_exported():
    pages = {
        "ClassifyPage": _read("js/pages/classify.js"),
        "SettingsPage": _read("js/pages/settings.js"),
        "FilesPage": _read("js/pages/files.js"),
    }

    for page, js in pages.items():
        refs = _page_refs(js, page)
        exports = _page_exports(js)
        missing = sorted(refs - exports)
        assert not missing, f"{page} references missing exports: {missing}"


def test_auth_background_calls_use_silent_requests():
    api_js = _read("js/api.js")
    app_js = _read("js/app.js")
    sidebar_js = _read("js/components/sidebar.js")

    assert "opts.silent" in api_js
    assert "get(path, opts = {})" in api_js
    assert "post(path, data, opts = {})" in api_js
    assert "API.get('/auth/me', { silent: true })" in app_js
    assert "API.logout({ silent: true })" in app_js
    assert "API.getHealth({ silent: true })" in sidebar_js


def test_classify_config_product_editor_contract():
    classify_js = _read("js/pages/classify.js")

    for expected in [
        "openProductEditor",
        "saveProductEditor",
        "addProductEditorRow",
        "deleteProductEditorRow",
    ]:
        assert expected in _page_exports(classify_js)


def test_file_manager_non_admin_controls_are_role_gated():
    files_js = _read("js/pages/files.js")

    assert "function isAdminRole()" in files_js
    assert '${isAdmin ? \'<button id="btn-upload-file"' in files_js
    assert '${isAdmin ? \'<button id="btn-sync-sharepoint"' in files_js
    assert '${isAdmin ? `<div class="bulk-toolbar"' in files_js
    assert "if (!isAdminRole())" in files_js


def test_file_manager_existing_input_ingest_contract():
    files_js = _read("js/pages/files.js")
    api_js = _read("js/api.js")

    assert "function ingestInputFile(filename)" in api_js
    assert "Đưa vào phân tích" in files_js
    assert "ingestInputFile" in _page_exports(files_js)
    assert "_activeFolder === 'input'" in files_js
    assert "source === 'local_cache'" in files_js
    assert "Phân loại luôn" not in files_js
    assert "result.ingest_error" in files_js
    assert "đã tải lên nhưng chưa đưa được vào phân tích" in files_js


def test_analytics_distinguishes_ingested_rows_without_ai_classification():
    analytics_js = _read("js/pages/analytics.js")

    assert "Nhãn phân loại do AI" in analytics_js
    assert "Kết quả phân loại" in analytics_js
    assert "if (item.classification_state === 'pending') return 'Chưa có nhãn';" in analytics_js
    assert "classificationDetailLabel(item)" in analytics_js
    assert (
        "return classificationLabel(item) === 'Chưa có nhãn' ? 'Trạng thái nhãn' : 'Nhãn phân loại do AI';"
        in analytics_js
    )
    assert "classification_state === 'pending'" in analytics_js
    assert "dữ liệu Excel đã đưa vào phân tích" in analytics_js


def test_managed_file_analytics_assets_have_updated_cache_versions():
    index_html = _read("index.html")

    assert "css/style.css?v=1.0.17" in index_html
    assert "js/api.js?v=1.0.9" in index_html
    assert "js/pages/analytics.js?v=1.0.20" in index_html
    assert "js/components/charts.js?v=1.0.7" in index_html
    assert "js/pages/files.js?v=1.0.4" in index_html


def test_password_controls_contract():
    password_js = _read("js/components/password.js")
    login_js = _read("js/pages/login.js")
    settings_js = _read("js/pages/settings.js")

    assert "window.PasswordControls" in password_js
    assert "normalizeValue" in password_js
    assert "toggleVisibility" in password_js
    assert 'inputmode="latin"' in password_js
    assert "Mật khẩu chỉ dùng ký tự tiếng Anh" in password_js
    assert "PasswordControls.renderInput" in login_js
    assert "PasswordControls.renderInput" in settings_js
    assert "new-user-password" in settings_js
    assert "edit-user-password" in settings_js
    assert "new-password" in settings_js


def test_sidebar_theme_switch_contract():
    sidebar_js = _read("js/components/sidebar.js")

    assert "Phân quyền theo vai trò" not in sidebar_js
    assert "theme-switch" in sidebar_js
    assert 'role="switch"' in sidebar_js
    assert "aria-checked" in sidebar_js
    assert "Chế độ sáng" in sidebar_js
    assert "Chế độ tối" in sidebar_js


def test_file_manager_delete_and_sync_copy_contract():
    files_js = _read("js/pages/files.js")

    assert "delete_scope" in files_js
    assert "local/cache" in files_js
    assert "SharePoint" in files_js
    assert "không bị xóa" in files_js
    assert "sharepoint-delete" in files_js
    assert "Xóa trên SharePoint" in files_js
    assert "Tải Input từ SharePoint" in files_js
    assert "đẩy Output lên SharePoint" in files_js
    assert "không tự đồng bộ thao tác xóa" in files_js


def test_keyword_product_editor_entry_points_contract():
    settings_js = _read("js/pages/settings.js")
    files_js = _read("js/pages/files.js")

    for expected in ["openKeywordAssetEditor", "openProductAssetEditor"]:
        assert expected in _page_exports(settings_js)
        assert expected in files_js

    assert "editKeywordAsset" in _page_exports(files_js)
    assert "kw_map.json" in files_js
    assert "Phân Chia Nhóm Sản Phẩm V2.xlsx" in files_js
    assert "Không hỗ trợ chỉnh sửa trực tiếp" in files_js


def test_sync_wrappers_use_explicit_no_body_post_contract():
    api_js = _read("js/api.js")

    assert (
        "function syncKeywordsToSP()    { return post('/pipeline/sync-keywords-to-sp', null); }"
        in api_js
    )
    assert (
        "function syncProductsToSP()    { return post('/pipeline/sync-products-to-sp', null); }"
        in api_js
    )
    assert "function syncSharePoint()      { return post('/files/sync', null); }" in api_js


def test_classify_config_has_single_editor_entry_per_section():
    classify_js = _read("js/pages/classify.js")

    assert "openKeywordEditor" in _page_exports(classify_js)
    assert classify_js.count("ClassifyPage.openPromptEditor()") == 1
    assert classify_js.count("ClassifyPage.openKeywordEditor()") == 1
    assert classify_js.count("ClassifyPage.openProductEditor()") == 1
    assert "Chỉnh sửa bảng" not in classify_js
    assert "Chỉnh sửa bảng sản phẩm" not in classify_js


def test_settings_keyword_product_editor_controls_are_consistent():
    settings_js = _read("js/pages/settings.js")

    assert "SettingsPage.saveKeywords()" in settings_js
    assert "SettingsPage.syncKeywordsToSP()" in settings_js
    assert "SettingsPage.saveProducts()" in settings_js
    assert "SettingsPage.syncProductsToSP()" in settings_js
    assert "btn-sync-keywords" in settings_js
    assert "btn-sync-products" in settings_js


def test_keyword_editor_does_not_render_duplicate_noise():
    settings_js = _read("js/pages/settings.js")

    assert "kw-duplicate-badge" not in settings_js
    assert "kw-duplicate-highlight" not in settings_js
    assert "từ khóa đã có trong nhóm này" in settings_js


def test_theme_switch_has_icon_and_label_contract():
    sidebar_js = _read("js/components/sidebar.js")

    assert "theme-switch-icon" in sidebar_js
    assert "isLight ? '☀️' : '🌙'" in sidebar_js
    assert "Chế độ sáng" in sidebar_js
    assert "Chế độ tối" in sidebar_js
    assert "aria-checked" in sidebar_js


def test_file_manager_omits_folder_tree_and_keeps_bulk_actions_near_table():
    files_js = _read("js/pages/files.js")
    css = _read("css/style.css")

    assert "Cấu trúc thư mục" not in files_js
    assert "folder-tree" not in files_js
    assert "loadTree()" not in files_js
    assert "bulk-toolbar-inline" in files_js
    assert "position: sticky" in css
    assert "Xóa local/cache" in files_js
    assert "Xóa trên SharePoint" in files_js


def test_bulk_toolbar_hidden_until_selection_and_select_all_contract():
    files_js = _read("js/pages/files.js")
    css = _read("css/style.css")

    assert "display: none;" in css
    assert ".bulk-toolbar.visible" in css
    assert "FilesPage.selectAllFiles()" in files_js
    assert "selectAllFiles" in _page_exports(files_js)


def test_model_api_key_reveal_contract():
    api_js = _read("js/api.js")
    settings_js = _read("js/pages/settings.js")

    assert "function getSecret(key)" in api_js
    assert "getSecret" in api_js
    assert "async function revealApiKey()" in settings_js
    assert "btn-reveal-apikey" in settings_js
    assert "API.getSecret('gemini_api_key')" in settings_js


def test_classify_queue_aware_job_status_contract():
    classify_js = _read("js/pages/classify.js")

    for expected in [
        "jobStatusMeta",
        "isTerminalStatus",
        "refreshCurrentJobStatus",
        "renderTerminalJobActions",
        "Đang xếp hàng",
        "Đang chờ chạy lại",
        "Job đã hủy",
    ]:
        assert expected in classify_js

    assert 'data-mode="jobs"' in classify_js
    assert "API.cancelJob(jobId)" in classify_js
    assert "API.retryJob(jobId)" in classify_js


def test_admin_job_operations_exports_and_api_contract():
    classify_js = _read("js/pages/classify.js")
    api_js = _read("js/api.js")

    for expected in ["loadAdminJobs", "cancelAdminJob", "retryAdminJob"]:
        assert expected in _page_exports(classify_js)

    assert "function getJobMetrics()" in api_js
    assert "function cancelJob(jobId)" in api_js
    assert "function retryJob(jobId)" in api_js
    assert "/classify/jobs/metrics" in api_js
    assert "/retry" in api_js


def test_analytics_api_client_has_authenticated_query_wrappers():
    api_js = _read("js/api.js")

    for expected in [
        "function getAnalyticsOverview(params = {})",
        "function getAnalyticsSources(params = {})",
        "function getAnalyticsUnits(params = {})",
        "function getAnalyticsGroups(params = {})",
        "function getAnalyticsProducts(params = {})",
        "function getAnalyticsIssues(params = {})",
        "function getAnalyticsDataQuality(params = {})",
        "buildAnalyticsQuery",
        "/analytics/overview",
        "/analytics/issues",
    ]:
        assert expected in api_js

    for exported in [
        "getAnalyticsOverview",
        "getAnalyticsSources",
        "getAnalyticsUnits",
        "getAnalyticsGroups",
        "getAnalyticsProducts",
        "getAnalyticsIssues",
        "getAnalyticsDataQuality",
    ]:
        assert exported in _page_exports(api_js)


def test_analytics_page_is_a_separate_authenticated_spa_route():
    index_html = _read("index.html")
    app_js = _read("js/app.js")
    sidebar_js = _read("js/components/sidebar.js")
    analytics_js = _read("js/pages/analytics.js")

    assert "js/pages/analytics.js" in index_html
    assert "analytics:  { module: () => window.AnalyticsPage" in app_js
    assert "{ id: 'analytics', icon: '📊', label: 'Dashboard' }" in sidebar_js
    assert "window.AnalyticsPage" in analytics_js
    assert "API.getAnalyticsOverview" in analytics_js
    assert "fetch(" not in analytics_js
    assert "XMLHttpRequest" not in analytics_js
    assert "Chất lượng dữ liệu" not in analytics_js
    assert "analytics-data-quality" not in analytics_js
    assert "renderDataQuality" not in analytics_js
    assert "API.getAnalyticsDataQuality" not in analytics_js
    assert {"render", "destroy", "applyFilters", "resetFilters", "refresh"} <= _page_exports(
        analytics_js
    )


def test_analytics_page_exposes_accessible_global_date_filters():
    analytics_js = _read("js/pages/analytics.js")

    for expected in [
        "dateField('analytics-date-from'",
        "dateField('analytics-date-to'",
        'aria-label="Bộ lọc thời gian phân tích"',
        "AnalyticsPage.applyFilters()",
        "AnalyticsPage.resetFilters()",
        "AnalyticsPage.refresh()",
    ]:
        assert expected in analytics_js
    assert "dateField('analytics-compare-from'" not in analytics_js
    assert "dateField('analytics-compare-to'" not in analytics_js
    assert "So sánh từ ngày" not in analytics_js
    assert "So sánh đến ngày" not in analytics_js


def test_analytics_page_supports_safe_issue_drilldown_filters_and_pagination():
    analytics_js = _read("js/pages/analytics.js")

    assert "textField('analytics-issue-source'" not in analytics_js
    for expected in [
        "selectField('analytics-issue-unit'",
        "selectField('analytics-issue-label'",
        "selectField('analytics-issue-product'",
        "selectField('analytics-issue-status'",
        "API.getAnalyticsIssues",
        "page_size: DEFAULT_PAGE_SIZE",
        "AnalyticsPage.changeIssuePage(-1)",
        "AnalyticsPage.changeIssuePage(1)",
        "AnalyticsPage.showIssueDetail",
        "App.showModal",
        "analytics-date-from",
    ]:
        assert expected in analytics_js

    assert {
        "applyIssueFilters",
        "clearIssueFilters",
        "changeIssuePage",
        "showIssueDetail",
    } <= _page_exports(analytics_js)


def test_analytics_page_uses_chart_helpers_and_cleans_up_its_charts():
    analytics_js = _read("js/pages/analytics.js")

    for expected in [
        "Charts.createDoughnutChart('analytics-sources-chart'",
        "Charts.createBarChart('analytics-units-chart'",
        "Charts.destroy('analytics-sources-chart')",
        "Charts.destroy('analytics-units-chart')",
        "Chưa gán cảm xúc",
        "available === false",
        'aria-label="Nhóm vấn đề và cảm xúc"',
        "<th>Nhóm vấn đề</th>",
        "<th>Tích cực</th>",
        "<th>Tiêu cực</th>",
        "<th>Trung lập</th>",
        "<th>Chưa gán cảm xúc</th>",
    ]:
        assert expected in analytics_js
    groups_fn = analytics_js.split("function renderGroups")[1].split("function renderProducts")[0]
    assert "analytics-distribution-row" not in groups_fn


def test_analytics_visual_refresh_uses_donut_sources_and_horizontal_unit_bars():
    analytics_js = _read("js/pages/analytics.js")
    style_css = _read("css/style.css")

    sources_fn = analytics_js.split("function renderSources")[1].split("function renderUnits")[0]
    units_fn = analytics_js.split("function renderUnits")[1].split("function renderDistribution")[0]
    assert "analytics-donut-shell" in sources_fn
    assert "analytics-donut-total" in sources_fn
    assert "data?.total_issues" in sources_fn
    assert "sourceChart.options.plugins.legend.display = false" in sources_fn
    assert "sourceChart.update('none')" in sources_fn
    assert "Vòng biểu diễn lượt thuộc nguồn; số ở tâm là số vấn đề duy nhất" in sources_fn
    assert "Charts.createDoughnutChart('analytics-sources-chart'" in sources_fn
    assert "Charts.createBarChart('analytics-units-chart'" in units_fn
    assert "indexAxis: 'y'" in units_fn
    assert ".analytics-donut-shell {" in style_css
    assert ".analytics-donut-total {" in style_css


def test_analytics_dashboard_is_wide_and_keeps_only_actionable_score_cards():
    analytics_js = _read("js/pages/analytics.js")
    style_css = _read("css/style.css")

    assert "app.classList.add('page-container-wide')" in analytics_js
    assert "app.classList.remove('page-container-wide')" in analytics_js
    assert ".page-container.page-container-wide" in style_css
    assert "max-width: none" in style_css
    assert "processed_issues" not in analytics_js
    assert "label_coverage" not in analytics_js
    assert "multi_label_rate" not in analytics_js
    assert "renderMetricSkeletons(5)" in analytics_js
    assert "function metricInsight(key, metric)" in analytics_js
    assert "Đã có cảm xúc" in analytics_js
    assert "Đã nhận diện sản phẩm" in analytics_js
    assert "mã vấn đề có nội dung trùng" in analytics_js
    assert "Chưa có nhãn đối chứng do con người xác nhận." in analytics_js
    assert "analytics-kpi analytics-kpi-${key}" in analytics_js
    assert "analytics-kpi-unavailable" in analytics_js
    assert 'aria-label="${escAttr(`${label}: ${displayValue}. ${hint}`)}"' in analytics_js
    assert ".analytics-kpi-total_issues" in style_css
    assert ".analytics-kpi-sentiment_coverage" in style_css
    assert ".analytics-kpi-product_coverage" in style_css
    assert ".analytics-kpi-duplicate_issue_rate" in style_css


def test_analytics_p0_matches_prototype_structure_without_restoring_removed_scope():
    analytics_js = _read("js/pages/analytics.js")
    style_css = _read("css/style.css")
    index_html = _read("index.html")

    assert 'class="analytics-page"' in analytics_js
    assert "Bộ lọc phân tích" in analytics_js
    assert 'id="analytics-active-filters"' in analytics_js
    assert analytics_js.index("dateField('analytics-date-from'") < analytics_js.index(
        "dateField('analytics-date-to'"
    )
    assert analytics_js.index("dateField('analytics-date-to'") < analytics_js.index(
        "selectField('analytics-unit'"
    )
    assert analytics_js.index("selectField('analytics-unit'") < analytics_js.index(
        "selectField('analytics-district'"
    )
    assert "renderMetricSkeletons(5)" in analytics_js
    assert "analytics-geography-layout" in analytics_js
    assert "analytics-unit-issue-type-matrix" in analytics_js
    assert "analytics-groups" in analytics_js
    assert "analytics-products" in analytics_js
    assert "analytics-issues" in analytics_js
    for expected_css in [
        ".analytics-page {",
        ".analytics-page .card {",
        ".analytics-page .stat-grid {",
        ".analytics-geography-layout {",
        ".analytics-matrix-wrap {",
        ".analytics-matrix thead th {",
    ]:
        assert expected_css in style_css
    assert "css/style.css?v=1.0.17" in index_html
    assert "js/pages/analytics.js?v=1.0.20" in index_html

    for removed in [
        "dateField('analytics-compare-from'",
        "dateField('analytics-compare-to'",
        "selectField('analytics-province'",
        "analytics-data-quality",
        "analytics-status-backlog",
    ]:
        assert removed not in analytics_js


def test_analytics_p0_filter_card_reports_applied_scope_and_option_failures():
    analytics_js = _read("js/pages/analytics.js")

    assert 'id="analytics-active-filters"' in analytics_js
    assert 'id="analytics-filter-options-error"' in analytics_js
    assert "function renderActiveFilters()" in analytics_js
    assert "Đang lọc:" in analytics_js
    assert "Toàn bộ dữ liệu" in analytics_js
    assert "renderActiveFilters();" in analytics_js
    assert "function setFilterOptionsError(message)" in analytics_js
    assert "Không thể tải danh sách Đơn vị và Quận/huyện." in analytics_js
    assert "if (optionsRequestId !== _state.optionsRequestId) return" in analytics_js


def test_analytics_visual_refresh_matches_prototype_header_filter_and_kpi_density():
    analytics_js = _read("js/pages/analytics.js")
    style_css = _read("css/style.css")

    for expected in [
        'class="analytics-header-icon"',
        'class="analytics-header-waves"',
        'class="analytics-filter-icon"',
        "analytics-filter-footer",
        "analytics-kpi-copy",
    ]:
        assert expected in analytics_js
    for expected in [
        ".analytics-page-header {",
        ".analytics-header-icon {",
        ".analytics-header-waves {",
        ".analytics-filter-icon {",
        ".analytics-filter-footer {",
        ".analytics-kpi-copy {",
    ]:
        assert expected in style_css


def test_analytics_visual_refresh_renders_product_quality_as_heatmap():
    analytics_js = _read("js/pages/analytics.js")
    style_css = _read("css/style.css")

    products_fn = analytics_js.split("function renderProducts")[1].split(
        "function renderGeography"
    )[0]
    assert "analytics-product-matrix" in products_fn
    assert "analytics-product-heat-cell" in products_fn
    assert "analytics-product-heat-cell-zero" in products_fn
    assert "Tổng lượt" in products_fn
    assert "const maxQualityCount" in products_fn
    assert ".analytics-product-heat-cell {" in style_css
    assert ".analytics-product-heat-cell-zero {" in style_css


def test_analytics_visual_refresh_uses_prototype_panel_composition():
    analytics_js = _read("js/pages/analytics.js")
    style_css = _read("css/style.css")

    assert 'class="analytics-primary-grid"' in analytics_js
    assert 'class="card analytics-daily-card"' in analytics_js
    assert "analytics-compact-card" in analytics_js
    assert 'class="analytics-secondary-grid"' in analytics_js
    assert "'analytics-groups-card')" in analytics_js
    assert "'analytics-product-card')" in analytics_js
    assert ".analytics-primary-grid," in style_css
    assert ".analytics-daily-card," in style_css
    assert ".analytics-secondary-grid {" in style_css
    assert ".analytics-groups-card {" in style_css
    assert ".analytics-product-card {" in style_css
    assert "grid-column: 1 / -1" in style_css


def test_analytics_detail_table_has_scoped_summary_and_badge_styles():
    css = _read("css/style.css")
    container = css.split("#analytics-issues {")[1].split("}")[0]
    assert "contain: inline-size;" in container
    # These selectors must outrank the later shared table nowrap rules.
    for selector in [
        ".table.analytics-issues-table thead th",
        ".table.analytics-issues-table tbody td",
    ]:
        rules = re.findall(r"([^{}]+)\{([^{}]*)\}", css)
        declarations = "\n".join(
            body for selectors, body in rules if selector in map(str.strip, selectors.split(","))
        )
        assert "white-space: normal;" in declarations, selector
        assert "overflow-wrap: anywhere;" in declarations
        assert "vertical-align: top;" in declarations
    for selector in [
        ".analytics-issues-table {",
        ".analytics-issues-table .analytics-issue-summary {",
        ".analytics-issues-table .analytics-issue-classification {",
        ".analytics-issues-table .badge {",
    ]:
        assert selector in css
    summary = css.split(".analytics-issues-table .analytics-issue-summary {")[1].split("}")[0]
    assert "-webkit-line-clamp: 3;" in summary
    assert "overflow-wrap: anywhere;" in summary
    assert "white-space: normal;" in summary


def test_analytics_compact_desktop_distribution_fits_without_clipping_data():
    css = _read("css/style.css")
    desktop = re.search(r"@media \(min-width: 641px\)\s*\{(.*?)\n\}", css, re.S)
    assert desktop, "Compact desktop cards need a scoped, shrinkable distribution layout"

    def declarations(selector):
        matches = re.findall(r"([^{}]+)\{([^{}]*)\}", desktop.group(1))
        return "\n".join(
            body for selectors, body in matches if selector in map(str.strip, selectors.split(","))
        )

    scope = ".analytics-compact-card .analytics-distribution-"
    row = declarations(scope + "row")
    # Two children (unit buttons) must not reserve a third, empty track.
    assert "grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);" in row
    for name, column in [("label", 1), ("value", 2)]:
        text = declarations(scope + name)
        assert f"grid-column: {column};" in text
        assert "grid-row: 1;" in text
        assert "min-width: 0;" in text
        assert "white-space: normal;" in text
        assert "overflow-wrap: anywhere;" in text
        assert "overflow: visible;" in text
        assert "text-overflow: ellipsis;" not in text
    bar = declarations(scope + "bar")
    assert "grid-column: 1 / -1;" in bar
    assert "grid-row: 2;" in bar
    # The noncompact distribution contract must remain unchanged.
    global_row = re.search(r"^\.analytics-distribution-row \{([^}]+)\}", css, re.M)
    assert global_row
    assert "minmax(100px, 0.8fr) minmax(100px, 2fr) auto" in global_row.group(1)


def test_analytics_page_renders_and_cleans_up_daily_trend():
    api_js = _read("js/api.js")
    analytics_js = _read("js/pages/analytics.js")

    assert "function getAnalyticsDailyTrend(params = {})" in api_js
    assert "/analytics/trends/daily" in api_js
    assert "API.getAnalyticsDailyTrend" in analytics_js
    assert "Charts.createLineChart('analytics-daily-trend-chart'" in analytics_js
    assert "Charts.destroy('analytics-daily-trend-chart')" in analytics_js


def test_analytics_page_renders_issue_type_distribution():
    api_js = _read("js/api.js")
    analytics_js = _read("js/pages/analytics.js")

    assert "function getAnalyticsIssueTypes(params = {})" in api_js
    assert "/analytics/issue-types" in api_js
    assert "API.getAnalyticsIssueTypes" in analytics_js
    assert "analytics-issue-types-chart" in analytics_js
    assert "Charts.destroy('analytics-issue-types-chart')" in analytics_js


def test_analytics_page_renders_paginated_duplicate_details():
    api_js = _read("js/api.js")
    analytics_js = _read("js/pages/analytics.js")

    assert "function getAnalyticsDuplicates(params = {})" in api_js
    assert "/analytics/duplicates" in api_js
    assert "API.getAnalyticsDuplicates" in analytics_js
    assert "analytics-duplicates" in analytics_js
    assert "AnalyticsPage.changeDuplicatePage(-1)" in analytics_js
    assert "AnalyticsPage.changeDuplicatePage(1)" in analytics_js
    assert "changeDuplicatePage" in _page_exports(analytics_js)


def test_analytics_page_renders_priority_issues():
    api_js = _read("js/api.js")
    analytics_js = _read("js/pages/analytics.js")
    style_css = _read("css/style.css")

    assert "function getAnalyticsPriorityIssues(params = {})" in api_js
    assert "/analytics/priority-issues" in api_js
    assert "API.getAnalyticsPriorityIssues" in analytics_js
    assert "analytics-priority-issues" in analytics_js
    assert "Phản hồi cần ưu tiên" in analytics_js
    assert "analytics-priority-table" in analytics_js
    assert "showPriorityDetail" in _page_exports(analytics_js)
    assert ".analytics-priority-table" in style_css


def test_analytics_page_renders_unit_issue_type_heatmap():
    api_js = _read("js/api.js")
    analytics_js = _read("js/pages/analytics.js")

    assert "function getAnalyticsUnitIssueTypeMatrix(params = {})" in api_js
    assert "/analytics/unit-issue-type-matrix" in api_js
    assert "API.getAnalyticsUnitIssueTypeMatrix" in analytics_js
    assert "analytics-unit-issue-type-matrix" in analytics_js
    assert "analytics-heat-cell" in analytics_js


def test_analytics_p0_matrix_renders_totals_insight_and_neutral_zero_cells():
    analytics_js = _read("js/pages/analytics.js")

    assert "data?.column_totals" in analytics_js
    assert "data?.grand_total" in analytics_js
    assert "data?.top_unit" in analytics_js
    assert "data?.top_issue_type" in analytics_js
    assert "Đơn vị nổi bật" in analytics_js
    assert "Loại vấn đề nổi bật" in analytics_js
    assert "Tổng cộng" in analytics_js
    assert "analytics-heat-cell-zero" in analytics_js
    assert "const strength = count === 0 ? 0" in analytics_js
    assert 'aria-label="${escAttr(cellLabel)}"' in analytics_js


def test_analytics_page_renders_unit_before_cascading_district_filter():
    api_js = _read("js/api.js")
    analytics_js = _read("js/pages/analytics.js")
    sidebar_js = _read("js/components/sidebar.js")
    app_js = _read("js/app.js")

    assert "function getAnalyticsGeography(params = {})" in api_js
    assert "function getAnalyticsFilterOptions(params = {})" in api_js
    assert "/analytics/filter-options" in api_js
    assert "selectField('analytics-unit'" in analytics_js
    assert "selectField('analytics-district'" in analytics_js
    assert "selectField('analytics-province'" not in analytics_js
    assert "getElementById('analytics-province')" not in analytics_js
    assert "onProvinceChange" not in analytics_js
    assert analytics_js.index("selectField('analytics-unit'") < analytics_js.index(
        "selectField('analytics-district'"
    )
    assert "AnalyticsPage.onUnitChange()" in analytics_js
    assert "resetSelect('analytics-district')" in analytics_js
    assert "API.getAnalyticsFilterOptions" in analytics_js
    assert "const optionsRequestId = ++_state.optionsRequestId" in analytics_js
    assert "if (optionsRequestId !== _state.optionsRequestId) return" in analytics_js
    assert "API.getAnalyticsGeography" in analytics_js
    assert "analytics-provinces-chart" in analytics_js
    assert "district: _state.filters.district" in analytics_js
    assert "unit: _state.filters.unit" in analytics_js
    assert "unit: globalParams.unit || _state.issueFilters.unit" in analytics_js
    assert "escAttr(option)" in analytics_js
    assert "onUnitChange" in _page_exports(analytics_js)
    assert sidebar_js.index("{ id: 'analytics'") < sidebar_js.index("{ id: 'classify'")
    assert "const DEFAULT_PAGE = 'analytics';" in app_js


def test_analytics_p0_geography_renders_ranked_district_table_and_data_notes():
    analytics_js = _read("js/pages/analytics.js")

    assert "data?.top_province" in analytics_js
    assert "Địa phương nổi bật" in analytics_js
    assert 'aria-label="Phân bổ vấn đề theo quận huyện"' in analytics_js
    assert "<th>Quận/huyện</th>" in analytics_js
    assert "<th>Số vấn đề</th>" in analytics_js
    assert "<th>Tỷ trọng</th>" in analytics_js
    assert "missing_province_count" in analytics_js
    assert "missing_district_count" in analytics_js
    assert "Thiếu Tỉnh/TP" in analytics_js
    assert "Thiếu Quận/huyện" in analytics_js


def test_analytics_page_renders_processing_status_donut_without_backlog_aging():
    api_js = _read("js/api.js")
    analytics_js = _read("js/pages/analytics.js")

    assert "function getAnalyticsStatusBacklog(params = {})" in api_js
    assert "/analytics/status-backlog" in api_js
    assert "API.getAnalyticsStatusBacklog" in analytics_js
    assert "analytics-status-chart" in analytics_js
    assert "Tình trạng xử lý" in analytics_js
    assert "Charts.destroy('analytics-status-chart')" in analytics_js
    assert "Thời gian tồn đọng" not in analytics_js
