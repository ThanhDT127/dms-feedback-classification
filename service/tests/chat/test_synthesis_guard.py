from __future__ import annotations

import logging

import pytest

from dms.chat.ai.fact_sheet import FactSheet, make_fact
from dms.chat.guardrails.synthesis_guard import (
    DropReason,
    SentenceSplitter,
    SynthesisGuard,
    iter_sentences,
)

QUESTION = "Tổng quan tháng 8 của Truyền thống Vùng 1"


def make_sheet() -> FactSheet:
    facts = [
        make_fact("kpi.total_issues", "1.230", 1230),
        make_fact("delta.total_issues", "tăng 23%", 23.0),
        make_fact("rank.1.label", "Đèn LED Bulb", "Đèn LED Bulb"),
        make_fact("rank.1.pct", "12,5%", 12.5),
        make_fact("rank.2.label", "Truyền thống Vùng 1", "Truyền thống Vùng 1"),
        make_fact("q.1", "“Đèn nhấp nháy” (FB-2026-10001)", "FB-2026-10001"),
    ]
    return FactSheet(facts={fact.key: fact for fact in facts})


@pytest.fixture
def guard() -> SynthesisGuard:
    return SynthesisGuard(make_sheet(), question=QUESTION)


# ── 3.1 Tách câu ──


def test_splitter_keeps_abbreviation_and_decimal_together():
    chunks = ["Tỉ lệ {{rank.1.pct}} tại TP.HCM là cao nhất. Tiếp theo ", "là {{rank.2.label}}."]

    sentences = list(iter_sentences(chunks))

    # "Tiếp theo" nằm cuối chunk đầu nhưng thuộc về câu sau.
    assert sentences == [
        "Tỉ lệ {{rank.1.pct}} tại TP.HCM là cao nhất.",
        "Tiếp theo là {{rank.2.label}}.",
    ]


def test_splitter_does_not_cut_inside_placeholder():
    sentences = list(iter_sentences(["Tổng {{kpi.total", "_issues}} vấn đề. Xong."]))

    assert sentences == ["Tổng {{kpi.total_issues}} vấn đề.", "Xong."]


def test_splitter_does_not_cut_between_digits():
    sentences = list(iter_sentences(["Chỉ số 1.5 điểm là ổn. Hết."]))

    assert sentences == ["Chỉ số 1.5 điểm là ổn.", "Hết."]


def test_splitter_cuts_on_newline_and_flushes_remainder():
    splitter = SentenceSplitter()

    first = splitter.feed("Câu một\nCâu hai. ")
    rest = splitter.flush()

    assert first == ["Câu một", "Câu hai."]
    assert rest == []


def test_splitter_flush_returns_unterminated_tail():
    splitter = SentenceSplitter()
    assert splitter.feed("Câu chưa có dấu chấm") == []
    assert splitter.flush() == ["Câu chưa có dấu chấm"]


# ── 3.2 / 3.3 Kiểm câu ──


def test_valid_sentence_is_rendered_with_display_values(guard):
    result = guard.check("Số vấn đề {{delta.total_issues}} so với kỳ trước.")

    assert result.ok is True
    assert result.text == "Số vấn đề tăng 23% so với kỳ trước."


def test_number_from_the_question_is_allowed(guard):
    result = guard.check("Trong tháng 8, {{rank.1.label}} dẫn đầu.")

    assert result.ok is True
    assert result.text == "Trong tháng 8, Đèn LED Bulb dẫn đầu."


def test_number_inside_entity_name_is_allowed(guard):
    result = guard.check("Đơn vị {{rank.2.label}} có nhiều phản hồi.")

    assert result.ok is True


def test_unknown_placeholder_is_dropped(guard):
    result = guard.check("{{kpi.revenue}} là cao nhất.")

    assert result.ok is False
    assert result.reason is DropReason.UNKNOWN_FACT


def test_self_written_number_is_dropped(guard):
    result = guard.check("Số vấn đề tăng 30% so với kỳ trước.")

    assert result.ok is False
    assert result.reason is DropReason.UNGROUNDED_NUMBER


def test_leak_is_dropped(guard):
    result = guard.check("Dữ liệu lấy từ get_products.")

    assert result.ok is False
    assert result.reason is DropReason.LEAK


def test_dropped_sentence_logs_only_the_reason(guard, caplog):
    with caplog.at_level(logging.INFO, logger="dms-chat-synthesis"):
        guard.check("Số vấn đề tăng 30% so với kỳ trước.")

    record = next(r for r in caplog.records if r.message == "synthesis_sentence_dropped")
    assert record.reason == "UNGROUNDED_NUMBER"
    assert "30%" not in record.getMessage()


@pytest.mark.parametrize(
    ("sentence", "reason"),
    [
        ("Tổng cộng 4.567 vấn đề trong kỳ.", DropReason.UNGROUNDED_NUMBER),
        ("Tỉ lệ xử lý đạt 91,2% trong tháng.", DropReason.UNGROUNDED_NUMBER),
        ("Có 15 đơn vị bị ảnh hưởng.", DropReason.UNGROUNDED_NUMBER),
        ("Ngày 12/08/2026 ghi nhận nhiều nhất.", DropReason.UNGROUNDED_NUMBER),
        ("Giảm 7 điểm so với kỳ trước.", DropReason.UNGROUNDED_NUMBER),
        ("Mã FB-2026-99999 là nghiêm trọng nhất.", DropReason.UNGROUNDED_CODE),
        ("Xem thêm mã AB-12 để biết chi tiết.", DropReason.UNGROUNDED_CODE),
        ("Dữ liệu lấy từ bảng feedback_records.", DropReason.LEAK),
        ("Truy vấn SELECT count(*) cho kết quả này.", DropReason.LEAK),
        ("Nguồn là v_issues_current của hệ thống.", DropReason.LEAK),
        ("Hàm get_overview đã trả về dữ liệu.", DropReason.LEAK),
        ("Trường raw_data_json chứa thông tin gốc.", DropReason.LEAK),
        ("Câu lệnh sql đã chạy xong.", DropReason.LEAK),
        ("{{kpi.doanh_thu}} tăng mạnh.", DropReason.UNKNOWN_FACT),
        ("{{rank.9.label}} đứng đầu bảng.", DropReason.UNKNOWN_FACT),
        ("Ừ.", DropReason.BAD_LENGTH),
        ("Nhận định rất dài. " * 40, DropReason.BAD_LENGTH),
    ],
)
def test_adversarial_sentences_are_dropped(guard, sentence, reason):
    result = guard.check(sentence)

    assert result.ok is False
    assert result.reason is reason


def test_check_order_puts_unknown_fact_before_number(guard):
    # Câu vừa có placeholder bịa vừa có số tự viết: báo UNKNOWN_FACT trước.
    result = guard.check("{{kpi.revenue}} tăng 30%.")

    assert result.reason is DropReason.UNKNOWN_FACT


def test_render_leaves_unknown_placeholder_untouched(guard):
    assert guard.render("{{kpi.revenue}} là bao nhiêu?") == "{{kpi.revenue}} là bao nhiêu?"
    assert guard.render("Tổng {{kpi.total_issues}}.") == "Tổng 1.230."


# ── Fact của Pattern 2 nằm dưới namespace "sql" (b09 D8) ──


def sql_guard() -> SynthesisGuard:
    """Fact sheet của một bảng Pattern 2: khoá bắt đầu bằng chính chữ ``sql``."""
    facts = [
        make_fact("sql.r1.product", "Bán nguyệt", "Bán nguyệt"),
        make_fact("sql.r1.so_van_de", "57", 57),
        make_fact("sql.r2.product", "Ấm siêu tốc", "Ấm siêu tốc"),
        make_fact("sql.r2.so_van_de", "32", 32),
    ]
    return SynthesisGuard(
        FactSheet(facts={fact.key: fact for fact in facts}),
        question="Sản phẩm nào bị Báo lỗi nhiều nhất trong tháng 8/2026?",
    )


def test_sentence_citing_a_pattern2_fact_is_kept():
    """Hồi quy: khoá ``{{sql.*}}`` từng khớp LEAK_MARKERS nên mọi câu trích số đều bị loại."""
    result = sql_guard().check(
        "Sản phẩm {{sql.r1.product}} có nhiều báo lỗi nhất với {{sql.r1.so_van_de}} vấn đề."
    )

    assert result.ok is True, result.reason
    assert result.text == "Sản phẩm Bán nguyệt có nhiều báo lỗi nhất với 57 vấn đề."


def test_several_pattern2_facts_in_one_sentence_are_kept():
    result = sql_guard().check("Tiếp theo là {{sql.r2.product}} với {{sql.r2.so_van_de}} vấn đề.")

    assert result.ok is True, result.reason
    assert result.text == "Tiếp theo là Ấm siêu tốc với 32 vấn đề."


def test_real_leak_is_still_dropped_even_with_pattern2_facts():
    """Nới lỏng ở trên không được làm mất khả năng bắt rò rỉ thật trong văn xuôi."""
    result = sql_guard().check(
        "Chạy câu lệnh sql trên {{sql.r1.product}} cho ra {{sql.r1.so_van_de}}."
    )

    assert result.ok is False
    assert result.reason is DropReason.LEAK


def test_leak_hidden_after_a_placeholder_is_still_dropped():
    result = sql_guard().check("{{sql.r1.product}} lấy từ bảng feedback_records.")

    assert result.ok is False
    assert result.reason is DropReason.LEAK
