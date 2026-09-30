"""Input Guard 2 lớp cho câu hỏi chat (spec ``chat-input-guard``, design b01 D5).

Chỉ dùng luật xác định, không gọi LLM. Lớp 1 chạy trên câu gốc trước mọi lần gọi LLM;
lớp 2 chạy lại cùng bộ luật trên câu đã được Contextualizer viết lại.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..ai.text_match import match_tokens, normalize_match_text, phrase_starts
from ..ai.types import GuardAction, GuardDecision, GuardLayer, GuardReason

RULES_PATH = Path(__file__).resolve().parent / "input_guard_rules.json"

REFUSAL_TEMPLATES: dict[GuardReason, str] = {
    GuardReason.WRITE_REQUEST: (
        "Trợ lý chỉ hỗ trợ tra cứu và thống kê dữ liệu phản hồi, không thể thêm, sửa hay xoá "
        "dữ liệu. Bạn có thể hỏi về số liệu, xu hướng hoặc nội dung phản hồi."
    ),
    GuardReason.SECRET_REQUEST: (
        "Trợ lý không cung cấp thông tin bảo mật của hệ thống như khoá truy cập, mật khẩu hay "
        "cấu hình kết nối."
    ),
    GuardReason.PROMPT_INJECTION: (
        "Câu hỏi có nội dung trợ lý không hỗ trợ. Vui lòng hỏi về dữ liệu phản hồi, "
        "ví dụ: “Tháng 8 có bao nhiêu vấn đề?”"
    ),
    GuardReason.INVALID_INPUT: (
        "Bạn chưa nhập câu hỏi. Hãy hỏi về dữ liệu phản hồi, ví dụ: “Tháng 8 có bao nhiêu vấn đề?”"
    ),
    GuardReason.INPUT_TOO_LONG: "Câu hỏi quá dài. Vui lòng rút gọn và hỏi từng ý một.",
}

Phrase = tuple[str, ...]


@dataclass(frozen=True)
class GuardRules:
    version: int
    max_gap_tokens: int
    write_verbs: tuple[Phrase, ...]
    verb_exceptions: tuple[Phrase, ...]
    write_objects: tuple[Phrase, ...]
    object_exceptions: tuple[Phrase, ...]
    topic_prefixes: tuple[Phrase, ...]
    question_markers: tuple[Phrase, ...]
    question_marker_verbs: frozenset[Phrase]
    secret_terms: tuple[Phrase, ...]
    secret_contexts: tuple[Phrase, ...]
    injection_patterns: tuple[tuple[str, re.Pattern[str]], ...]


def _phrases(items: Sequence[str]) -> tuple[Phrase, ...]:
    return tuple(p for p in (tuple(match_tokens(item)) for item in items) if p)


def load_rules(path: Path = RULES_PATH) -> GuardRules:
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    write = raw["write_request"]
    secret = raw["secret_request"]
    return GuardRules(
        version=int(raw.get("version", 1)),
        max_gap_tokens=int(write.get("max_gap_tokens", 4)),
        write_verbs=_phrases(write["verbs"]),
        verb_exceptions=_phrases(write.get("verb_exceptions", [])),
        write_objects=_phrases(write["objects"]),
        object_exceptions=_phrases(write.get("object_exceptions", [])),
        topic_prefixes=_phrases(write.get("topic_prefixes", [])),
        question_markers=_phrases(write.get("question_markers", [])),
        question_marker_verbs=frozenset(_phrases(write.get("question_marker_verbs", []))),
        secret_terms=_phrases(secret["terms"]),
        secret_contexts=_phrases(secret["contexts"]),
        injection_patterns=tuple(
            (str(item["id"]), re.compile(str(item["regex"]))) for item in raw["prompt_injection"]
        ),
    )


_DEFAULT_RULES = load_rules()


def _covers(tokens: Sequence[str], phrases: Sequence[Phrase], index: int) -> bool:
    """Có cụm nào trong ``phrases`` phủ lên vị trí ``index`` không."""
    for phrase in phrases:
        for start in phrase_starts(tokens, phrase):
            if start <= index < start + len(phrase):
                return True
    return False


def _starts_with_any(tokens: Sequence[str], phrases: Sequence[Phrase], index: int) -> bool:
    return any(index in phrase_starts(tokens, phrase) for phrase in phrases)


class InputGuard:
    def __init__(self, *, max_chars: int, rules: GuardRules | None = None) -> None:
        self.max_chars = max_chars
        self.rules = rules or _DEFAULT_RULES

    def check(self, text: str | None, *, layer: GuardLayer = GuardLayer.RAW) -> GuardDecision:
        raw = text or ""
        if not raw.strip():
            if layer is GuardLayer.REWRITTEN:
                return GuardDecision.allow(layer)
            return self._refuse(GuardReason.INVALID_INPUT, layer, "invalid_input")
        if layer is GuardLayer.RAW and len(raw) > self.max_chars:
            return self._refuse(GuardReason.INPUT_TOO_LONG, layer, "input_too_long")

        lowered = " ".join(raw.lower().split())
        normalized = normalize_match_text(raw)
        for rule_id, pattern in self.rules.injection_patterns:
            if pattern.search(lowered) or pattern.search(normalized):
                return self._refuse(GuardReason.PROMPT_INJECTION, layer, rule_id)

        tokens = match_tokens(raw)
        write_rule = self._match_write(tokens)
        if write_rule:
            return self._refuse(GuardReason.WRITE_REQUEST, layer, write_rule)
        secret_rule = self._match_secret(tokens)
        if secret_rule:
            return self._refuse(GuardReason.SECRET_REQUEST, layer, secret_rule)
        return GuardDecision.allow(layer)

    def _match_write(self, tokens: Sequence[str]) -> str | None:
        """Động từ thao tác, rồi tới đối tượng dữ liệu cách tối đa ``max_gap_tokens`` token."""
        rules = self.rules
        object_starts = sorted(
            {
                start
                for phrase in rules.write_objects
                for start in phrase_starts(tokens, phrase)
                if not _starts_with_any(tokens, rules.object_exceptions, start)
            }
        )
        if not object_starts:
            return None
        for verb in rules.write_verbs:
            for start in phrase_starts(tokens, verb):
                end = start + len(verb)
                if _covers(tokens, rules.verb_exceptions, start):
                    continue
                if self._has_topic_prefix(tokens, start):
                    continue
                if verb in rules.question_marker_verbs and any(
                    marker_start >= end
                    for marker in rules.question_markers
                    for marker_start in phrase_starts(tokens, marker)
                ):
                    continue
                if any(end <= obj <= end + rules.max_gap_tokens for obj in object_starts):
                    return "write:" + " ".join(verb)
        return None

    def _has_topic_prefix(self, tokens: Sequence[str], verb_start: int) -> bool:
        """Động từ là chủ đề của phản hồi ("về việc cập nhật giá"), không phải mệnh lệnh."""
        for prefix in self.rules.topic_prefixes:
            for start in phrase_starts(tokens, prefix):
                if verb_start - 2 <= start + len(prefix) <= verb_start:
                    return True
        return False

    def _match_secret(self, tokens: Sequence[str]) -> str | None:
        term = next((p for p in self.rules.secret_terms if phrase_starts(tokens, p)), None)
        if term is None:
            return None
        if any(phrase_starts(tokens, context) for context in self.rules.secret_contexts):
            return "secret:" + " ".join(term)
        return None

    @staticmethod
    def _refuse(reason: GuardReason, layer: GuardLayer, rule_id: str) -> GuardDecision:
        return GuardDecision(
            action=GuardAction.REFUSE,
            reason_code=reason,
            message=REFUSAL_TEMPLATES[reason],
            layer=layer,
            rule_id=rule_id,
        )
