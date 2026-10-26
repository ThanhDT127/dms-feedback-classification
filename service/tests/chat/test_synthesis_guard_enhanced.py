from __future__ import annotations

import pytest

from dms.chat.ai.block_builders import DataBlock
from dms.chat.ai.fact_sheet import FactSheet, build_fact_sheet, make_fact
from dms.chat.guardrails.synthesis_guard import DropReason, SynthesisGuard


def make_enhanced_sheet() -> FactSheet:
    facts = [
        make_fact("kpi.total_issues", "1.230", 1230),
        make_fact("delta.total_issues", "tăng 23%", 23.0),
        make_fact("rank.1.label", "Đèn LED Bulb", "Đèn LED Bulb"),
        make_fact("rank.1.pct", "12,5%", 12.5),
        make_fact("rank.2.label", "Truyền thống Vùng 1", "Truyền thống Vùng 1"),
        make_fact("q.1", "“Đèn nhấp nháy liên tục khi bật” (FB-2026-10001)", "FB-2026-10001"),
        make_fact("q.2", "“Bóng bị vỡ đui khi giao hàng” (254198)", "254198"),
        make_fact("range.current", "01/08/2026..31/08/2026", "2026-08-01..2026-08-31"),
        make_fact("trend.max.date", "15/08/2026", "2026-08-15"),
    ]
    return FactSheet(facts={fact.key: fact for fact in facts})


@pytest.fixture
def enhanced_guard() -> SynthesisGuard:
    return SynthesisGuard(make_enhanced_sheet(), question="Tình hình phản hồi sản phẩm tháng 8")


# ── 1. Issue Code Validation ──


def test_grounded_issue_code_allowed(enhanced_guard):
    result = enhanced_guard.check(
        "Khách hàng phàn nàn đèn nhấp nháy theo phản hồi (FB-2026-10001)."
    )
    assert result.ok is True, result.reason
    assert "(FB-2026-10001)" in result.text


def test_numeric_quote_raw_not_allowed_as_bare_number(enhanced_guard):
    # Numeric raw string "254198" from q.2 must NOT be allowed as bare ungrounded number
    result = enhanced_guard.check(
        "Ghi nhận phản hồi (254198) về đui đèn bị vỡ."
    )
    assert result.ok is False
    assert result.reason is DropReason.UNGROUNDED_NUMBER


def test_ungrounded_issue_code_rejected(enhanced_guard):
    result = enhanced_guard.check(
        "Mã FB-2026-99999 là vấn đề nghiêm trọng nhất."
    )
    assert result.ok is False
    assert result.reason is DropReason.UNGROUNDED_CODE


def test_issue_code_from_question_allowed():
    sheet = FactSheet(facts={"kpi.total_issues": make_fact("kpi.total_issues", "10", 10)})
    guard = SynthesisGuard(sheet, question="Chi tiết mã FB-2026-55555")
    result = guard.check("Vấn đề (FB-2026-55555) có {{kpi.total_issues}} phản hồi liên quan.")
    assert result.ok is True, result.reason


def test_multi_step_block2_numbers_rejected_without_placeholder():
    b1 = DataBlock(
        block_id="b1",
        kind="kpi",
        title="K1",
        subtitle="",
        payload={"items": [{"key": "tot", "value": 100, "display": "100", "available": True}]},
    )
    b2 = DataBlock(
        block_id="b2",
        kind="kpi",
        title="K2",
        subtitle="",
        payload={"items": [{"key": "tot", "value": 250, "display": "250", "available": True}]},
    )
    sheet = build_fact_sheet([b1, b2])
    guard = SynthesisGuard(sheet, question="So sánh")
    assert "250" not in guard.allowed_codes
    assert "250" not in guard.allowed_tokens
    result = guard.check("Kỳ sau có 250 vấn đề.")
    assert result.ok is False
    assert result.reason is DropReason.UNGROUNDED_NUMBER


def test_sql_cell_numbers_rejected_without_placeholder():
    sheet = FactSheet(facts={"sql.r1.tong_so": make_fact("sql.r1.tong_so", "9999", 9999)})
    guard = SynthesisGuard(sheet, question="Đếm số")
    assert "9999" not in guard.allowed_codes
    assert "9999" not in guard.allowed_tokens
    result = guard.check("Tổng số là 9999.")
    assert result.ok is False
    assert result.reason is DropReason.UNGROUNDED_NUMBER


def test_issue_code_subnumbers_not_leaked_to_allowed_tokens(enhanced_guard):
    # FB-2026-10001 is in allowed_codes, but sub-number 10001 must not leak into allowed_tokens
    assert "10001" not in enhanced_guard.allowed_tokens
    result = enhanced_guard.check("Có 10001 sản phẩm bị hỏng.")
    assert result.ok is False
    assert result.reason is DropReason.UNGROUNDED_NUMBER


# ── 2. Ordinals & Small Counts ──


def test_ordinals_top_n_allowed(enhanced_guard):
    result = enhanced_guard.check(
        "Nằm trong top 3 sản phẩm có nhiều phản hồi nhất là {{rank.1.label}}."
    )
    assert result.ok is True, result.reason
    assert "top 3" in result.text


def test_small_counts_allowed(enhanced_guard):
    result = enhanced_guard.check(
        "Có 2 nhóm vấn đề chính xuất hiện trong kỳ."
    )
    assert result.ok is True, result.reason
    assert "2 nhóm" in result.text


def test_large_count_rejected(enhanced_guard):
    result = enhanced_guard.check(
        "Có 25 nhóm vấn đề khác nhau phát sinh."
    )
    assert result.ok is False
    assert result.reason is DropReason.UNGROUNDED_NUMBER


# ── 3. Timeframes & Date Facts ──


def test_timeframe_month_and_year_allowed(enhanced_guard):
    result = enhanced_guard.check(
        "Trong tháng 10 năm 2026 ghi nhận số lượng tăng."
    )
    assert result.ok is True, result.reason


def test_quarter_allowed(enhanced_guard):
    result = enhanced_guard.check(
        "Số liệu quý 3 phản ánh tình hình ổn định."
    )
    assert result.ok is True, result.reason


def test_numeric_tokens_from_date_facts_allowed(enhanced_guard):
    # trend.max.date is 15/08/2026 -> 15 is allowed
    result = enhanced_guard.check(
        "Ngày 15 có nhiều phản hồi nhất theo ghi nhận."
    )
    assert result.ok is True, result.reason


# ── 4. Percentage Facts ──


def test_valid_percentage_allowed(enhanced_guard):
    # 23% and 12,5% exist in FactSheet
    result = enhanced_guard.check(
        "Tỉ lệ tăng 23% so với kỳ trước."
    )
    assert result.ok is True, result.reason


def test_invalid_percentage_rejected(enhanced_guard):
    # 45% does not exist in FactSheet
    result = enhanced_guard.check(
        "Tỉ lệ tăng 45% so với kỳ trước."
    )
    assert result.ok is False
    assert result.reason is DropReason.UNGROUNDED_NUMBER


# ── 5. Sentence Length (MAX_SENTENCE_CHARS = 400) ──


def test_sentence_length_350_chars_allowed(enhanced_guard):
    # Create a valid sentence with length around 350 chars
    filler = "Đơn vị {{rank.2.label}} ghi nhận các phản hồi tích cực từ phía người tiêu dùng và tiếp tục theo dõi sát sao tiến độ xử lý khiếu nại nhằm nâng cao chất lượng dịch vụ khách hàng trên toàn quốc một cách hiệu quả và kịp thời"
    # Rendered length is ~240 chars, expand to ~350
    expanded = filler + ", đồng thời đảm bảo quyền lợi tốt nhất cho người tiêu dùng theo phản hồi (FB-2026-10001)."
    result = enhanced_guard.check(expanded)
    assert result.ok is True, result.reason
    assert 300 < len(result.text) <= 400


def test_sentence_length_over_400_chars_rejected(enhanced_guard):
    long_sentence = "Nhận định rất dài vượt quá bốn trăm ký tự cho phép của hệ thống. " * 8
    result = enhanced_guard.check(long_sentence)
    assert result.ok is False
    assert result.reason is DropReason.BAD_LENGTH


# ── 6. FactSheet Unprefixed Fallback for Quotes ──


def test_fact_sheet_unprefixed_quote_access():
    block1 = DataBlock(
        block_id="b1",
        kind="kpi",
        title="KPI",
        subtitle="",
        payload={"items": [{"key": "total", "value": 100, "display": "100", "available": True}]},
    )
    block2 = DataBlock(
        block_id="b2",
        kind="quote",
        title="Trích dẫn",
        subtitle="",
        payload={
            "quotes": [
                {"issue_code": "FB-2026-77777", "content": "Sản phẩm hỏng", "unit_name": "Vùng 1"}
            ]
        },
    )
    sheet = build_fact_sheet([block1, block2])
    # Both s2.q.1 and q.1 must be present
    assert "s2.q.1" in sheet
    assert "q.1" in sheet
    assert sheet.get("q.1").raw == "FB-2026-77777"
