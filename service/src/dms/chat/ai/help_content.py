"""Nội dung trả lời cho intent HELP (design b05 D10).

Không gọi LLM: hướng dẫn sinh từ ``intents.py`` theo mốc hiện tại, còn định nghĩa nhãn lấy
thẳng từ cấu hình phân loại đang chạy.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .intents import INTENT_SPECS, Intent, is_supported
from .text_match import normalize_match_text, rank_values

MAX_LABEL_OPTIONS = 5
DEFAULT_MATCH_THRESHOLD = 0.88

USAGE_HEADER = "Tôi trả lời các câu hỏi về dữ liệu phản hồi khách hàng:"
USAGE_FOOTER = (
    "Bạn có thể nêu kèm khoảng thời gian, đơn vị, sản phẩm hoặc loại vấn đề để tôi lọc đúng hơn."
)
LABEL_NOT_FOUND = "Tôi không tìm thấy nhãn nào tên là “{label}”. Ý bạn là một trong các nhãn sau?"
LABEL_UNAVAILABLE = "Hiện chưa có định nghĩa nhãn nào trong cấu hình phân loại."


@dataclass(frozen=True)
class HelpAnswer:
    text: str
    clarify_options: tuple[str, ...] = ()
    needs_clarify: bool = False


def build_help(
    topic: str | None,
    *,
    label: str | None = None,
    milestone: str = "M1",
    snapshot: Mapping[str, object] | None = None,
    threshold: float = DEFAULT_MATCH_THRESHOLD,
) -> HelpAnswer:
    """``topic`` = ``usage`` (mặc định) hoặc ``label_definition``."""
    if (topic or "usage") == "label_definition":
        return _label_definition(label, snapshot=snapshot, threshold=threshold)
    return HelpAnswer(text=usage_text(milestone))


def usage_text(milestone: str = "M1") -> str:
    """Danh sách việc hỏi được ở mốc hiện tại, sinh từ taxonomy intent."""
    lines = [USAGE_HEADER]
    for intent in Intent:
        spec = INTENT_SPECS[intent]
        if intent is Intent.HELP or not is_supported(intent, milestone):
            continue
        example = spec.examples[0] if spec.examples else spec.description_vi
        lines.append(f"- {spec.description_vi} Ví dụ: “{example}”")
    lines.append(USAGE_FOOTER)
    return "\n".join(lines)


def _label_definition(
    label: str | None,
    *,
    snapshot: Mapping[str, object] | None,
    threshold: float,
) -> HelpAnswer:
    definitions = _label_definitions(snapshot)
    if not definitions:
        return HelpAnswer(text=LABEL_UNAVAILABLE, needs_clarify=True)

    names = list(definitions)
    mention = (label or "").strip()
    if mention:
        normalized = normalize_match_text(mention)
        for name in names:
            if normalize_match_text(name) == normalized:
                return HelpAnswer(text=_format_definition(name, definitions[name]))

        matches = rank_values(mention, names, threshold, limit=MAX_LABEL_OPTIONS)
        if matches:
            best = matches[0].value
            return HelpAnswer(text=_format_definition(best, definitions[best]))

    options = _nearest_labels(mention, names)
    return HelpAnswer(
        text=LABEL_NOT_FOUND.format(label=mention or "…"),
        clarify_options=options,
        needs_clarify=True,
    )


def _label_definitions(snapshot: Mapping[str, object] | None) -> dict[str, str]:
    data = snapshot if snapshot is not None else _live_snapshot()
    definitions = data.get("label_definitions") if isinstance(data, Mapping) else None
    if not isinstance(definitions, Mapping):
        return {}
    return {str(name): str(text) for name, text in definitions.items() if str(text).strip()}


def _live_snapshot() -> Mapping[str, object]:
    # Import muộn: module phân loại kéo theo mô hình, không nên nạp khi chỉ chào hỏi.
    from ...pipeline.issue_classifier import get_label_config_snapshot

    return get_label_config_snapshot()


def _nearest_labels(mention: str, names: list[str]) -> tuple[str, ...]:
    if not mention:
        return tuple(names[:MAX_LABEL_OPTIONS])
    ranked = rank_values(mention, names, 0.0, limit=MAX_LABEL_OPTIONS)
    if ranked:
        return tuple(match.value for match in ranked)
    return tuple(names[:MAX_LABEL_OPTIONS])


def _format_definition(name: str, definition: str) -> str:
    return f"{name}: {definition}"
