"""Trích từ khoá đặc trưng của một phản hồi cho ``LOOKUP_SIMILAR`` (design b08 D6).

Bigram và unigram không phải stopword, xếp theo TF × IDF. Không dùng LLM.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from .fts_query_builder import FtsQueryBuilder

BIGRAM_BONUS = 1.2
# Từ chỉ có trong chính phản hồi gốc không giúp tìm phản hồi khác.
MIN_DOCUMENT_FREQUENCY = 2
SEARCH_TERMS_FROM_TOP = 3


def extract(content: str, builder: FtsQueryBuilder, *, top: int = 8) -> list[str]:
    """Trả tối đa ``top`` từ khoá (unigram hoặc bigram "a b"), đặc trưng nhất trước."""
    tokens = builder.to_match_tokens(content)
    if not tokens:
        return []
    # Bigram chỉ ghép 2 token liền nhau trong câu gốc (không bắc qua stopword đã bỏ).
    raw = [t for t in content.lower().replace("\n", " ").split()]
    bigrams: list[str] = []
    for left, right in zip(raw, raw[1:], strict=False):
        left_tokens, right_tokens = builder.to_match_tokens(left), builder.to_match_tokens(right)
        if len(left_tokens) == 1 and len(right_tokens) == 1:
            bigrams.append(f"{left_tokens[0]} {right_tokens[0]}")

    scores: dict[str, float] = {}
    order: dict[str, int] = {}
    for term, count in Counter(tokens).items():
        scores[term] = count * builder.specificity(term)
        order.setdefault(term, tokens.index(term))
    for term, count in Counter(bigrams).items():
        left, right = term.split()
        idf = (builder.specificity(left) + builder.specificity(right)) / 2
        scores[term] = count * idf * BIGRAM_BONUS
        order.setdefault(term, tokens.index(left))
    df = getattr(builder.stats, "df", None)
    if isinstance(df, dict) and df:
        from .text_match import normalize_match_text

        def frequent(term: str) -> bool:
            return all(
                df.get(normalize_match_text(part), 0) >= MIN_DOCUMENT_FREQUENCY
                for part in term.split()
            )

        kept = {t: v for t, v in scores.items() if frequent(t)}
        scores = kept or scores
    ranked = sorted(scores, key=lambda t: (-scores[t], order[t], t))
    return ranked[:top]


def search_tokens(terms: Sequence[str], builder: FtsQueryBuilder) -> list[str]:
    """Token để tìm: gộp top 3 từ khoá, giữ thứ tự, tối đa ``builder.max_terms``."""
    tokens: list[str] = []
    for term in terms[:SEARCH_TERMS_FROM_TOP]:
        for token in term.split():
            if token not in tokens:
                tokens.append(token)
    return tokens[: builder.max_terms]
