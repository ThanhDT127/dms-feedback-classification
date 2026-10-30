from __future__ import annotations

import re
from pathlib import Path


def test_css_referenced_static_assets_exist():
    static_dir = Path(__file__).resolve().parents[1] / "static"
    css_dir = static_dir / "css"

    for css_path in css_dir.glob("*.css"):
        css = css_path.read_text(encoding="utf-8")
        for match in re.finditer(r"url\((['\"]?)(?P<url>.*?)\1\)", css):
            url = match.group("url")
            if url.startswith(("data:", "http:", "https:")):
                continue
            asset_path = (css_path.parent / url).resolve()
            assert asset_path.is_file(), f"{css_path.name} references missing asset {url}"


def test_static_ui_text_has_no_mojibake_markers():
    static_dir = Path(__file__).resolve().parents[1] / "static"
    files = [
        *static_dir.glob("*.html"),
        *static_dir.glob("css/*.css"),
        *static_dir.glob("js/**/*.js"),
    ]
    bad_patterns = [
        "\u00c3",  # Ã
        "\u00c2",  # Â
        "\u00c4",  # Ä
        "\u00d0",  # Ð
        "\u00f0\u0178",  # ðŸ
        "\u00e2\u20ac",  # â€
        "\u00e2\u0161",  # âš
        "\u00e2\u0153",  # âœ
        "\u00e2\u2020",  # â†
        "\u00e2\u2013",  # â–
        "\u00e2\u02dc",  # â˜
        "\u00e2\u017e",  # âž
        "\u00e2\u009d",  # â
        "\u00e1\u00ba",  # áº
        "\u00e1\u00bb",  # á»
        "\u00c6",  # Æ
    ]
    control_re = re.compile(r"[\u0080-\u009f]")

    failures: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        found = [pattern for pattern in bad_patterns if pattern in text]
        if control_re.search(text):
            found.append("C1 control")
        if found:
            rel = path.relative_to(static_dir)
            failures.append(f"{rel}: {', '.join(found)}")

    assert not failures, "Mojibake markers found:\n" + "\n".join(failures)


def test_representative_vietnamese_ui_text_is_present():
    static_dir = Path(__file__).resolve().parents[1] / "static"
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [
            static_dir / "index.html",
            static_dir / "js/pages/login.js",
            static_dir / "js/components/sidebar.js",
            static_dir / "js/pages/classify.js",
            static_dir / "js/pages/files.js",
            static_dir / "js/pages/settings.js",
            static_dir / "js/pages/qa.js",
        ]
    )

    for expected in [
        "Hệ thống phân loại phản hồi Rạng Đông",
        "Đăng nhập",
        "Quản lý file",
        "Từ khóa",
        "Hướng dẫn",
    ]:
        assert expected in combined
    assert "Phân loại phản hồi tiếp thị" not in combined


def test_background_asset_is_used_in_dark_light_and_login_contexts():
    static_dir = Path(__file__).resolve().parents[1] / "static"
    css = (static_dir / "css/style.css").read_text(encoding="utf-8")

    assert (static_dir / "assets/Nen-chatbot.png").is_file()
    assert css.count("Nen-chatbot.png") >= 5
    assert re.search(r"body\s*{[^}]*Nen-chatbot\.png", css, re.S)
    assert re.search(r'\[data-theme="soft"\]\s+body\s*{[^}]*Nen-chatbot\.png', css, re.S)
    assert re.search(r'\[data-theme="light"\]\s+body\s*{[^}]*Nen-chatbot\.png', css, re.S)
    assert re.search(r"\.login-container\s*{[^}]*Nen-chatbot\.png", css, re.S)
    assert re.search(
        r'\[data-theme="soft"\]\s+\.login-container\s*{[^}]*Nen-chatbot\.png', css, re.S
    )


def test_brand_status_pill_and_compact_sidebar_bottom():
    static_dir = Path(__file__).resolve().parents[1] / "static"
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    login_js = (static_dir / "js/pages/login.js").read_text(encoding="utf-8")
    sidebar_js = (static_dir / "js/components/sidebar.js").read_text(encoding="utf-8")
    css = (static_dir / "css/style.css").read_text(encoding="utf-8")

    assert "<title>Hệ thống phân loại phản hồi Rạng Đông</title>" in html
    assert '<span class="topbar-title">Hệ thống phân loại phản hồi Rạng Đông</span>' in html
    assert "DMS" not in re.search(r"<title>.*?</title>", html).group(0)

    assert "brand-status-pill" in login_js
    assert "API.getPublicHealth" in login_js
    assert "brand-status-pill" in sidebar_js
    assert ".brand-status-pill" in css

    bottom_match = re.search(
        r'<div class="sidebar-status" id="sidebar-status">(.*?)</div>\s*<div style=',
        sidebar_js,
        re.S,
    )
    assert bottom_match is not None
    bottom_html = bottom_match.group(1)
    assert "Uptime" in bottom_html
    assert "Cấu hình" in bottom_html
    assert "Phiên bản" not in bottom_html
    assert "Trạng thái" not in bottom_html


def test_every_script_in_index_html_exists():
    """Quên đăng ký file mới trong index.html thì trang chỉ báo lỗi ở console."""
    static_dir = Path(__file__).resolve().parents[1] / "static"
    html = (static_dir / "index.html").read_text(encoding="utf-8")

    refs = re.findall(r'(?:src|href)="((?:js|css)/[^"?]+)(?:\?[^"]*)?"', html)

    assert refs
    for ref in refs:
        assert (static_dir / ref).is_file(), f"index.html trỏ tới file không có: {ref}"


def test_chat_ui_does_not_use_native_browser_dialogs():
    """``confirm``/``alert`` của trình duyệt khoá cả tab và không theo giao diện tối/sáng."""
    chat_js = Path(__file__).resolve().parents[1] / "static" / "js" / "pages" / "chat.js"
    source = chat_js.read_text(encoding="utf-8")

    for call in ("window.confirm(", "window.alert("):
        assert call not in source, f"chat.js còn dùng {call}"
