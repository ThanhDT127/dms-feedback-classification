"""Nhận diện "đổi chủ đề" (spec ``chat-conversation-memory``, design b11 D6).

Khớp cụm cố định trên câu gốc đã bỏ dấu, **không** dùng LLM: đổi chủ đề phải xác định được,
và Contextualizer đã có việc riêng là viết lại câu.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..text_match import match_tokens, phrase_starts

DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "reset_phrases.json"
RESET_ASSUMPTION = "Đã bắt đầu chủ đề mới, không dùng bộ lọc của các câu trước."


@dataclass(frozen=True)
class TopicReset:
    matched: bool = False
    phrase: str = ""
    only_reset: bool = False  # câu không còn gì ngoài cụm reset và từ đệm

    def __bool__(self) -> bool:
        return self.matched


@dataclass(frozen=True)
class _Phrases:
    phrases: tuple[tuple[str, ...], ...]
    fillers: frozenset[str]
    filler_pairs: tuple[tuple[str, ...], ...]


@lru_cache(maxsize=1)
def _load(path: str = str(DATA_PATH)) -> _Phrases:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    phrases = sorted(
        (tuple(p.split()) for p in raw.get("phrases", []) if p.strip()),
        key=len,
        reverse=True,  # cụm dài khớp trước: "sang chu de khac" trước "chu de khac"
    )
    fillers = [tuple(f.split()) for f in raw.get("fillers", []) if f.strip()]
    return _Phrases(
        phrases=tuple(phrases),
        fillers=frozenset(f[0] for f in fillers if len(f) == 1),
        filler_pairs=tuple(f for f in fillers if len(f) > 1),
    )


def detect_topic_reset(question: str) -> TopicReset:
    tokens = match_tokens(question)
    if not tokens:
        return TopicReset()
    data = _load()
    for phrase in data.phrases:
        starts = phrase_starts(tokens, phrase)
        if not starts:
            continue
        remaining = list(tokens)
        for start in sorted(starts, reverse=True):
            del remaining[start : start + len(phrase)]
        # Bỏ cả cụm reset khác lặp lại trong câu ("bắt đầu lại, chủ đề mới nhé").
        for other in data.phrases:
            for start in sorted(phrase_starts(remaining, other), reverse=True):
                del remaining[start : start + len(other)]
        for pair in data.filler_pairs:
            for start in sorted(phrase_starts(remaining, pair), reverse=True):
                del remaining[start : start + len(pair)]
        leftover = [token for token in remaining if token not in data.fillers]
        return TopicReset(matched=True, phrase=" ".join(phrase), only_reset=not leftover)
    return TopicReset()


__all__ = ["RESET_ASSUMPTION", "TopicReset", "detect_topic_reset"]
