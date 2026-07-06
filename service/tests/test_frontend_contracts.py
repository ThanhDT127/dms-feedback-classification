from __future__ import annotations

from pathlib import Path
import re


STATIC = Path(__file__).resolve().parents[1] / "static"


def _read(rel: str) -> str:
    return (STATIC / rel).read_text(encoding="utf-8")


def test_classify_config_uses_product_list_endpoint():
    classify_js = _read("js/pages/classify.js")

    assert "API.get('/pipeline/products/list')" in classify_js
    assert "API.get('/pipeline/products')" not in classify_js


def test_protected_downloads_do_not_use_plain_internal_anchors():
    combined = "\n".join([
        _read("js/pages/classify.js"),
        _read("js/pages/files.js"),
    ])

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
    body = js[start + len(marker):end]
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
    assert "${isAdmin ? '<button id=\"btn-upload-file\"" in files_js
    assert "${isAdmin ? '<button id=\"btn-sync-sharepoint\"" in files_js
    assert "${isAdmin ? `<div class=\"bulk-toolbar\"" in files_js
    assert "if (!isAdminRole())" in files_js


def test_password_controls_contract():
    password_js = _read("js/components/password.js")
    login_js = _read("js/pages/login.js")
    settings_js = _read("js/pages/settings.js")

    assert "window.PasswordControls" in password_js
    assert "normalizeValue" in password_js
    assert "toggleVisibility" in password_js
    assert "inputmode=\"latin\"" in password_js
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
    assert "role=\"switch\"" in sidebar_js
    assert "aria-checked" in sidebar_js
    assert "Chế độ sáng" in sidebar_js
    assert "Chế độ tối" in sidebar_js


def test_file_manager_delete_and_sync_copy_contract():
    files_js = _read("js/pages/files.js")

    assert "delete_scope" in files_js
    assert "local/cache" in files_js
    assert "SharePoint không bị xóa" in files_js
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

    assert "function syncKeywordsToSP()    { return post('/pipeline/sync-keywords-to-sp', null); }" in api_js
    assert "function syncProductsToSP()    { return post('/pipeline/sync-products-to-sp', null); }" in api_js
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
    assert " trùng" not in settings_js
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
    assert "SettingsPage.revealApiKey()" in settings_js
    assert "btn-reveal-apikey" in settings_js
    assert "API.getSecret('gemini_api_key')" in settings_js
