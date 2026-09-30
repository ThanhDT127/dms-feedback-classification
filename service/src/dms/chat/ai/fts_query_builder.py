"""Dựng truy vấn FTS5 an toàn từ từ khoá của Planner (design b08 D1–D4).

LLM chỉ chọn cụm từ nội dung; code tách token, bỏ stopword, xếp theo độ đặc trưng và dựng
``fts_query`` (các token nối bằng khoảng trắng — FTS5 hiểu là AND). Chuỗi này đi qua hàm làm
sạch v1.0 của executor mà không bị đổi.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from .text_match import match_tokens, normalize_match_text, phrase_starts

logger = logging.getLogger("dms-chat-fts")

DATA_DIR = Path(__file__).resolve().parent / "data"
STOPWORDS_PATH = DATA_DIR / "stopwords_vi.txt"
SYNONYMS_PATH = DATA_DIR / "synonyms_vi.json"

DEFAULT_MAX_TERMS = 6
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
FTS_FILTER_KEYS = frozenset({"date_from", "date_to", "unit_name", "sentiment", "source", "label"})
# Tách tại mọi ký tự không phải chữ hoặc số (Unicode); "_" cũng là dấu tách.
_SPLIT = re.compile(r"[\W_]+", re.UNICODE)
_SYNTAX = re.compile(r'["*^:()]')
# Dạng bỏ dấu của stopword trùng với từ nội dung phổ biến ("đến"/"đèn", "đó"/"đỏ", "từ"/"tủ"...):
# với token gõ không dấu thì không coi là stopword.
AMBIGUOUS_PLAIN_STOPWORDS = frozenset(
    {
        "den",
        "do",
        "tu",
        "nam",
        "sau",
        "gio",
        "lan",
        "ngay",
        "thang",
        "ban",
        "vi",
        "ra",
        "hon",
        "qua",
        "khi",
        "nay",
        "co",
        "la",
    }
)

ATTEMPT_EXACT = "exact"
ATTEMPT_SYNONYM = "synonym"
ATTEMPT_DROP = "drop"


class TermStats(Protocol):
    def idf(self, token: str) -> float | None:
        """IDF xấp xỉ của token (so ở dạng không dấu); ``None`` khi không có bảng."""
        ...


@dataclass(frozen=True)
class DocumentFrequencies:
    """Bảng tần suất tài liệu dựng từ nội dung phản hồi (b08 D4)."""

    total_docs: int
    df: Mapping[str, int] = field(default_factory=dict)

    @classmethod
    def from_texts(cls, texts: Iterable[str]) -> DocumentFrequencies:
        counts: dict[str, int] = {}
        total = 0
        for text in texts:
            total += 1
            for token in set(match_tokens(text or "")):
                counts[token] = counts.get(token, 0) + 1
        return cls(total_docs=total, df=counts)

    def idf(self, token: str) -> float | None:
        if self.total_docs <= 0:
            return None
        key = normalize_match_text(token)
        return math.log((self.total_docs + 1) / (self.df.get(key, 0) + 1)) + 1.0


class CachedTermStats:
    """Cache bảng IDF theo TTL; lỗi khi nạp thì trả ``None`` để builder dùng độ dài token."""

    def __init__(
        self,
        loader: Callable[[], TermStats],
        *,
        ttl_seconds: float = 300.0,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._loader = loader
        self._ttl = ttl_seconds
        self._now = now
        self._cached: TermStats | None = None
        self._loaded_at = 0.0
        self._failed_at: float | None = None

    def idf(self, token: str) -> float | None:
        current = self._now()
        if self._cached is None or current - self._loaded_at >= self._ttl:
            if self._failed_at is not None and current - self._failed_at < self._ttl:
                return None
            try:
                self._cached = self._loader()
                self._loaded_at = current
                self._failed_at = None
            except Exception as exc:
                logger.warning(
                    "fts_term_stats_unavailable", extra={"error_type": type(exc).__name__}
                )
                self._failed_at = current
                return None
        return self._cached.idf(token)


@lru_cache(maxsize=1)
def load_stopwords(path: Path = STOPWORDS_PATH) -> frozenset[str]:
    """Dạng có dấu và dạng không dấu nằm chung; ``is_stopword`` chọn cách so theo token."""
    words: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        word = line.strip().lower()
        if not word or word.startswith("#"):
            continue
        words.add(word)
        words.add(normalize_match_text(word))
    return frozenset(words)


@lru_cache(maxsize=1)
def load_search_synonyms(path: Path = SYNONYMS_PATH) -> dict[str, tuple[str, ...]]:
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return {
        normalize_match_text(key): tuple(normalize_match_text(v) for v in values)
        for key, values in raw.get("search_synonyms", {}).items()
    }


@dataclass(frozen=True)
class FtsAttempt:
    tokens: tuple[str, ...]
    kind: str = ATTEMPT_EXACT
    dropped_terms: tuple[str, ...] = ()

    @property
    def fts_query(self) -> str:
        return " ".join(self.tokens)


@dataclass(frozen=True)
class FtsSearchSpec:
    terms: tuple[str, ...]
    tokens: tuple[str, ...]
    rejected_terms: tuple[str, ...] = ()
    filters: Mapping[str, Any] = field(default_factory=dict)
    limit: int = DEFAULT_LIMIT

    @property
    def fts_query(self) -> str:
        return " ".join(self.tokens)

    @property
    def is_empty(self) -> bool:
        return not self.tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "terms": list(self.terms),
            "tokens": list(self.tokens),
            "rejected_terms": list(self.rejected_terms),
            "filters": dict(self.filters),
            "limit": self.limit,
        }


class FtsQueryBuilder:
    def __init__(
        self,
        *,
        stopwords: frozenset[str] | None = None,
        synonyms: Mapping[str, Sequence[str]] | None = None,
        stats: TermStats | None = None,
        max_terms: int = DEFAULT_MAX_TERMS,
        default_limit: int = DEFAULT_LIMIT,
    ) -> None:
        self.stopwords = stopwords if stopwords is not None else load_stopwords()
        self.synonyms = {
            normalize_match_text(k): tuple(v)
            for k, v in (synonyms if synonyms is not None else load_search_synonyms()).items()
        }
        self.stats = stats
        self.max_terms = max_terms
        self.default_limit = default_limit

    # ── Token ──

    def to_match_tokens(self, term: str) -> list[str]:
        """D2: viết thường, tách tại ký tự không phải chữ/số, bỏ token 1 ký tự và stopword."""
        tokens: list[str] = []
        for token in _SPLIT.split((term or "").lower()):
            if len(token) <= 1:
                continue
            if self.is_stopword(token):
                continue
            tokens.append(token)
        return tokens

    def is_stopword(self, token: str) -> bool:
        """Token có dấu so với dạng có dấu (tránh "đơ" trùng "đó"); token không dấu so dạng bỏ dấu."""
        plain = normalize_match_text(token)
        if token != plain:
            return token in self.stopwords
        return plain in self.stopwords and plain not in AMBIGUOUS_PLAIN_STOPWORDS

    def is_unseen(self, token: str) -> bool:
        df = getattr(self.stats, "df", None)
        if not isinstance(df, Mapping) or not df:
            return False
        return df.get(normalize_match_text(token), 0) == 0

    def specificity(self, token: str) -> float:
        if self.stats is not None:
            value = self.stats.idf(token)
            if value is not None:
                return float(value)
        return float(len(token))

    # ── Kiểm chéo từ khoá với câu hỏi (D1) ──

    def check_terms(self, terms: Sequence[str], question: str) -> tuple[list[str], list[str]]:
        question_tokens = match_tokens(question)
        kept: list[str] = []
        rejected: list[str] = []
        for term in terms:
            phrase = match_tokens(str(term))
            if not phrase:
                continue
            # Cụm liền trong câu, hoặc mọi token của cụm đều có trong câu (LLM hay bỏ từ đệm
            # như "bị"), hoặc là đồng nghĩa của một cụm trong câu. Từ bịa vẫn bị loại.
            if (
                phrase_starts(question_tokens, phrase)
                or set(phrase) <= set(question_tokens)
                or self._synonym_in_question(" ".join(phrase), question_tokens)
            ):
                kept.append(str(term))
            else:
                rejected.append(str(term))
                logger.info("fts_term_not_in_question", extra={"term": str(term)[:80]})
        return kept, rejected

    def _synonym_in_question(self, phrase: str, question_tokens: Sequence[str]) -> bool:
        for key, values in self.synonyms.items():
            group = (key, *values)
            if phrase in group and any(
                phrase_starts(question_tokens, tuple(other.split())) for other in group
            ):
                return True
        return False

    # ── Dựng spec ──

    def build(
        self,
        terms: Sequence[str],
        question: str,
        *,
        filters: Mapping[str, Any] | None = None,
        limit: Any = None,
    ) -> FtsSearchSpec:
        kept, rejected = self.check_terms(terms, question)
        ordered: list[str] = []
        for term in kept:
            for token in self.to_match_tokens(term):
                if token not in ordered:
                    ordered.append(token)
        tokens = self.select_tokens(ordered)
        return FtsSearchSpec(
            terms=tuple(kept),
            tokens=tuple(tokens),
            rejected_terms=tuple(rejected),
            filters={k: v for k, v in (filters or {}).items() if k in FTS_FILTER_KEYS and v},
            limit=clamp_limit(limit, self.default_limit),
        )

    def select_tokens(self, tokens: Sequence[str], limit: int | None = None) -> list[str]:
        """Giữ tối đa ``max_terms`` token đặc trưng nhất, theo thứ tự xuất hiện."""
        cap = limit or self.max_terms
        if len(tokens) <= cap:
            return list(tokens)
        ranked = sorted(range(len(tokens)), key=lambda i: (-self.specificity(tokens[i]), i))
        keep = sorted(ranked[:cap])
        return [tokens[i] for i in keep]

    # ── Nới điều kiện (D4) ──

    def attempts(self, tokens: Sequence[str], *, max_relax: int) -> list[FtsAttempt]:
        base = tuple(tokens)
        if not base:
            return []
        result = [FtsAttempt(base)]
        synonym = self._synonym_tokens(base)
        if synonym is not None and len(result) <= max_relax:
            result.append(FtsAttempt(synonym, kind=ATTEMPT_SYNONYM))
        current = list(base)
        dropped: list[str] = []
        while len(result) <= max_relax and len(current) > 1:
            # Token không có trong dữ liệu không thể khớp: bỏ trước; sau đó bỏ token ít đặc trưng nhất.
            weakest = min(
                range(len(current)),
                key=lambda i: (not self.is_unseen(current[i]), self.specificity(current[i]), -i),
            )
            dropped.append(current.pop(weakest))
            result.append(
                FtsAttempt(tuple(current), kind=ATTEMPT_DROP, dropped_terms=tuple(dropped))
            )
        return result

    def _synonym_tokens(self, tokens: Sequence[str]) -> tuple[str, ...] | None:
        normalized = [normalize_match_text(t) for t in tokens]
        for key, values in sorted(self.synonyms.items(), key=lambda kv: -len(kv[0].split())):
            phrase = key.split()
            for start in phrase_starts(normalized, phrase):
                replacement = values[0].split() if values else []
                if not replacement:
                    continue
                new = [*tokens[:start], *replacement, *tokens[start + len(phrase) :]]
                return tuple(dict.fromkeys(new))
        return None


def clamp_limit(value: Any, default: int = DEFAULT_LIMIT) -> int:
    try:
        number = int(value) if value is not None else default
    except (TypeError, ValueError):
        number = default
    return min(max(number, 1), MAX_LIMIT)


def is_safe_fts_query(query: str) -> bool:
    """Không có toán tử viết hoa hay ký tự cú pháp FTS5."""
    if _SYNTAX.search(query):
        return False
    return not any(word in {"AND", "OR", "NOT", "NEAR"} for word in query.split())
