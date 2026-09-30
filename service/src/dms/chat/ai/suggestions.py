"""Gợi ý câu hỏi tiếp theo, sinh bằng bảng luật (design b05 D10).

Không gọi LLM: gợi ý phải luôn nằm trong khả năng của mốc hiện tại, nên sinh từ
``intents.py`` cộng một bảng mẫu câu.
"""

from __future__ import annotations

from collections.abc import Sequence

from .intents import Intent, is_supported
from .text_match import normalize_match_text

MAX_SUGGESTIONS = 3

# Mẫu câu theo intent; {unit} và {range} lấy từ slot của lượt, thiếu thì bỏ câu đó.
SUGGESTION_TEMPLATES: dict[Intent, tuple[str, ...]] = {
    Intent.OVERVIEW: (
        "Xu hướng phản hồi theo từng ngày {range} thế nào?",
        "Sản phẩm nào bị phản hồi nhiều nhất {range}?",
        "Loại vấn đề nào chiếm nhiều nhất {range}?",
    ),
    Intent.TREND: (
        "Tổng quan {range} ra sao?",
        "Đơn vị nào có nhiều phản hồi nhất {range}?",
        "Cho xem các phản hồi cần ưu tiên xử lý.",
    ),
    Intent.COMPARISON: (
        "Sản phẩm nào bị phản hồi nhiều nhất {range}?",
        "Xu hướng theo từng ngày {range} thế nào?",
        "Loại vấn đề nào tăng nhiều nhất?",
    ),
    Intent.DRILL_PRODUCT: (
        "Các phản hồi về sản phẩm này nói gì?",
        "Loại vấn đề nào hay gặp nhất {range}?",
        "Tổng quan {range} ra sao?",
    ),
    Intent.DRILL_UNIT: (
        "Đơn vị {unit} gặp loại vấn đề nào nhiều nhất?",
        "Xu hướng phản hồi của {unit} theo ngày thế nào?",
        "Tổng quan {range} ra sao?",
    ),
    Intent.DRILL_GEOGRAPHY: (
        "Sản phẩm nào bị phản hồi nhiều nhất ở địa bàn này?",
        "Xu hướng theo từng ngày {range} thế nào?",
        "Tổng quan {range} ra sao?",
    ),
    Intent.DRILL_ISSUE: (
        "Cho xem các phản hồi cần ưu tiên xử lý.",
        "Sản phẩm nào gặp loại vấn đề này nhiều nhất?",
        "Tình trạng xử lý và tồn đọng {range} thế nào?",
    ),
    Intent.LOOKUP_FEEDBACK: (
        "Loại vấn đề nào chiếm nhiều nhất {range}?",
        "Tổng quan {range} ra sao?",
        "Đơn vị nào có nhiều phản hồi nhất {range}?",
    ),
    Intent.HELP: (
        "Tổng quan tháng này ra sao?",
        "Sản phẩm nào bị phản hồi nhiều nhất?",
        "Xu hướng phản hồi theo từng ngày thế nào?",
    ),
}

# Intent mà mỗi mẫu câu sẽ dẫn tới; dùng để lọc theo mốc hiện tại.
_TEMPLATE_INTENT: tuple[tuple[str, Intent], ...] = (
    ("xu hướng", Intent.TREND),
    ("theo từng ngày", Intent.TREND),
    ("sản phẩm nào", Intent.DRILL_PRODUCT),
    ("đơn vị nào", Intent.DRILL_UNIT),
    ("loại vấn đề", Intent.DRILL_ISSUE),
    ("ưu tiên", Intent.DRILL_ISSUE),
    ("tồn đọng", Intent.DRILL_ISSUE),
    ("địa bàn", Intent.DRILL_GEOGRAPHY),
    ("phản hồi", Intent.LOOKUP_FEEDBACK),
    ("tổng quan", Intent.OVERVIEW),
)


def build_suggestions(
    intent: Intent | None,
    *,
    milestone: str = "M1",
    unit: str | None = None,
    range_text: str = "",
    asked_question: str = "",
    limit: int = MAX_SUGGESTIONS,
) -> list[str]:
    """Tối đa ``limit`` gợi ý, chỉ gồm intent đang hỗ trợ, không trùng câu vừa hỏi."""
    templates = SUGGESTION_TEMPLATES.get(intent or Intent.OVERVIEW, ())
    asked = normalize_match_text(asked_question)
    seen: set[str] = set()
    suggestions: list[str] = []

    for template in templates:
        if "{unit}" in template and not unit:
            continue
        text = _fill(template, unit=unit, range_text=range_text)
        if not text:
            continue
        target = _target_intent(text)
        if target is not None and not is_supported(target, milestone):
            continue
        key = normalize_match_text(text)
        if not key or key == asked or key in seen:
            continue
        seen.add(key)
        suggestions.append(text)
        if len(suggestions) >= limit:
            break
    return suggestions


def _fill(template: str, *, unit: str | None, range_text: str) -> str:
    text = template.replace("{unit}", unit or "")
    # Không có khoảng thời gian thì bỏ hẳn placeholder thay vì để chữ "{range}".
    text = text.replace("{range}", range_text or "")
    return " ".join(text.split()).replace(" ?", "?").strip()


def _target_intent(text: str) -> Intent | None:
    lowered = normalize_match_text(text)
    for marker, intent in _TEMPLATE_INTENT:
        if normalize_match_text(marker) in lowered:
            return intent
    return None


def suggestions_for_milestone(milestone: str) -> Sequence[Intent]:
    """Các intent còn dùng được ở mốc hiện tại; test và b07 dùng để kiểm."""
    return tuple(intent for intent in SUGGESTION_TEMPLATES if is_supported(intent, milestone))
