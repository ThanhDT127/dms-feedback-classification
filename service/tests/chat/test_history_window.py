"""Cửa sổ ngữ cảnh theo ngân sách ký tự (spec ``chat-conversation-memory``, b11 task 3.1, 3.2)."""

from __future__ import annotations

import json

import pytest

from dms.chat.ai.contextualizer import CALL_TYPE, Contextualizer
from dms.chat.ai.memory.history_window import StoredTurn, build_window, pending_for_summary
from dms.chat.ai.memory.store import SessionMemory
from dms.chat.ai.prompt_loader import load_prompt
from dms.chat.ai.types import HistoryTurn, HistoryWindow

from .ai_fakes import ScriptedLLM


def turns_of(count: int, size: int) -> list[StoredTurn]:
    """Mỗi lượt dài đúng ``size`` ký tự (câu hỏi + câu trả lời)."""
    half = size // 2
    return [
        StoredTurn(
            message_id=i * 2,
            question=f"{i:02d}".ljust(half, "q"),
            answer_summary="a" * (size - half),
        )
        for i in range(1, count + 1)
    ]


def test_budget_keeps_only_the_newest_turns_that_fit():
    memory = SessionMemory(summary="s" * 800)
    window = build_window(turns_of(10, 600), memory, max_turns=5, budget_chars=3000)
    assert len(window.turns) == 3
    assert [t.question[:2] for t in window.turns] == ["08", "09", "10"]
    assert window.char_size() <= 3000


def test_turn_limit_applies_before_budget():
    window = build_window(turns_of(10, 10), None, max_turns=5, budget_chars=3000)
    assert [t.question[:2] for t in window.turns] == ["06", "07", "08", "09", "10"]


def test_turns_already_summarized_are_excluded():
    memory = SessionMemory(summary="đã tóm tắt", summarized_until_message_id=6)  # lượt 1–3
    window = build_window(turns_of(6, 10), memory, max_turns=5, budget_chars=3000)
    assert [t.question[:2] for t in window.turns] == ["04", "05", "06"]
    assert window.summary == "đã tóm tắt"


def test_window_without_summary_for_topic_reset():
    memory = SessionMemory(summary="đã tóm tắt")
    window = build_window(turns_of(2, 10), memory, max_turns=5, include_summary=False)
    assert window.summary == ""


@pytest.mark.parametrize(("total", "expected"), [(5, 0), (7, 2), (8, 3), (12, 7)])
def test_pending_turns_are_those_outside_the_window(total, expected):
    assert len(pending_for_summary(turns_of(total, 10), None, window_turns=5)) == expected


# ── Prompt Contextualizer không vượt ngân sách ──


def _fixed_prompt_size() -> int:
    return len(
        load_prompt("contextualize_v1", {"session_summary": "", "history": "", "question": ""}).text
    )


@pytest.mark.parametrize("turn_size", [100, 600, 2000])
def test_contextualizer_prompt_stays_within_budget(turn_size):
    budget = 3000
    question = "còn tháng 7 thì sao"
    memory = SessionMemory(summary="t" * 800)
    window = build_window(turns_of(20, turn_size), memory, max_turns=5, budget_chars=budget)
    llm = ScriptedLLM(default=json.dumps({"standalone_question": "q", "is_follow_up": True}))
    Contextualizer(llm, max_turns=5, answer_max_chars=10_000).rewrite(question, window)

    prompt = llm.calls_for(CALL_TYPE)[0]
    # Phần thêm vào mỗi lượt: nhãn "Lượt i – Người dùng/Trợ lý" và xuống dòng.
    overhead = 80 * max(1, len(window.turns))
    assert len(prompt) <= _fixed_prompt_size() + budget + len(question) + overhead


def test_contextualizer_puts_summary_in_its_own_block():
    llm = ScriptedLLM(default=json.dumps({"standalone_question": "q", "is_follow_up": True}))
    window = HistoryWindow(summary="Đơn vị lúc đầu là Nha Trang.", turns=(HistoryTurn("a", "b"),))
    Contextualizer(llm).rewrite("đơn vị lúc đầu thì sao", window)
    prompt = llm.calls_for(CALL_TYPE)[0]
    block = prompt[prompt.index("<tom_tat_phien>") : prompt.index("</tom_tat_phien>")]
    assert "Nha Trang" in block


def test_summary_alone_is_enough_to_contextualize():
    llm = ScriptedLLM(default=json.dumps({"standalone_question": "q", "is_follow_up": True}))
    Contextualizer(llm).rewrite("còn đơn vị lúc đầu?", HistoryWindow(summary="Nha Trang tháng 8."))
    assert len(llm.calls_for(CALL_TYPE)) == 1


def test_no_summary_and_no_turns_skips_llm():
    llm = ScriptedLLM()
    result = Contextualizer(llm).rewrite("Tổng quan tháng 8", HistoryWindow())
    assert result.rewritten_query == "Tổng quan tháng 8" and llm.calls == []
