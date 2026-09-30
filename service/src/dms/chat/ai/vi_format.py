"""Định dạng số/ngày tiếng Việt, gom về một nơi (design b05 D4).

Mọi chuỗi người dùng nhìn thấy đều đi qua đây, nên số trong câu trả lời luôn do Python tạo.
"""

from __future__ import annotations

from datetime import date

NOT_ENOUGH_DATA = "Chưa đủ dữ liệu"
DATE_RANGE_SEPARATOR = " – "  # en dash


def format_int(value: int | float | None) -> str:
    """``27554`` → ``"27.554"`` (dấu chấm phân cách hàng nghìn)."""
    if value is None:
        return NOT_ENOUGH_DATA
    return f"{int(round(float(value))):,}".replace(",", ".")


def format_percent(value: int | float | None, *, trim_zero: bool = False) -> str:
    """``12.54`` → ``"12,5%"``; ``trim_zero`` bỏ phần thập phân khi bằng 0 ("23%")."""
    if value is None:
        return NOT_ENOUGH_DATA
    rounded = round(float(value), 1)
    if trim_zero and abs(rounded - int(rounded)) < 1e-9:
        return f"{int(rounded)}%"
    return f"{rounded:.1f}".replace(".", ",") + "%"


def format_date(value: str | date | None) -> str:
    """``"2026-08-01"`` → ``"01/08/2026"``; giá trị lạ thì trả nguyên văn."""
    if value is None or value == "":
        return NOT_ENOUGH_DATA
    if isinstance(value, date):
        parsed = value
    else:
        try:
            parsed = date.fromisoformat(str(value)[:10])
        except ValueError:
            return str(value)
    return f"{parsed.day:02d}/{parsed.month:02d}/{parsed.year}"


def format_date_range(date_from: str | date | None, date_to: str | date | None) -> str:
    """``"01/08/2026 – 31/08/2026"``; thiếu một đầu thì trả đầu còn lại."""
    if not date_from and not date_to:
        return ""
    if not date_to:
        return f"từ {format_date(date_from)}"
    if not date_from:
        return f"đến {format_date(date_to)}"
    return f"{format_date(date_from)}{DATE_RANGE_SEPARATOR}{format_date(date_to)}"


def delta_text(current: int | float | None, previous: int | float | None) -> str:
    """Câu chênh lệch: "tăng 23%", "giảm 5,2%", "không đổi", "tăng từ 0 lên 12"."""
    if current is None or previous is None:
        return NOT_ENOUGH_DATA
    current_value = float(current)
    previous_value = float(previous)
    if previous_value == 0:
        # Không chia cho 0 (spec chat-data-blocks).
        if current_value == 0:
            return "không đổi"
        direction = "tăng" if current_value > 0 else "giảm"
        return f"{direction} từ 0 lên {format_int(abs(current_value))}"
    if current_value == previous_value:
        return "không đổi"
    change = (current_value - previous_value) * 100 / previous_value
    direction = "tăng" if change > 0 else "giảm"
    return f"{direction} {format_percent(abs(change), trim_zero=True)}"


def delta_from_change_percent(change_percent: float | None) -> str:
    """Dùng ``change_percent`` mà ``_comparison()`` của analytics đã tính sẵn."""
    if change_percent is None:
        return NOT_ENOUGH_DATA
    if change_percent == 0:
        return "không đổi"
    direction = "tăng" if change_percent > 0 else "giảm"
    return f"{direction} {format_percent(abs(change_percent), trim_zero=True)}"


def format_metric(metric: dict | None, *, unit: str = "") -> str:
    """Chuỗi hiển thị của một KPI ``{available, value, ...}`` của analytics."""
    if not metric or not metric.get("available"):
        return NOT_ENOUGH_DATA
    value = metric.get("value")
    if value is None:
        return NOT_ENOUGH_DATA
    text = format_percent(value) if _is_rate(metric) else format_int(value)
    return f"{text} {unit}".strip() if unit else text


def _is_rate(metric: dict) -> bool:
    """KPI tỉ lệ có ``numerator`` hoặc tên kết thúc bằng rate/coverage."""
    if "numerator" in metric:
        return True
    value = metric.get("value")
    return isinstance(value, float) and not float(value).is_integer()


def truncate_text(text: str, limit: int) -> str:
    """Cắt nội dung trích dẫn, bỏ ký tự điều khiển, thêm dấu … khi bị cắt."""
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "…"
