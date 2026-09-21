"""Tóm tắt phiên cuộn và guard (spec ``chat-conversation-memory``, b11 task 2.1–2.5)."""

from __future__ import annotations

import logging

import pytest

from dms.chat.ai.fact_sheet import FactSheet
from dms.chat.ai.memory.session_summarizer import (
    CALL_TYPE_MEMORY_SUMMARY,
    PendingTurn,
    SessionSummarizer,
    SummarizerConfig,
)
from dms.chat.ai.memory.store import (
    SUMMARY_METHOD_EXTRACTIVE,
    SUMMARY_METHOD_LLM,
    InMemorySessionMemoryStore,
    MemoryVersionConflict,
    SessionMemory,
)
from dms.chat.ai.memory.summary_guard import (
    SummaryDropReason,
    check_summary,
    extractive_summary,
    source_from_turns,
)
from dms.chat.ai.response_shaper import build_summary, strip_quoted_content
from dms.chat.ws.history_adapter import HistoryAdapter

from .ai_fakes import ScriptedLLM
from .memory_fakes import add_turns, memory_chat_store, summary_json

WINDOW = 5
BATCH = 3


def run_after_turns(total: int, *, llm: ScriptedLLM | None = None):
    """Mô phỏng tác vụ nền sau ``done`` của lượt thứ ``total``."""
    store = memory_chat_store()
    ids = add_turns(store, "s1", total)
    memory = InMemorySessionMemoryStore()
    history = HistoryAdapter(store, memory=memory)
    llm = llm or ScriptedLLM(default=summary_json("Người dùng hỏi Nha Trang nhiều lượt."))
    summarizer = SessionSummarizer(memory, llm=llm, config=SummarizerConfig(batch=BATCH))
    pending = history.pending_turns("s1", window_turns=WINDOW)
    saved = summarizer.summarize("s1", pending, request_id="r1")
    return saved, llm, ids, memory, history


# ── Điều kiện lô ──


def test_not_enough_turns_outside_the_window():
    saved, llm, *_ = run_after_turns(7)
    assert saved is None
    assert llm.calls_for(CALL_TYPE_MEMORY_SUMMARY) == []


def test_full_batch_summarizes_the_oldest_turns_once():
    saved, llm, ids, memory, _ = run_after_turns(8)

    assert len(llm.calls_for(CALL_TYPE_MEMORY_SUMMARY)) == 1
    prompt = llm.calls_for(CALL_TYPE_MEMORY_SUMMARY)[0]
    assert "Câu hỏi số 1" in prompt and "Câu hỏi số 3" in prompt
    assert "Câu hỏi số 4" not in prompt
    assert saved is not None
    assert saved.summarized_until_message_id == ids[2]  # tin nhắn cuối của lượt 3
    assert memory.load("s1").method == SUMMARY_METHOD_LLM


def test_window_after_summary_starts_after_the_summarized_turns():
    _, _, _, _, history = run_after_turns(8)
    window = history.window("s1", max_turns=WINDOW, budget_chars=10_000)
    assert window.summary.startswith("Người dùng hỏi Nha Trang")
    assert [t.question for t in window.turns][0] == "Câu hỏi số 4 về Nha Trang"
    assert len(window.turns) == WINDOW


def test_batch_zero_disables_summarization():
    memory = InMemorySessionMemoryStore()
    summarizer = SessionSummarizer(memory, llm=ScriptedLLM(), config=SummarizerConfig(batch=0))
    pending = [PendingTurn(i, f"q{i}", f"a{i}") for i in range(1, 10)]
    assert summarizer.summarize("s1", pending) is None
    assert memory.load("s1") is None


# ── Guard ──


def test_fabricated_number_falls_back_to_extractive():
    llm = ScriptedLLM(default=summary_json("Nha Trang có 1.500 vấn đề."))
    store = memory_chat_store()
    add_turns(store, "s1", 8, answer="Tổng quan lượt {n}. Tổng số vấn đề 1.234")
    memory = InMemorySessionMemoryStore()
    history = HistoryAdapter(store, memory=memory)
    summarizer = SessionSummarizer(memory, llm=llm, config=SummarizerConfig(batch=BATCH))

    saved = summarizer.summarize("s1", history.pending_turns("s1", window_turns=WINDOW))

    assert saved is not None and saved.method == SUMMARY_METHOD_EXTRACTIVE
    assert "1.500" not in saved.summary
    assert "1.234" in saved.summary


def test_summary_copying_a_quote_is_rejected():
    quote = "đèn led bị chập chờn liên tục sau hai ngày lắp đặt ở nhà khách"
    llm = ScriptedLLM(default=summary_json(f"Khách phàn nàn: {quote[:40]}."))
    store = memory_chat_store()
    add_turns(store, "s1", 8, quotes={2: [{"content": quote, "issue_code": "NT-1"}]})
    memory = InMemorySessionMemoryStore()
    history = HistoryAdapter(store, memory=memory)
    summarizer = SessionSummarizer(memory, llm=llm, config=SummarizerConfig(batch=BATCH))

    saved = summarizer.summarize("s1", history.pending_turns("s1", window_turns=WINDOW))

    assert saved is not None and saved.method == SUMMARY_METHOD_EXTRACTIVE
    assert quote[:40] not in saved.summary


@pytest.mark.parametrize(
    ("summary", "reason"),
    [
        ("", SummaryDropReason.BAD_LENGTH),
        ("x" * 900, SummaryDropReason.BAD_LENGTH),
        ("Có 42 vấn đề mới.", SummaryDropReason.UNGROUNDED_NUMBER),
        ("Đã gọi get_overview cho Nha Trang.", SummaryDropReason.LEAK),
        ("Truy vấn SELECT trên bảng v_issues.", SummaryDropReason.LEAK),
    ],
)
def test_guard_rules(summary, reason):
    source = source_from_turns("", [("Tổng quan Nha Trang", "Tổng số vấn đề 1.234")])
    verdict = check_summary(summary, source, max_chars=800)
    assert not verdict.ok and verdict.reason is reason


def test_guard_accepts_grounded_summary():
    source = source_from_turns("", [("Tổng quan Nha Trang tháng 8", "Tổng số vấn đề 1.234")])
    assert check_summary("Người dùng xem Nha Trang tháng 8: 1.234 vấn đề.", source).ok


def test_extractive_summary_drops_oldest_first_to_fit():
    source = source_from_turns(
        "Tóm tắt cũ rất dài " * 10,
        [("Câu mới nhất", "Trả lời mới nhất")],
    )
    text = extractive_summary(source, max_chars=60)
    assert len(text) <= 60
    assert "Câu mới nhất" in text


# ── Lưu có phiên bản, lỗi chỉ log ──


class _ConflictStore(InMemorySessionMemoryStore):
    def save(self, session_id, memory, *, if_version):
        raise MemoryVersionConflict("lượt khác vừa ghi")


def test_version_conflict_skips_this_round():
    summarizer = SessionSummarizer(
        _ConflictStore(), llm=ScriptedLLM(default=summary_json("tóm tắt")), config=SummarizerConfig(batch=1)
    )
    assert summarizer.summarize("s1", [PendingTurn(1, "q", "a")]) is None


def test_llm_failure_uses_extractive_and_logs(caplog):
    llm = ScriptedLLM([RuntimeError("gemini down")])
    memory = InMemorySessionMemoryStore()
    summarizer = SessionSummarizer(memory, llm=llm, config=SummarizerConfig(batch=1))
    with caplog.at_level(logging.WARNING, logger="dms-chat-memory"):
        saved = summarizer.summarize("s1", [PendingTurn(1, "Tổng quan?", "Tổng số vấn đề 5")])
    assert saved is not None and saved.method == SUMMARY_METHOD_EXTRACTIVE
    assert any(r.message == "memory_summary_failed" for r in caplog.records)


def test_save_failure_only_logs(caplog):
    class Broken(InMemorySessionMemoryStore):
        def save(self, session_id, memory, *, if_version):
            raise OSError("disk full")

    summarizer = SessionSummarizer(Broken(), config=SummarizerConfig(batch=1))
    with caplog.at_level(logging.WARNING, logger="dms-chat-memory"):
        assert summarizer.summarize("s1", [PendingTurn(1, "q", "a")]) is None
    assert any(r.message == "memory_summary_failed" for r in caplog.records)


def test_summary_is_cumulative():
    memory = InMemorySessionMemoryStore()
    memory.save("s1", SessionMemory(summary="Tóm tắt cũ: Nha Trang 1.234."), if_version=0)
    llm = ScriptedLLM(default=summary_json("Nha Trang 1.234, rồi hỏi Biên Hòa 56."))
    summarizer = SessionSummarizer(memory, llm=llm, config=SummarizerConfig(batch=1))
    saved = summarizer.summarize("s1", [PendingTurn(9, "Còn Biên Hòa?", "Tổng số vấn đề 56")])
    assert saved is not None and saved.method == SUMMARY_METHOD_LLM
    assert saved.version == 2
    assert "Tóm tắt cũ" in llm.calls_for(CALL_TYPE_MEMORY_SUMMARY)[0]


# ── summary của lượt không mang trích dẫn (b11 D4) ──


def test_turn_summary_replaces_quote_with_issue_code():
    commentary = "Ví dụ: “đèn chập chờn sau 2 ngày” (NT-0012)."
    summary = build_summary(["Phản hồi tiêu biểu"], FactSheet(), commentary)
    assert "(phản hồi NT-0012)" in summary
    assert "đèn chập chờn sau 2 ngày" not in summary


def test_strip_quoted_content_without_code():
    assert strip_quoted_content('Khách viết "giao chậm quá".') == "Khách viết (một phản hồi)."
