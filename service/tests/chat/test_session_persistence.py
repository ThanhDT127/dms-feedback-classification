"""Spec ``chat-turn-persistence`` (b06 task 3.4) trên ``ChatStore`` thật của Dev A."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, date, datetime, timedelta

import pytest

from dms.chat.ai.answer_events import CommentaryStatus, DoneStatus
from dms.chat.ai.intents import Intent
from dms.chat.ai.response_shaper import ComposeResult
from dms.chat.ai.types import (
    DateRange,
    Decision,
    DroppedEntity,
    EntityCandidate,
    SessionSlots,
    StepResult,
    StepState,
    StepStatus,
    TurnOutcome,
    TurnRequest,
)
from dms.chat.contract import MessageRole, UserScope
from dms.chat.db.chat_store import ChatStore
from dms.chat.db.migrations import apply_chat_migrations
from dms.chat.ws.audit import AUDIT_EVENT, audit_fields, log_turn_audit
from dms.chat.ws.history_adapter import HistoryAdapter
from dms.chat.ws.session_service import (
    ChatSessionService,
    SessionNotFound,
    SessionServiceConfig,
    fit_metadata,
    json_size,
    make_title,
)
from dms.chat.ws.turn_runner import TurnRecord

QUESTION = "Tổng quan phản hồi của Nha Trang tháng 8 năm nay thế nào, có gì đáng chú ý không?"
SCOPE = UserScope(username="alice", role="user", display_name="Alice", unit_ids=["Nha Trang"])


@pytest.fixture
def store() -> ChatStore:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    apply_chat_migrations(conn)
    return ChatStore(lambda: conn)


@pytest.fixture
def service(store) -> ChatSessionService:
    return ChatSessionService(store)


NHA_TRANG_SLOTS = SessionSlots(
    date_range=DateRange(date(2026, 8, 1), date(2026, 8, 31), "tháng 8"),
    entities={"unit": (EntityCandidate("unit", "Nha Trang", "Nha Trang", 1.0),)},
)


def record_for(
    session_id: str,
    *,
    answer_id: str = "a1",
    summary: str = "Tổng quan: 120 phản hồi.",
    events=None,
) -> TurnRecord:
    outcome = TurnOutcome(
        request_id="req-1",
        original_query=QUESTION,
        rewritten_query=QUESTION,
        decision=Decision.RUN,
        intent=Intent.OVERVIEW,
        step_results=(
            StepResult(index=1, function_name="get_overview", status=StepStatus(StepState.OK)),
        ),
        scope_units=("Nha Trang",),
        dropped_entities=(DroppedEntity("unit", "Biên Hòa"),),
        slots=NHA_TRANG_SLOTS,
        timings_ms={"total": 900},
        llm_usage={"chat_plan": {"calls": 1, "total_tokens": 800}},
    )
    return TurnRecord(
        answer_id=answer_id,
        username="alice",
        session_id=session_id,
        client_msg_id="c1",
        turn=TurnRequest(question=QUESTION, scope=SCOPE, session_id=session_id, request_id="req-1"),
        outcome=outcome,
        compose=ComposeResult(DoneStatus.OK, summary, CommentaryStatus.FULL, 2, 6),
        done_status=DoneStatus.OK,
        summary=summary,
        events=events
        if events is not None
        else [
            {"seq": 1, "type": "status", "data": {"stage": "understanding"}},
            {"seq": 2, "type": "data_block", "data": {"kind": "kpi", "payload": {"items": []}}},
            {"seq": 3, "type": "commentary", "data": {"text": "Nhận định."}},
            {"seq": 4, "type": "suggestions", "data": {"items": ["Tháng 7 thì sao?"]}},
            {"seq": 5, "type": "done", "data": {"status": "ok"}},
        ],
        queue_ms=3,
        total_ms=950,
    )


# ── Phiên chỉ thuộc về chủ sở hữu ──


def test_new_ask_creates_session_titled_with_first_60_chars(service):
    session = service.open_for_ask("alice", None, QUESTION)

    assert session.user_id == "alice"
    assert len(session.title) <= 60
    assert session.title.startswith("Tổng quan phản hồi của Nha Trang")
    assert service.get_owned("alice", session.session_id).session_id == session.session_id


def test_short_question_title_is_kept_whole():
    assert make_title("  Tổng quan   tháng 8 ") == "Tổng quan tháng 8"


@pytest.mark.parametrize("username", ["bob", "admin"])
def test_other_users_including_admin_cannot_use_session(service, store, username):
    session = service.open_for_ask("alice", None, QUESTION)

    with pytest.raises(SessionNotFound):
        service.open_for_ask(username, session.session_id, "còn tháng 7 thì sao")
    with pytest.raises(SessionNotFound):
        service.messages(username, session.session_id)
    with pytest.raises(SessionNotFound):
        service.rename(username, session.session_id, "Đổi tên")
    with pytest.raises(SessionNotFound):
        service.delete(username, session.session_id)
    assert store.get_messages(session.session_id) == []
    assert store.get_session(session.session_id) is not None


def test_expired_and_missing_sessions_are_not_found(store):
    later = datetime.now(UTC) + timedelta(days=8)
    service = ChatSessionService(store, now=lambda: later)
    session = service.open_for_ask("alice", None, QUESTION)

    with pytest.raises(SessionNotFound):
        service.get_owned("alice", session.session_id)
    with pytest.raises(SessionNotFound):
        service.get_owned("alice", "missing")
    assert service.list_sessions("alice") == []


# ── Lưu một lượt ──


def test_completed_turn_stores_user_then_assistant_without_status_events(service, store):
    session = service.open_for_ask("alice", None, QUESTION)
    service.persist_user_message(session.session_id, QUESTION, client_msg_id="c1", answer_id="a1")
    service.persist_turn(record_for(session.session_id))

    messages = service.messages("alice", session.session_id)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == QUESTION
    assert messages[0]["metadata"] == {"client_msg_id": "c1", "answer_id": "a1"}

    meta = messages[1]["metadata"]
    assert messages[1]["content"] == "Tổng quan: 120 phản hồi."
    assert {
        k: meta[k] for k in ("answer_id", "request_id", "intent", "decision", "done_status")
    } == {
        "answer_id": "a1",
        "request_id": "req-1",
        "intent": "OVERVIEW",
        "decision": "run",
        "done_status": "ok",
    }
    assert [e["type"] for e in meta["events"]] == ["data_block", "commentary", "suggestions"]
    assert meta["slots"] == NHA_TRANG_SLOTS.to_dict()
    assert "truncated_for_storage" not in meta


def test_slots_are_saved_for_the_follow_up_turn(service):
    session = service.open_for_ask("alice", None, QUESTION)
    service.persist_turn(record_for(session.session_id))

    follow_up = service.open_for_ask("alice", session.session_id, "còn tháng 7 thì sao")
    slots = service.previous_slots(follow_up)

    assert slots.entities["unit"][0].value == "Nha Trang"
    assert slots.date_range == NHA_TRANG_SLOTS.date_range


def test_broken_slots_json_falls_back_to_empty(service, store):
    session = service.open_for_ask("alice", None, QUESTION)
    store.update_session(session.session_id, slots_json='{"date_range": {"x": 1}}')
    assert service.previous_slots(service.get_owned("alice", session.session_id)).is_empty()


def test_persist_failure_is_logged_not_raised(service, store, monkeypatch, caplog):
    session = service.open_for_ask("alice", None, QUESTION)

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "add_message", broken)
    with caplog.at_level(logging.ERROR, logger="dms-chat"):
        service.persist_turn(record_for(session.session_id))

    assert any(r.getMessage() == "chat_persist_failed" for r in caplog.records)


def test_oversized_metadata_truncates_table_rows(store):
    service = ChatSessionService(store, config=SessionServiceConfig(metadata_max_bytes=8000))
    rows = [{"label": f"Sản phẩm {i}", "value": i, "note": "x" * 40} for i in range(400)]
    events = [
        {"seq": 1, "type": "data_block", "data": {"kind": "table", "payload": {"rows": rows}}},
        {"seq": 2, "type": "commentary", "data": {"text": "Nhận định."}},
    ]
    session = service.open_for_ask("alice", None, QUESTION)
    service.persist_turn(record_for(session.session_id, events=events))

    meta = service.messages("alice", session.session_id)[0]["metadata"]
    assert meta["truncated_for_storage"] is True
    kept = meta["events"][0]["data"]["payload"]
    assert 0 < len(kept["rows"]) < 400 and kept["truncated"] is True
    assert json_size(meta) <= 8000
    assert meta["events"][1]["data"]["text"] == "Nhận định."


def test_metadata_that_cannot_shrink_drops_events():
    huge = {"events": [{"seq": 1, "type": "commentary", "data": {"text": "x" * 20000}}]}
    fitted = fit_metadata(huge, 4096)
    assert fitted["events"] == [] and fitted["truncated_for_storage"] is True


# ── REST-level service operations ──


def test_rename_validates_title(service):
    session = service.open_for_ask("alice", None, QUESTION)
    for bad in ["", "   ", "x" * 121]:
        with pytest.raises(ValueError):
            service.rename("alice", session.session_id, bad)
    assert service.get_owned("alice", session.session_id).title == session.title
    assert (
        service.rename("alice", session.session_id, " Báo cáo  Nha Trang ").title
        == "Báo cáo Nha Trang"
    )


def test_delete_removes_session_and_messages(service, store):
    session = service.open_for_ask("alice", None, QUESTION)
    service.persist_user_message(session.session_id, QUESTION, client_msg_id="c1", answer_id="a1")
    service.delete("alice", session.session_id)

    with pytest.raises(SessionNotFound):
        service.messages("alice", session.session_id)
    assert store.get_messages(session.session_id) == []


def test_list_sessions_only_returns_own(service):
    mine = service.open_for_ask("alice", None, "Câu của Alice")
    service.open_for_ask("bob", None, "Câu của Bob")
    assert [s.session_id for s in service.list_sessions("alice")] == [mine.session_id]


# ── Lịch sử cho Contextualizer ──


def test_last_turns_returns_most_recent_pairs_in_order(service, store):
    session = service.open_for_ask("alice", None, "Lượt 1")
    for i in range(1, 9):
        answer_id = f"a{i}"
        service.persist_user_message(
            session.session_id, f"Hỏi {i}", client_msg_id=f"c{i}", answer_id=answer_id
        )
        service.persist_turn(
            record_for(session.session_id, answer_id=answer_id, summary=f"Đáp {i}")
        )

    turns = HistoryAdapter(store).last_turns(session.session_id, 5)

    assert [t.question for t in turns] == [f"Hỏi {i}" for i in range(4, 9)]
    assert [t.answer_summary for t in turns] == [f"Đáp {i}" for i in range(4, 9)]


def test_unanswered_question_is_not_paired(service, store):
    session = service.open_for_ask("alice", None, "x")
    service.persist_user_message(session.session_id, "Hỏi 1", client_msg_id="c1", answer_id="a1")
    service.persist_turn(record_for(session.session_id, answer_id="a1", summary="Đáp 1"))
    # Lượt 2 lỗi khi lưu assistant: chỉ có tin nhắn user.
    service.persist_user_message(session.session_id, "Hỏi 2", client_msg_id="c2", answer_id="a2")
    service.persist_user_message(session.session_id, "Hỏi 3", client_msg_id="c3", answer_id="a3")
    service.persist_turn(record_for(session.session_id, answer_id="a3", summary="Đáp 3"))

    turns = HistoryAdapter(store).last_turns(session.session_id, 5)
    assert [(t.question, t.answer_summary) for t in turns] == [
        ("Hỏi 1", "Đáp 1"),
        ("Hỏi 3", "Đáp 3"),
    ]
    assert HistoryAdapter(store).last_turns(session.session_id, 0) == []


# ── Audit ──


def test_audit_record_has_ids_but_no_question_or_data(caplog):
    record = record_for("s1")
    with caplog.at_level(logging.INFO, logger="dms-chat-audit"):
        log_turn_audit(record)

    audits = [r for r in caplog.records if r.getMessage() == AUDIT_EVENT]
    assert len(audits) == 1
    fields = audits[0].audit
    assert fields["request_id"] == "req-1"
    assert fields["functions"] == ["get_overview"]
    assert fields["scope_units"] == ["Nha Trang"]
    assert fields["dropped_units"] == ["Biên Hòa"]
    assert fields["queue_ms"] == 3 and fields["dropped_sentences"] == 2
    assert fields["llm_usage"]["chat_plan"]["total_tokens"] == 800
    serialized = json.dumps(fields, ensure_ascii=False)
    for secret in (QUESTION, "Nha Trang tháng 8", "Nhận định", "120 phản hồi", "Tháng 7 thì sao"):
        assert secret not in serialized


def test_audit_fields_without_outcome():
    record = record_for("s1")
    record.outcome = None
    record.compose = None
    record.error = "RuntimeError: sqlite path /secret"
    fields = audit_fields(record)
    assert fields["decision"] is None and fields["functions"] == []
    assert fields["error_type"] == "RuntimeError"
    assert "/secret" not in json.dumps(fields)


def test_message_role_enum_round_trip(store):
    # ChatStore của Dev A lưu role dạng chuỗi; adapter đọc lại qua MessageRole.
    session = ChatSessionService(store).open_for_ask("alice", None, "x")
    store.add_message(session.session_id, MessageRole.SYSTEM, "hệ thống")
    assert HistoryAdapter(store).last_turns(session.session_id, 5) == []


def test_last_quotes_come_from_latest_answer_with_quote_block(service, store):
    session = service.open_for_ask("alice", None, "x")
    quote_events = [
        {
            "seq": 1,
            "type": "data_block",
            "data": {
                "kind": "quote",
                "payload": {
                    "quotes": [
                        {
                            "feedback_id": 101,
                            "issue_code": "TV1-0101",
                            "content": "Đèn chập chờn",
                            "unit_name": "TV1",
                        },
                        {
                            "feedback_id": 102,
                            "issue_code": "TV1-0102",
                            "content": "Đèn nhấp nháy",
                            "unit_name": "TV1",
                        },
                    ]
                },
            },
        },
    ]
    service.persist_turn(record_for(session.session_id, answer_id="a1", events=quote_events))
    service.persist_turn(record_for(session.session_id, answer_id="a2"))  # lượt sau không có quote

    quotes = HistoryAdapter(store).last_quotes(session.session_id)
    assert [q["feedback_id"] for q in quotes] == [101, 102]
    assert HistoryAdapter(store).last_quotes("missing") == []
