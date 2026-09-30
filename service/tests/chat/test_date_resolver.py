from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from dms.chat.ai.date_resolver import DateResolver, resolve_timezone
from dms.chat.ai.types import QueryIssue

from .ai_fakes import FixedClock

# 2026-09-15 10:00 giờ Việt Nam, thứ Ba.
ANCHOR = FixedClock(datetime(2026, 9, 15, 3, 0, tzinfo=UTC))
resolver = DateResolver(ANCHOR)


def d(value: str) -> date:
    return date.fromisoformat(value)


@pytest.mark.parametrize(
    ("text", "start", "end"),
    [
        ("hôm nay", "2026-09-15", "2026-09-15"),
        ("hôm qua", "2026-09-14", "2026-09-14"),
        ("tuần này", "2026-09-14", "2026-09-15"),
        ("tuần trước", "2026-09-07", "2026-09-13"),
        ("tháng này", "2026-09-01", "2026-09-15"),
        ("tháng trước", "2026-08-01", "2026-08-31"),
        ("7 ngày qua", "2026-09-09", "2026-09-15"),
        ("3 tháng gần nhất", "2026-06-16", "2026-09-15"),
        ("quý 3", "2026-07-01", "2026-09-15"),
        ("Q3", "2026-07-01", "2026-09-15"),
        ("quý III", "2026-07-01", "2026-09-15"),
        ("quý trước", "2026-04-01", "2026-06-30"),
        ("năm ngoái", "2025-01-01", "2025-12-31"),
        ("năm nay", "2026-01-01", "2026-09-15"),
        ("năm 2025", "2025-01-01", "2025-12-31"),
        ("tháng 8", "2026-08-01", "2026-08-31"),
        ("T8", "2026-08-01", "2026-08-31"),
        ("th8", "2026-08-01", "2026-08-31"),
        ("tháng 8/2025", "2025-08-01", "2025-08-31"),
        ("tháng 2 năm 2024", "2024-02-01", "2024-02-29"),
        ("từ 01/08 đến 15/08", "2026-08-01", "2026-08-15"),
        ("từ tháng 6 đến tháng 8", "2026-06-01", "2026-08-31"),
        ("phản hồi ngày 15/08/2026", "2026-08-15", "2026-08-15"),
        ("phản hồi ngày 02-09-2026", "2026-09-02", "2026-09-02"),
        ("ngày 2 tháng 9", "2026-09-02", "2026-09-02"),
        ("6 tháng đầu năm", "2026-01-01", "2026-06-30"),
        ("6 tháng cuối năm 2025", "2025-07-01", "2025-12-31"),
    ],
)
def test_single_period(text, start, end):
    result = resolver.resolve(text)
    assert result.issues == ()
    assert result.date_range is not None, text
    assert (result.date_range.date_from, result.date_range.date_to) == (d(start), d(end))
    assert result.compare_range is None


def test_future_month_means_previous_year_with_assumption():
    result = resolver.resolve("tháng 10")
    assert result.date_range is not None
    assert (result.date_range.date_from, result.date_range.date_to) == (
        d("2025-10-01"),
        d("2025-10-31"),
    )
    assert any("10/2025" in a for a in result.assumptions)


def test_future_quarter_means_previous_year():
    result = resolver.resolve("quý 4")
    assert result.date_range is not None
    assert result.date_range.date_from == d("2025-10-01")
    assert result.assumptions


def test_two_periods_later_is_primary():
    """Kỳ sau là kỳ đang xem để "kỳ trước"/"tăng, giảm" đọc đúng chiều thời gian."""
    result = resolver.resolve("So sánh Q2 vs Q3")
    assert result.date_range is not None and result.compare_range is not None
    assert (result.date_range.date_from, result.date_range.date_to) == (
        d("2026-07-01"),
        d("2026-09-15"),
    )
    assert (result.compare_range.date_from, result.compare_range.date_to) == (
        d("2026-04-01"),
        d("2026-06-30"),
    )


def test_two_months_earlier_first_are_swapped():
    result = resolver.resolve("So sánh tháng 3/2026 với tháng 4/2026")
    assert result.date_range is not None and result.compare_range is not None
    assert result.date_range.date_from == d("2026-04-01")
    assert result.compare_range.date_from == d("2026-03-01")


def test_later_period_first_keeps_order():
    result = resolver.resolve("Tháng 8 so với tháng 7")
    assert result.date_range is not None and result.compare_range is not None
    assert result.date_range.date_from == d("2026-08-01")
    assert result.compare_range.date_from == d("2026-07-01")


@pytest.mark.parametrize(
    "text",
    ["So sánh từ đầu tháng 8", "Tổng quan từ 01/08", "phan hoi tu thang 6"],
)
def test_open_start_without_end_is_flagged(text):
    assert QueryIssue.MISSING_END_DATE in resolver.resolve(text).issues


@pytest.mark.parametrize(
    "text",
    [
        "Báo cáo từ 01/01/2026 đến 31/08/2026",
        "từ tháng 3 đến tháng 5",
        "Tổng quan tháng 8",
        "Tổng quan từ đầu năm đến nay",
    ],
)
def test_closed_or_plain_ranges_are_not_flagged(text):
    assert QueryIssue.MISSING_END_DATE not in resolver.resolve(text).issues


def test_same_period_last_month():
    result = resolver.resolve("tháng này so với cùng kỳ tháng trước")
    assert result.date_range is not None and result.compare_range is not None
    assert (result.date_range.date_from, result.date_range.date_to) == (
        d("2026-09-01"),
        d("2026-09-15"),
    )
    assert (result.compare_range.date_from, result.compare_range.date_to) == (
        d("2026-08-01"),
        d("2026-08-15"),
    )


def test_same_period_defaults_to_last_year():
    result = resolver.resolve("tháng 8 so với cùng kỳ")
    assert result.compare_range is not None
    assert (result.compare_range.date_from, result.compare_range.date_to) == (
        d("2025-08-01"),
        d("2025-08-31"),
    )
    assert any("năm trước" in a for a in result.assumptions)


def test_vague_period_uses_30_days_with_assumption():
    result = resolver.resolve("Gần đây có nhiều phản hồi tiêu cực không?")
    assert result.date_range is not None
    assert (result.date_range.date_from, result.date_range.date_to) == (
        d("2026-08-17"),
        d("2026-09-15"),
    )
    assert any("17/08" in a and "15/09/2026" in a for a in result.assumptions)


def test_no_time_mention():
    result = resolver.resolve("Sản phẩm nào bị báo lỗi nhiều nhất?")
    assert result.date_range is None and result.compare_range is None
    assert result.issues == ()


@pytest.mark.parametrize(
    "text",
    ["phản hồi ngày 31/02/2026", "từ 20/08/2026 đến 10/08/2026", "ngày 30 tháng 2 năm 2026"],
)
def test_invalid_dates_are_flagged(text):
    result = resolver.resolve(text)
    assert result.date_range is None
    assert result.issues == (QueryIssue.INVALID_DATE,)


def test_timezone_boundary_uses_vietnam_date():
    late_utc = DateResolver(FixedClock(datetime(2026, 9, 14, 18, 30, tzinfo=UTC)))
    result = late_utc.resolve("hôm nay")
    assert result.date_range is not None
    assert result.date_range.date_from == d("2026-09-15")


def test_naive_clock_is_treated_as_utc():
    naive = DateResolver(FixedClock(datetime(2026, 9, 14, 18, 30)))
    assert naive.today() == d("2026-09-15")


def test_timezone_fallback_is_utc_plus_7():
    tz = resolve_timezone("Không/Tồn_Tại")
    assert tz.utcoffset(None) == timedelta(hours=7)


def test_unit_number_is_not_a_date():
    result = resolver.resolve("số liệu Truyền thống Vùng 1/2")
    assert result.date_range is None and result.issues == ()
