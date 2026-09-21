"""So khớp văn bản tiếng Việt, dùng chung cho Query Normalizer (b01) và Plan Guard (b03)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from rapidfuzz import fuzz
from unidecode import unidecode

MAX_WINDOW_TOKENS = 4
# Điểm cho cụm người dùng gõ tắt nằm liền trong tên đầy đủ, vd. "vùng 1" ↔ "Truyền thống Vùng 1".
PARTIAL_MATCH_SCORE = 0.9
# Cụm quá ngắn dễ khớp mờ nhầm, nên chỉ chấp nhận khớp chính xác.
MIN_FUZZY_CHARS = 4

_WHITESPACE = re.compile(r"\s+")
_TOKEN = re.compile(r"[a-z0-9]+")


def normalize_match_text(text: str) -> str:
    """Bỏ dấu (kể cả "đ"), viết thường, gộp khoảng trắng."""
    return _WHITESPACE.sub(" ", unidecode(text or "").lower()).strip()


def match_tokens(text: str) -> list[str]:
    """Tách token chữ/số từ dạng đã chuẩn hoá."""
    return _TOKEN.findall(normalize_match_text(text))


def phrase_starts(tokens: Sequence[str], phrase: Sequence[str]) -> list[int]:
    """Vị trí bắt đầu của mọi lần cụm ``phrase`` xuất hiện liền trong ``tokens``."""
    size = len(phrase)
    if size == 0 or size > len(tokens):
        return []
    first = phrase[0]
    return [
        i
        for i in range(len(tokens) - size + 1)
        if tokens[i] == first and list(tokens[i : i + size]) == list(phrase)
    ]


@dataclass(frozen=True)
class TextMatch:
    value: str
    score: float  # 0..1
    mention: str


def _is_contiguous_part(window: Sequence[str], value_tokens: Sequence[str]) -> bool:
    if not phrase_starts(value_tokens, window):
        return False
    is_suffix = list(value_tokens[-len(window) :]) == list(window)
    return is_suffix or any(token.isdigit() for token in window)


def _window_score(window: Sequence[str], value_tokens: Sequence[str]) -> float:
    window_text = " ".join(window)
    value_text = " ".join(value_tokens)
    if window_text == value_text:
        return 1.0
    if len(window_text) < MIN_FUZZY_CHARS or len(value_text) < MIN_FUZZY_CHARS:
        score = 0.0
    else:
        score = fuzz.ratio(window_text, value_text) / 100.0
    if 2 <= len(window) < len(value_tokens) and _is_contiguous_part(window, value_tokens):
        score = max(score, PARTIAL_MATCH_SCORE)
    return score


def find_candidates(
    text: str,
    values: Iterable[str],
    threshold: float,
    limit: int = 3,
) -> list[TextMatch]:
    """Tìm các giá trị trong ``values`` được nhắc tới trong ``text``.

    Quét cửa sổ n-gram 1–4 token của câu (dạng không dấu). Không chốt giá trị: trả tối đa
    ``limit`` ứng viên có điểm ≥ ``threshold``, điểm cao trước.
    """
    tokens = match_tokens(text)
    if not tokens or limit <= 0:
        return []

    best: dict[str, TextMatch] = {}
    for value in values:
        value_tokens = match_tokens(value)
        if not value_tokens:
            continue
        # Mọi độ dài cửa sổ tới n+1 token: cần cả cửa sổ ngắn cho cụm gõ tắt ("vùng 1").
        for size in range(1, min(len(value_tokens) + 1, MAX_WINDOW_TOKENS) + 1):
            for start in range(len(tokens) - size + 1):
                window = tokens[start : start + size]
                score = _window_score(window, value_tokens)
                if score < threshold:
                    continue
                current = best.get(value)
                if current is None or score > current.score:
                    best[value] = TextMatch(value=value, score=score, mention=" ".join(window))

    ranked = sorted(best.values(), key=lambda m: (-m.score, m.value))
    return ranked[:limit]


def mention_score(mention: str, value: str) -> float:
    """Điểm cho một cụm người dùng gõ so với một giá trị thật (Plan Guard, b03 D3).

    Khác ``find_candidates``: ở đây ``mention`` đã là giá trị cần chốt, nên cụm nằm gọn trong
    tên đầy đủ ("vùng" ↔ "Truyền thống Vùng 1") được tính là khớp một phần.
    """
    mention_tokens_ = match_tokens(mention)
    value_tokens = match_tokens(value)
    if not mention_tokens_ or not value_tokens:
        return 0.0
    if mention_tokens_ == value_tokens:
        return 1.0
    if phrase_starts(value_tokens, mention_tokens_):
        return PARTIAL_MATCH_SCORE
    mention_text = " ".join(mention_tokens_)
    value_text = " ".join(value_tokens)
    if len(mention_text) < MIN_FUZZY_CHARS or len(value_text) < MIN_FUZZY_CHARS:
        return 0.0
    return fuzz.ratio(mention_text, value_text) / 100.0


def rank_values(
    mention: str,
    values: Iterable[str],
    threshold: float,
    limit: int = 5,
) -> list[TextMatch]:
    """Xếp hạng giá trị thật theo mức khớp với ``mention``; điểm cao trước."""
    matches = [
        TextMatch(value=value, score=score, mention=mention)
        for value in values
        if (score := mention_score(mention, value)) >= threshold
    ]
    matches.sort(key=lambda m: (-m.score, m.value))
    return matches[:limit]
