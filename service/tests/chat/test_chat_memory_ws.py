"""b11 qua WebSocket/REST thật: hạn mức, cảnh báo, hạn phiên, tóm tắt nền, API usage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from dms.chat.ai.budget import request_context
from dms.chat.ai.memory.session_summarizer import CALL_TYPE_MEMORY_SUMMARY
from dms.chat.ai.memory.store import SUMMARY_METHOD_EXTRACTIVE
from dms.chat.ai.types import LLMResult

from . import test_chat_ws as ws_module
from .test_chat_ws import PLAN_JSON, Env, GatedLLM, ask, receive_until_done, wait_for


class RecordingLLM(GatedLLM):
    """Ghi lại ``ChatRequestContext`` đang có ở mỗi lần gọi, như gateway thật đọc nó."""

    def __init__(self) -> None:
        super().__init__()
        self.seen: list[tuple[str, request_context.ChatRequestContext | None]] = []

    def generate_json(self, prompt, *, call_type, system_instruction=None):
        self.seen.append((call_type, request_context.current()))
        return LLMResult(text=PLAN_JSON, usage={"total_tokens": 10})

    def stream(self, prompt, *, call_type, **kwargs):
        self.seen.append((call_type, request_context.current()))
        return super().stream(prompt, call_type=call_type, **kwargs)


@pytest.fixture
def make_env(tmp_path, monkeypatch):
    envs: list[Env] = []

    def factory(**overrides) -> Env:
        env = Env(tmp_path / f"e{len(envs)}", monkeypatch, **overrides)
        envs.append(env)
        return env

    yield factory
    for env in envs:
        env.llm.gate.set()
        env.services.shutdown()


def spend(env: Env, username: str, tokens: int) -> None:
    env.services.ledger.record(
        username=username,
        request_id="earlier",
        session_id=None,
        call_type="chat_plan",
        model="m",
        prompt_tokens=tokens,
        completion_tokens=0,
        total_tokens=tokens,
        cost_usd=0.01,
        success=True,
        at=datetime.now(UTC),
    )


# ── Hạn mức (4.4) ──


def test_ask_over_budget_is_rejected_with_reset_time(make_env):
    env = make_env(chat_user_daily_token_budget=1000)
    spend(env, "alice", 1200)
    with env.ws() as ws:
        ws.send_json({"type": "ask", "client_msg_id": "m1", "question": "Tổng quan tháng 8"})
        reply = ws.receive_json()
    assert reply["type"] == "error" and reply["code"] == "BUDGET_EXCEEDED"
    assert reply["client_msg_id"] == "m1"
    assert reply["data"]["reset_at"].endswith("T00:00:00+07:00")
    assert env.buffer.active_count("alice") == 0
    assert env.store.list_sessions("alice") == []  # không tạo phiên, không tạo answer


def test_admin_over_budget_is_exempt(make_env):
    env = make_env(chat_user_daily_token_budget=1000)
    spend(env, "admin", 5000)
    with env.ws("admin") as ws:
        reply = ask(ws)
        events = receive_until_done(ws, reply["answer_id"])
    assert events[-1]["data"]["status"] in ("ok", "partial", "no_data")


def test_done_has_budget_warning_after_80_percent(make_env):
    env = make_env(chat_user_daily_token_budget=1000)
    spend(env, "alice", 850)
    with env.ws() as ws:
        reply = ask(ws)
        done = receive_until_done(ws, reply["answer_id"])[-1]
    warning = done["data"]["budget_warning"]
    assert warning["used_ratio"] == pytest.approx(0.85)
    assert warning["reset_at"].endswith("+07:00")


def test_no_budget_warning_below_ratio(make_env):
    env = make_env(chat_user_daily_token_budget=1000)
    spend(env, "alice", 100)
    with env.ws() as ws:
        reply = ask(ws)
        done = receive_until_done(ws, reply["answer_id"])[-1]
    assert "budget_warning" not in done["data"]


def test_budget_zero_disables_the_check(make_env):
    env = make_env(chat_user_daily_token_budget=0)
    assert env.services.budget is None
    with env.ws() as ws:
        assert ask(ws)["type"] == "ack"


# ── Context của lượt và tóm tắt nền (2.3, 4.1) ──


def test_every_llm_call_of_a_turn_carries_the_user_context(tmp_path, monkeypatch):
    # Env dựng GatedLLM trong __init__; thay lớp ở module để mọi thành phần dùng chung một LLM.
    monkeypatch.setattr(ws_module, "GatedLLM", RecordingLLM)
    env = Env(tmp_path, monkeypatch, chat_memory_compact_batch=1, chat_history_turns=1)
    llm = env.llm
    try:
        with env.ws() as ws:
            first = ask(ws, client_msg_id="m1")
            receive_until_done(ws, first["answer_id"])
            second = ask(ws, client_msg_id="m2", session_id=first["session_id"])
            receive_until_done(ws, second["answer_id"])
        session_id = first["session_id"]
        # Tác vụ nền chạy sau done, không chặn câu trả lời.
        wait_for(lambda: env.services.memory.load(session_id) is not None)
    finally:
        env.services.shutdown()

    summary_calls = [ctx for call_type, ctx in llm.seen if call_type == CALL_TYPE_MEMORY_SUMMARY]
    assert summary_calls and all(
        ctx is not None and ctx.username == "alice" for ctx in summary_calls
    )
    turn_calls = [ctx for call_type, ctx in llm.seen if call_type != CALL_TYPE_MEMORY_SUMMARY]
    assert turn_calls and all(ctx is not None and ctx.username == "alice" for ctx in turn_calls)
    memory = env.services.memory.load(session_id)
    # PLAN_JSON không có "summary" nên guard loại và dùng bản trích xuất.
    assert memory.method == SUMMARY_METHOD_EXTRACTIVE
    assert memory.summarized_until_message_id is not None


def test_background_summary_disabled_when_batch_is_zero(make_env):
    env = make_env(chat_memory_compact_batch=0, chat_history_turns=1)
    with env.ws() as ws:
        first = ask(ws, client_msg_id="m1")
        receive_until_done(ws, first["answer_id"])
        second = ask(ws, client_msg_id="m2", session_id=first["session_id"])
        receive_until_done(ws, second["answer_id"])
    wait_for(lambda: env.services.runner.inflight == 0)
    assert env.services.memory.load(first["session_id"]) is None


# ── Hạn phiên trượt (5.1) ──


def test_completed_turn_extends_session_expiry(make_env):
    env = make_env(chat_session_ttl_days=7)
    with env.ws() as ws:
        reply = ask(ws)
        receive_until_done(ws, reply["answer_id"])
    session_id = reply["session_id"]
    env.store.update_session(
        session_id, expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat()
    )
    with env.ws() as ws:
        second = ask(ws, client_msg_id="m2", session_id=session_id)
        receive_until_done(ws, second["answer_id"])

    def extended() -> bool:
        expires = datetime.fromisoformat(env.store.get_session(session_id).expires_at)
        return expires - datetime.now(UTC) > timedelta(days=6, hours=23)

    wait_for(extended)


# ── Dọn phiên hết hạn (5.2) ──


def test_cleanup_removes_expired_sessions_and_messages(make_env):
    from dms.web.app import _clear_expired_chat_sessions

    env = make_env()
    with env.ws() as ws:
        reply = ask(ws)
        receive_until_done(ws, reply["answer_id"])
    session_id = reply["session_id"]
    wait_for(lambda: len(env.store.get_messages(session_id)) == 2)
    env.store.update_session(
        session_id, expires_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    )

    assert _clear_expired_chat_sessions() == 1
    assert env.store.get_session(session_id) is None
    assert env.store.get_messages(session_id) == []


def test_cleanup_loop_survives_errors(monkeypatch):
    import asyncio

    from dms.web import app as app_module

    calls = {"n": 0}

    def boom() -> int:
        calls["n"] += 1
        raise RuntimeError("db locked")

    monkeypatch.setattr(app_module, "_clear_expired_chat_sessions", boom)

    async def scenario() -> None:
        task = asyncio.create_task(app_module._chat_session_cleanup_loop(0.01))
        await asyncio.sleep(0.08)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert calls["n"] >= 2  # lỗi lần đầu không dừng vòng dọn


# ── API usage (4.6) ──


def test_usage_api_for_admin(make_env):
    env = make_env()
    spend(env, "alice", 300)
    today = datetime.now(UTC).date().isoformat()
    response = env.client.get(
        f"/api/chat/usage?date_from={today}&date_to={today}", headers=env.headers("admin")
    )
    assert response.status_code == 200
    rows = response.json()["rows"]
    assert rows == [
        {"username": "alice", "date": today, "total_tokens": 300, "cost_usd": 0.01, "turns": 1}
    ]
    assert "question" not in response.text


def test_usage_api_forbidden_for_normal_user(make_env):
    env = make_env()
    today = datetime.now(UTC).date().isoformat()
    response = env.client.get(
        f"/api/chat/usage?date_from={today}&date_to={today}", headers=env.headers("alice")
    )
    assert response.status_code == 403


@pytest.mark.parametrize(("days", "status"), [(92, 200), (120, 422)])
def test_usage_api_range_limit(make_env, days, status):
    env = make_env()
    start = datetime(2026, 5, 1).date()
    end = start + timedelta(days=days - 1)
    response = env.client.get(
        f"/api/chat/usage?date_from={start}&date_to={end}", headers=env.headers("admin")
    )
    assert response.status_code == status
