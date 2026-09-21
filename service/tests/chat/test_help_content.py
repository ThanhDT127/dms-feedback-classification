from __future__ import annotations

from dms.chat.ai.help_content import (
    LABEL_UNAVAILABLE,
    MAX_LABEL_OPTIONS,
    build_help,
    usage_text,
)
from dms.chat.ai.intents import Intent, is_supported

SNAPSHOT = {
    "label_definitions": {
        "Báo lỗi": "Khách hàng phản ánh sản phẩm bị lỗi hoặc hỏng.",
        "Bảo hành": "Yêu cầu liên quan tới bảo hành, đổi trả.",
        "Y/c cải tiến": "Đề nghị cải tiến tính năng hoặc mẫu mã.",
        "Báo CL tốt": "Khách hàng khen chất lượng sản phẩm.",
        "Website": "Vấn đề khi dùng website.",
        "Đề xuất SPM": "Đề xuất sản phẩm mới.",
    },
    "minor_order": ["Báo lỗi", "Bảo hành"],
    "minor_to_major": {"Báo lỗi": "Sản phẩm"},
}


# ── usage ──


def test_usage_lists_only_supported_intents():
    text = usage_text("M1")

    assert text.startswith("Tôi trả lời các câu hỏi")
    assert "Số liệu chung" in text  # OVERVIEW
    # Báo cáo tuần chưa hỗ trợ ở M1 nên không được liệt kê.
    assert "Báo cáo theo tuần" not in text
    assert is_supported(Intent.REPORT_WEEKLY, "M1") is False


def test_build_help_defaults_to_usage():
    answer = build_help(None, milestone="M1")

    assert answer.needs_clarify is False
    assert "Ví dụ" in answer.text


# ── label_definition ──


def test_exact_label_returns_its_definition():
    answer = build_help("label_definition", label="Báo lỗi", snapshot=SNAPSHOT)

    assert answer.needs_clarify is False
    assert answer.text == "Báo lỗi: Khách hàng phản ánh sản phẩm bị lỗi hoặc hỏng."


def test_label_match_ignores_accents_and_case():
    answer = build_help("label_definition", label="bao loi", snapshot=SNAPSHOT)

    assert answer.needs_clarify is False
    assert answer.text.startswith("Báo lỗi:")


def test_unknown_label_asks_again_with_options():
    answer = build_help("label_definition", label="Nhãn Sao Hoả", snapshot=SNAPSHOT)

    assert answer.needs_clarify is True
    assert "Nhãn Sao Hoả" in answer.text
    assert 1 <= len(answer.clarify_options) <= MAX_LABEL_OPTIONS
    assert all(option in SNAPSHOT["label_definitions"] for option in answer.clarify_options)


def test_missing_label_config_is_reported_clearly():
    answer = build_help("label_definition", label="Báo lỗi", snapshot={"label_definitions": {}})

    assert answer.needs_clarify is True
    assert answer.text == LABEL_UNAVAILABLE


def test_label_definition_without_label_offers_options():
    answer = build_help("label_definition", label=None, snapshot=SNAPSHOT)

    assert answer.needs_clarify is True
    assert len(answer.clarify_options) <= MAX_LABEL_OPTIONS
