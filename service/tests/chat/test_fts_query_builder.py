"""Spec ``chat-fts-query-building`` — token, kiểm chéo, nới (b08 task 1.2, 1.3, 2.3, 2.4)."""

from __future__ import annotations

import json
import logging
import random

import pytest

from dms.chat.ai.fts_query_builder import (
    ATTEMPT_DROP,
    ATTEMPT_SYNONYM,
    SYNONYMS_PATH,
    CachedTermStats,
    DocumentFrequencies,
    FtsQueryBuilder,
    clamp_limit,
    is_safe_fts_query,
    load_search_synonyms,
    load_stopwords,
)
from dms.chat.ai.text_match import normalize_match_text
from dms.chat.contract import AnswerShape, QueryPattern, QueryPlan, UserScope

from .ai_fakes import SqliteFtsFakeExecutor

ADMIN = UserScope(username="admin", role="admin", display_name="Admin", unit_ids=[])

BUSINESS_PHRASES = [
    "không sáng",
    "hỏng",
    "chậm",
    "chập chờn",
    "nhấp nháy",
    "cháy",
    "nóng",
    "rò nước",
    "vỡ",
    "móp",
    "đổi trả",
    "bảo hành",
    "giao hàng",
    "vận chuyển",
    "lỗi",
    "kêu",
    "đen chân",
    "ố vàng",
    "bong keo",
    "vào nước",
    "giữ nhiệt",
    "mỏi mắt",
    "không nhạy",
    "đứt",
    "sáng yếu",
    "đơ",
    "thất thường",
    "tiết kiệm điện",
    "khó ngủ",
    "thanh toán",
]


@pytest.fixture(scope="module")
def executor() -> SqliteFtsFakeExecutor:
    return SqliteFtsFakeExecutor()


def run(executor: SqliteFtsFakeExecutor, query: str):
    plan = QueryPlan(
        pattern=QueryPattern.FTS5_SEARCH,
        answer_shape=AnswerShape.TABLE,
        original_query="q",
        fts_query=query,
    )
    return executor.execute(plan, ADMIN)


# ── Stopword và đồng nghĩa ──


@pytest.mark.parametrize("phrase", BUSINESS_PHRASES)
def test_business_phrases_are_not_stopwords(phrase):
    builder = FtsQueryBuilder()
    tokens = [t for t in phrase.split() if len(t) > 1]
    assert builder.to_match_tokens(phrase) == tokens


def test_stopwords_have_accented_and_plain_forms():
    words = load_stopwords()
    assert {"phản", "phan", "tìm", "tim"} <= words
    assert "không" not in words and "khong" not in words
    builder = FtsQueryBuilder()
    assert builder.is_stopword("phan") and builder.is_stopword("phản")
    assert not builder.is_stopword("đơ") and not builder.is_stopword("kiệm")


def test_search_synonyms_are_valid_normalized_groups():
    raw = json.loads(SYNONYMS_PATH.read_text(encoding="utf-8"))
    groups = raw["search_synonyms"]
    assert len(groups) >= 5
    for key, values in groups.items():
        assert key == normalize_match_text(key)
        assert values and all(v == normalize_match_text(v) and v != key for v in values)
    assert load_search_synonyms()["chap chon"][0] == "nhap nhay"


# ── Token an toàn ──


def test_special_characters_split_into_tokens():
    assert FtsQueryBuilder().to_match_tokens("TP.HCM") == ["tp", "hcm"]
    assert FtsQueryBuilder().to_match_tokens("2/9") == []  # token 1 ký tự bị bỏ


def test_operator_words_are_lowercased_and_query_runs(executor):
    builder = FtsQueryBuilder()
    spec = builder.build(["đèn OR quạt NOT sáng"], "tìm đèn OR quạt NOT sáng")
    assert spec.fts_query == "đèn or quạt not sáng"
    assert is_safe_fts_query(spec.fts_query)
    result = run(executor, spec.fts_query)
    assert result.status.value in {"ok", "no_data"}
    assert executor.syntax_errors == []


def test_only_stopwords_yield_empty_spec():
    spec = FtsQueryBuilder().build(["các phản hồi"], "tìm các phản hồi")
    assert spec.is_empty and spec.fts_query == ""


def test_max_terms_keeps_most_specific_in_question_order():
    stats = DocumentFrequencies(total_docs=100, df={"den": 90, "led": 50, "chap": 5, "chon": 5})
    builder = FtsQueryBuilder(stats=stats, max_terms=3)
    spec = builder.build(["đèn led chập chờn"], "đèn led chập chờn")
    assert spec.tokens == ("led", "chập", "chờn")


def test_length_fallback_without_stats():
    assert FtsQueryBuilder(max_terms=1).select_tokens(["đèn", "chập"]) == ["chập"]


def test_filters_and_limit_are_sanitized():
    spec = FtsQueryBuilder().build(
        ["chập chờn"],
        "chập chờn",
        filters={"unit_name": "TV1", "province": "Hà Nội", "source": ""},
        limit=500,
    )
    assert spec.filters == {"unit_name": "TV1"}
    assert spec.limit == 50
    assert clamp_limit("x") == 20 and clamp_limit(0) == 1


# ── Từ khoá phải có trong câu hỏi ──


def test_invented_terms_are_rejected_and_logged(caplog):
    with caplog.at_level(logging.INFO, logger="dms-chat-fts"):
        spec = FtsQueryBuilder().build(["chập chờn", "bảo hành"], "tìm phản hồi đèn chập chờn")
    assert spec.tokens == ("chập", "chờn")
    assert spec.rejected_terms == ("bảo hành",)
    assert any(r.getMessage() == "fts_term_not_in_question" for r in caplog.records)


def test_unaccented_question_accepts_accented_term():
    spec = FtsQueryBuilder().build(["đổi trả"], "tim phan hoi doi tra den rang dong")
    assert spec.tokens == ("đổi", "trả")


def test_synonym_of_question_phrase_is_accepted():
    spec = FtsQueryBuilder().build(["nhấp nháy"], "tìm đèn chập chờn")
    assert spec.terms == ("nhấp nháy",)


# ── Nới điều kiện ──


def test_relax_order_synonym_then_drop_least_specific():
    stats = DocumentFrequencies(total_docs=100, df={"den": 80, "chap": 5, "chon": 5, "dem": 90})
    attempts = FtsQueryBuilder(stats=stats).attempts(("đèn", "chập", "chờn", "đêm"), max_relax=2)
    assert [a.kind for a in attempts] == ["exact", ATTEMPT_SYNONYM, ATTEMPT_DROP]
    assert attempts[1].tokens == ("đèn", "nhap", "nhay", "đêm")
    assert attempts[2].dropped_terms == ("đêm",)


def test_relax_keeps_at_least_one_token_and_respects_limit():
    attempts = FtsQueryBuilder(synonyms={}).attempts(("chập", "chờn"), max_relax=5)
    # Đặc trưng ngang nhau (cùng độ dài) → bỏ token đứng sau.
    assert [a.tokens for a in attempts] == [("chập", "chờn"), ("chập",)]
    assert len(FtsQueryBuilder(synonyms={}).attempts(("a1", "b2", "c3", "d4"), max_relax=2)) == 3
    assert FtsQueryBuilder().attempts((), max_relax=2) == []


def test_cached_term_stats_ttl_and_failure_fallback():
    clock = [0.0]
    loads = []

    def loader():
        loads.append(1)
        if len(loads) == 2:
            raise RuntimeError("db locked")
        return DocumentFrequencies(total_docs=10, df={"den": 9})

    stats = CachedTermStats(loader, ttl_seconds=60, now=lambda: clock[0])
    first = stats.idf("đèn")
    assert first is not None and stats.idf("den") == first and len(loads) == 1
    clock[0] = 61
    assert stats.idf("đèn") is None  # lỗi → builder dùng độ dài
    assert stats.idf("đèn") is None and len(loads) == 2  # không thử lại ngay
    clock[0] = 130
    assert stats.idf("đèn") == first and len(loads) == 3


# ── Fuzz: không lỗi cú pháp FTS5 ──


def test_fuzz_500_random_strings_never_break_fts_syntax(executor):
    rng = random.Random(20260917)
    alphabet = (
        "abcdefghijklmnopqrstuvwxyzđăâêôơưáàảãạéèẻẽẹíìỉĩịóòỏõọúùủũụýỳỷỹỵ"
        "ABCDEFĐ0123456789 \t\n.,;:!?\"'`*^()[]{}<>/\\|-_+=~@#$%&"
    )
    specials = [
        "AND",
        "OR",
        "NOT",
        "NEAR",
        "NEAR(",
        '"',
        "*",
        "^",
        "col:",
        "-",
        "TP.HCM",
        "2/9",
        "🙂",
        "\x00",
    ]
    builder = FtsQueryBuilder()
    for _ in range(500):
        parts = []
        for _ in range(rng.randint(1, 8)):
            if rng.random() < 0.3:
                parts.append(rng.choice(specials))
            else:
                parts.append("".join(rng.choice(alphabet) for _ in range(rng.randint(1, 8))))
        text = " ".join(parts)
        spec = builder.build([text], text)
        assert is_safe_fts_query(spec.fts_query), spec.fts_query
        for attempt in builder.attempts(spec.tokens, max_relax=2):
            if attempt.fts_query:
                run(executor, attempt.fts_query)
    assert executor.syntax_errors == []


def test_unseen_tokens_are_dropped_before_common_ones():
    stats = DocumentFrequencies(total_docs=50, df={"phich": 4, "nuoc": 20, "ro": 3})
    builder = FtsQueryBuilder(stats=stats, synonyms={})
    attempts = builder.attempts(("phích", "nước", "rò", "xyzabc"), max_relax=1)
    assert attempts[1].dropped_terms == ("xyzabc",)


def test_unaccented_ambiguous_stopword_forms_are_kept():
    assert FtsQueryBuilder().to_match_tokens("doi tra den rang dong") == [
        "doi",
        "tra",
        "den",
        "rang",
        "dong",
    ]
    assert FtsQueryBuilder().to_match_tokens("đến") == []


def test_term_tokens_present_but_not_contiguous_are_accepted():
    spec = FtsQueryBuilder().build(["ổ cắm cháy"], "ổ cắm điện bị cháy đen")
    assert spec.tokens == ("ổ", "cắm", "cháy") or spec.tokens == ("cắm", "cháy")
