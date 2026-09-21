from __future__ import annotations

from datetime import date

import pytest

from dms.chat.ai.vi_format import (
    NOT_ENOUGH_DATA,
    delta_from_change_percent,
    delta_text,
    format_date,
    format_date_range,
    format_int,
    format_metric,
    format_percent,
    truncate_text,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(27554, "27.554"), (0, "0"), (1234567, "1.234.567"), (12, "12"), (None, NOT_ENOUGH_DATA)],
)
def test_format_int(value, expected):
    assert format_int(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(12.54, "12,5%"), (0.0, "0,0%"), (100, "100,0%"), (None, NOT_ENOUGH_DATA)],
)
def test_format_percent(value, expected):
    assert format_percent(value) == expected


def test_format_percent_can_trim_trailing_zero():
    assert format_percent(23.0, trim_zero=True) == "23%"
    assert format_percent(5.2, trim_zero=True) == "5,2%"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-08-01", "01/08/2026"),
        (date(2026, 12, 31), "31/12/2026"),
        ("2026-08-01T10:00:00", "01/08/2026"),
        ("không phải ngày", "không phải ngày"),
    ],
)
def test_format_date(value, expected):
    assert format_date(value) == expected


def test_format_date_range():
    assert format_date_range("2026-08-01", "2026-08-31") == "01/08/2026 – 31/08/2026"
    assert format_date_range("2026-08-01", None) == "từ 01/08/2026"
    assert format_date_range(None, "2026-08-31") == "đến 31/08/2026"
    assert format_date_range(None, None) == ""


@pytest.mark.parametrize(
    ("current", "previous", "expected"),
    [
        (1230, 1000, "tăng 23%"),
        (948, 1000, "giảm 5,2%"),
        (100, 100, "không đổi"),
        (12, 0, "tăng từ 0 lên 12"),  # không chia cho 0
        (0, 0, "không đổi"),
        (None, 10, NOT_ENOUGH_DATA),
    ],
)
def test_delta_text(current, previous, expected):
    assert delta_text(current, previous) == expected


def test_delta_from_change_percent_matches_analytics_output():
    assert delta_from_change_percent(23.0) == "tăng 23%"
    assert delta_from_change_percent(-5.2) == "giảm 5,2%"
    assert delta_from_change_percent(0) == "không đổi"
    assert delta_from_change_percent(None) == NOT_ENOUGH_DATA


def test_format_metric_follows_kpi_semantics():
    count_metric = {"available": True, "value": 1234, "denominator": 1234}
    rate_metric = {"available": True, "value": 12.54, "denominator": 100, "numerator": 12}
    unavailable = {"available": False, "value": None, "reason": "no issue codes"}

    assert format_metric(count_metric) == "1.234"
    assert format_metric(count_metric, unit="vấn đề") == "1.234 vấn đề"
    assert format_metric(rate_metric) == "12,5%"
    assert format_metric(unavailable) == NOT_ENOUGH_DATA
    assert format_metric(None) == NOT_ENOUGH_DATA


def test_truncate_text_cuts_and_cleans():
    assert truncate_text("  nhiều   khoảng \n trắng ", 100) == "nhiều khoảng trắng"
    long_text = "x" * 1000
    cut = truncate_text(long_text, 240)
    assert len(cut) == 241
    assert cut.endswith("…")
