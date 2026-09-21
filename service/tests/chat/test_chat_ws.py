"""Spec ``chat-websocket-protocol``, ``chat-answer-resume`` và REST (b06 task 4.5, 5.4)."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from dms.chat.ai.types import LLMResult
from dms.chat.db.chat_store import ChatStore
from dms.chat.db.migrations import apply_chat_migrations
from dms.chat.mock_executor import MockQueryExecutor
from dms.chat.ws import chat_endpoint
from dms.chat.ws.answer_buffer import InMemoryAnswerBuffer
from dms.chat.ws.services import build_chat_services, warn_if_buffer_not_shared
from dms.jwt_utils import create_token
from dms.settings import Settings
from dms.token_blacklist import clear
from dms.web import deps
from dms.web.app import create_app
from dms.web.rate_limit import limiter
from dms.web.ws.connection_limiter import WS_LIMIT_CLOSE_CODE, ws_connection_limiter

from .ai_fakes import FakeTextStream, StaticMetadataProvider

SECRET = "test-secret-key-that-is-at-least-32-bytes-long"
TV1 = "Truyền thống Vùng 1"
TV2 = "Truyền thống Vùng 2"
QUESTION = "Tổng quan tháng 8"
PLAN_JSON = json.dumps(
    {
        "intent": "OVERVIEW",
        "answer_shape": "number",
        "confidence": 0.9,
        "steps": [{"pattern": "sql_template", "function_name": "get_overview", "params": {}}],
    }
)


class GatedLLM:
    """Planner luôn trả OVERVIEW; ``stream`` chờ ``gate`` để giữ lượt đang chạy."""

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.gate.set()

    def generate_json(self, prompt, *, call_type, system_instruction=None):
        return LLMResult(text=PLAN_JSON, usage={"total_tokens": 10})

    def stream(
        self,
        prompt,
        *,
        call_type,
        system_instruction=None,
        temperature=None,
        max_output_tokens=None,
    ):
        self.gate.wait(timeout=10)
        return FakeTextStream(["Kỳ này có tổng quan như các khối số liệu phía trên."])


class FakeUserStore:
    def __init__(self) -> None:
        self.users = {
            "alice": {
                "username": "alice",
                "role": "user",
                "display_name": "Alice",
                "is_active": True,
                "unit_ids": [TV1, TV2],
            },
            "bob": {
                "username": "bob",
                "role": "user",
                "display_name": "Bob",
                "is_active": True,
                "unit_ids": [TV1],
            },
            "admin": {
                "username": "admin",
                "role": "admin",
                "display_name": "Admin",
                "is_active": True,
                "unit_ids": [],
            },
        }

    def get_user(self, username):
        user = self.users.get(username)
        return dict(user, unit_ids=list(user["unit_ids"])) if user else None


class Env:
    def __init__(self, tmp_path: Path, monkeypatch, **overrides) -> None:
        values = dict(
            azure_tenant_id="tenant",
            azure_client_id="client",
            azure_client_secret="secret-value",
            sharepoint_drive_id="drive",
            sharepoint_root_folder_id="root",
            gemini_backend="vertex",
            gcp_project_id="project",
            data_dir=tmp_path / "data",
            work_dir=tmp_path / "work",
            log_dir=tmp_path / "logs",
            jwt_secret_key=SECRET,
            default_admin_password="admin-password",
            chat_enabled=True,
        )
        values.update(overrides)
        self.settings = Settings(**values)
        self.llm = GatedLLM()
        self.users = FakeUserStore()
        self.offset = [0.0]
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.execute("PRAGMA foreign_keys = ON")
        apply_chat_migrations(conn)
        self.store = ChatStore(lambda: conn)
        self.buffer = InMemoryAnswerBuffer(monotonic=lambda: time.monotonic() + self.offset[0])
        self.services = build_chat_services(
            self.settings,
            llm=self.llm,
            executor=MockQueryExecutor(),
            metadata=StaticMetadataProvider(),
            store=self.store,
            buffer=self.buffer,
        )
        monkeypatch.setattr(deps, "get_settings", lambda: self.settings)
        monkeypatch.setattr(deps, "get_user_store", lambda: self.users)
        monkeypatch.setattr(
            deps, "get_chat_services", lambda: self.services if self.settings.chat_enabled else None
        )
        self.client = TestClient(create_app())

    def token(self, username="alice", minutes=30):
        return create_token(username, "access", SECRET, expires_minutes=minutes)

    def ws(self, username="alice"):
        return self.client.websocket_connect(f"/ws/chat?token={self.token(username)}")

    def headers(self, username="alice"):
        return {"Authorization": f"Bearer {self.token(username)}"}


@pytest.fixture(autouse=True)
def reset_state():
    clear()
    limiter.reset()
    ws_connection_limiter.reset()
    yield
    clear()
    limiter.reset()
    ws_connection_limiter.reset()


@pytest.fixture
def env(tmp_path, monkeypatch):
    environment = Env(tmp_path, monkeypatch)
    yield environment
    environment.llm.gate.set()
    environment.services.shutdown()


def ask(ws, question=QUESTION, *, client_msg_id="m1", session_id=None):
    ws.send_json(
        {
            "type": "ask",
            "client_msg_id": client_msg_id,
            "session_id": session_id,
            "question": question,
        }
    )
    reply = ws.receive_json()
    assert reply["type"] == "ack", reply
    return reply


def receive_until_done(ws, answer_id):
    events = []
    while True:
        message = ws.receive_json()
        assert message.get("answer_id") == answer_id, message
        events.append(message)
        if message["type"] == "done":
            return events


def receive_until_seq(ws, answer_id, seq):
    events = []
    while not events or events[-1]["seq"] < seq:
        message = ws.receive_json()
        assert message.get("answer_id") == answer_id, message
        events.append(message)
    return events


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("timeout waiting for condition")


# ── Xác thực ──


def test_missing_token_closes_4001(env):
    with pytest.raises(WebSocketDisconnect) as caught:
        with env.client.websocket_connect("/ws/chat"):
            pass
    assert caught.value.code == 4001


def test_inactive_user_cannot_connect(env):
    env.users.users["alice"]["is_active"] = False
    with pytest.raises(WebSocketDisconnect) as caught:
        with env.ws():
            pass
    assert caught.value.code == 4001


def test_fourth_connection_is_rejected(env):
    with env.ws() as c1, env.ws() as c2, env.ws() as c3:
        for conn in (c1, c2, c3):
            conn.send_json({"type": "ping"})
            assert conn.receive_json() == {"type": "pong"}
        with pytest.raises(WebSocketDisconnect) as caught:
            with env.ws() as c4:
                c4.receive_json()
        assert caught.value.code == WS_LIMIT_CLOSE_CODE


# ── Message và lỗi giao thức ──


def test_bad_message_keeps_connection_open(env):
    with env.ws() as ws:
        ws.send_text("abc")
        reply = ws.receive_json()
        assert reply["type"] == "error" and reply["code"] == "BAD_MESSAGE"
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_successful_ask_acks_then_streams_to_done_with_stages(env):
    with env.ws() as ws:
        acked = ask(ws)
        assert acked["client_msg_id"] == "m1" and acked["answer_id"] and acked["session_id"]
        events = receive_until_done(ws, acked["answer_id"])

    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    stages = [e["data"]["stage"] for e in events if e["type"] == "status"]
    assert stages == ["understanding", "planning", "querying", "writing"]
    assert any(e["type"] == "data_block" for e in events)
    assert events[-1]["data"]["status"] in {"ok", "partial"}


def test_question_too_long_creates_nothing(tmp_path, monkeypatch):
    environment = Env(tmp_path, monkeypatch, chat_max_question_chars=10)
    try:
        with environment.ws() as ws:
            ws.send_json({"type": "ask", "client_msg_id": "m1", "question": "x" * 11})
            reply = ws.receive_json()
        assert reply == {
            "type": "error",
            "code": "INPUT_TOO_LONG",
            "text": reply["text"],
            "client_msg_id": "m1",
        }
        assert environment.buffer.active_count("alice") == 0
        assert environment.store.list_sessions("alice") == []
    finally:
        environment.services.shutdown()


def test_scope_is_rebuilt_from_user_store_on_every_ask(env, monkeypatch):
    seen = []
    original = env.services.runner.submit

    def spy(**kwargs):
        seen.append(list(kwargs["turn"].scope.unit_ids))
        return original(**kwargs)

    monkeypatch.setattr(env.services.runner, "submit", spy)
    with env.ws() as ws:
        first = ask(ws, client_msg_id="m1")
        receive_until_done(ws, first["answer_id"])
        env.users.users["alice"]["unit_ids"] = [TV1]
        second = ask(ws, client_msg_id="m2", session_id=first["session_id"])
        receive_until_done(ws, second["answer_id"])

    assert seen == [[TV1, TV2], [TV1]]


def test_expired_token_on_open_connection_closes_before_creating_answer(env, monkeypatch):
    with env.ws() as ws:
        monkeypatch.setattr(chat_endpoint, "time", SimpleNamespace(time=lambda: 10**12))
        ws.send_json({"type": "ask", "client_msg_id": "m1", "question": QUESTION})
        with pytest.raises(WebSocketDisconnect) as caught:
            ws.receive_json()
    assert caught.value.code == 4001
    assert env.buffer.active_count("alice") == 0
    assert env.store.list_sessions("alice") == []


def test_user_deactivated_between_asks_closes_4001(env):
    with env.ws() as ws:
        env.users.users["alice"]["is_active"] = False
        ws.send_json({"type": "ask", "client_msg_id": "m1", "question": QUESTION})
        with pytest.raises(WebSocketDisconnect) as caught:
            ws.receive_json()
    assert caught.value.code == 4001


# ── Giới hạn ──


def test_second_ask_while_first_is_running_is_busy(env):
    env.llm.gate.clear()
    with env.ws() as ws:
        first = ask(ws, client_msg_id="m1")
        ws.send_json({"type": "ask", "client_msg_id": "m2", "question": QUESTION})
        while True:
            message = ws.receive_json()
            if message["type"] == "error":
                break
        assert message["code"] == "BUSY" and message["client_msg_id"] == "m2"
        env.llm.gate.set()
        events = list(receive_until_done_lenient(ws, first["answer_id"]))
    assert events[-1]["type"] == "done"


def receive_until_done_lenient(ws, answer_id):
    while True:
        message = ws.receive_json()
        if message.get("answer_id") != answer_id:
            continue
        yield message
        if message["type"] == "done":
            return


def test_eleventh_ask_in_a_minute_is_rate_limited(env):
    with env.ws() as ws:
        session_id = None
        for i in range(10):
            acked = ask(ws, client_msg_id=f"m{i}", session_id=session_id)
            session_id = acked["session_id"]
            receive_until_done(ws, acked["answer_id"])
        ws.send_json(
            {"type": "ask", "client_msg_id": "m10", "session_id": session_id, "question": QUESTION}
        )
        reply = ws.receive_json()
    assert reply["type"] == "error" and reply["code"] == "RATE_LIMITED"


def test_session_of_other_user_is_not_found_and_nothing_is_written(env):
    with env.ws("alice") as ws:
        acked = ask(ws)
        receive_until_done(ws, acked["answer_id"])
    wait_for(lambda: len(env.store.get_messages(acked["session_id"])) == 2)

    with env.ws("bob") as ws:
        ws.send_json(
            {
                "type": "ask",
                "client_msg_id": "b1",
                "session_id": acked["session_id"],
                "question": QUESTION,
            }
        )
        reply = ws.receive_json()
    assert reply["code"] == "SESSION_NOT_FOUND"
    time.sleep(0.1)
    assert len(env.store.get_messages(acked["session_id"])) == 2


def test_ping_keeps_idle_connection_open_then_idle_closes_1000(tmp_path, monkeypatch):
    environment = Env(tmp_path, monkeypatch, chat_ws_idle_timeout_seconds=1)
    try:
        with environment.ws() as ws:
            for _ in range(6):
                ws.send_json({"type": "ping"})
                assert ws.receive_json() == {"type": "pong"}
                time.sleep(0.5)
            with pytest.raises(WebSocketDisconnect) as caught:
                ws.receive_json()
        assert caught.value.code == 1000
    finally:
        environment.services.shutdown()


# ── Resume, huỷ, ngắt kết nối ──


def test_resume_mid_answer_on_new_connection_has_no_gaps_or_duplicates(env):
    env.llm.gate.clear()
    with env.ws() as ws:
        acked = ask(ws)
        answer_id = acked["answer_id"]
        first = receive_until_seq(ws, answer_id, 2)

    with env.ws() as ws:
        ws.send_json({"type": "resume", "answer_id": answer_id, "last_seq": 2})
        env.llm.gate.set()
        rest = receive_until_done(ws, answer_id)

    seqs = [e["seq"] for e in first + rest]
    assert seqs == list(range(1, len(seqs) + 1))


def test_resume_after_done_returns_tail(env):
    with env.ws() as ws:
        acked = ask(ws)
        events = receive_until_done(ws, acked["answer_id"])
        last = events[-1]["seq"]
        ws.send_json({"type": "resume", "answer_id": acked["answer_id"], "last_seq": last - 3})
        tail = receive_until_done(ws, acked["answer_id"])
    assert [e["seq"] for e in tail] == [last - 2, last - 1, last]


@pytest.mark.parametrize("answer_of", ["alice", "nobody"])
def test_resume_foreign_or_unknown_answer_is_not_found(env, answer_of):
    answer_id = "does-not-exist"
    if answer_of == "alice":
        with env.ws("alice") as ws:
            acked = ask(ws)
            receive_until_done(ws, acked["answer_id"])
            answer_id = acked["answer_id"]
    with env.ws("bob") as ws:
        ws.send_json({"type": "resume", "answer_id": answer_id, "last_seq": 0})
        reply = ws.receive_json()
        ws.send_json({"type": "cancel", "answer_id": answer_id})
        cancel_reply = ws.receive_json()
    assert reply["type"] == "error" and reply["code"] == "ANSWER_NOT_FOUND"
    assert cancel_reply["code"] == "ANSWER_NOT_FOUND"


def test_resume_after_ttl_gets_answer_expired(env):
    with env.ws() as ws:
        acked = ask(ws)
        receive_until_done(ws, acked["answer_id"])
        env.offset[0] += env.settings.chat_answer_buffer_ttl_seconds + 1
        ws.send_json({"type": "resume", "answer_id": acked["answer_id"], "last_seq": 0})
        reply = ws.receive_json()
    assert reply == {"type": "answer_expired", "answer_id": acked["answer_id"]}


def test_cancel_from_another_connection_ends_with_done_cancelled(env):
    env.llm.gate.clear()
    with env.ws() as ws1:
        acked = ask(ws1)
        receive_until_seq(ws1, acked["answer_id"], 1)
        with env.ws() as ws2:
            ws2.send_json({"type": "cancel", "answer_id": acked["answer_id"]})
            ws2.send_json({"type": "ping"})
            assert ws2.receive_json() == {"type": "pong"}
        wait_for(lambda: env.buffer.is_cancel_requested(acked["answer_id"]))
        env.llm.gate.set()
        events = receive_until_done(ws1, acked["answer_id"])
    assert events[-1]["data"]["status"] == "cancelled"


def test_disconnect_does_not_cancel_the_turn(env):
    env.llm.gate.clear()
    with env.ws() as ws:
        acked = ask(ws)
    env.llm.gate.set()
    wait_for(lambda: env.buffer.meta(acked["answer_id"]).complete)
    events = env.buffer.read(acked["answer_id"], 0, timeout_ms=0).events
    assert events[-1]["type"] == "done" and events[-1]["data"]["status"] != "cancelled"


# ── Lưu lượt, audit ──


def test_completed_turn_is_persisted_and_audited_once(env, caplog):
    with caplog.at_level(logging.INFO, logger="dms-chat-audit"):
        with env.ws() as ws:
            acked = ask(ws)
            receive_until_done(ws, acked["answer_id"])
        wait_for(lambda: len(env.store.get_messages(acked["session_id"])) == 2)
        wait_for(lambda: any(r.getMessage() == "chat_turn_audit" for r in caplog.records))

    messages = env.client.get(
        f"/api/chat/sessions/{acked['session_id']}/messages", headers=env.headers()
    ).json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert all(e["type"] != "status" for e in messages[1]["metadata"]["events"])
    audits = [r for r in caplog.records if r.getMessage() == "chat_turn_audit"]
    assert len(audits) == 1
    assert QUESTION not in json.dumps(audits[0].audit, ensure_ascii=False)


# ── REST và feature flag ──


def test_rest_sessions_owner_only(env):
    with env.ws() as ws:
        acked = ask(ws)
        receive_until_done(ws, acked["answer_id"])
    session_id = acked["session_id"]
    wait_for(lambda: len(env.store.get_messages(session_id)) == 2)

    listed = env.client.get("/api/chat/sessions", headers=env.headers()).json()["sessions"]
    assert [s["session_id"] for s in listed] == [session_id]
    assert env.client.get("/api/chat/sessions", headers=env.headers("bob")).json()["sessions"] == []
    assert (
        env.client.get(
            f"/api/chat/sessions/{session_id}/messages", headers=env.headers("admin")
        ).status_code
        == 404
    )

    bad = env.client.patch(
        f"/api/chat/sessions/{session_id}", json={"title": ""}, headers=env.headers()
    )
    assert bad.status_code == 422
    blank = env.client.patch(
        f"/api/chat/sessions/{session_id}", json={"title": "   "}, headers=env.headers()
    )
    assert blank.status_code == 422
    assert env.store.get_session(session_id).title == QUESTION
    renamed = env.client.patch(
        f"/api/chat/sessions/{session_id}", json={"title": "Báo cáo"}, headers=env.headers()
    )
    assert renamed.status_code == 200 and renamed.json()["title"] == "Báo cáo"
    assert (
        env.client.patch(
            f"/api/chat/sessions/{session_id}", json={"title": "x"}, headers=env.headers("bob")
        ).status_code
        == 404
    )

    assert (
        env.client.delete(
            f"/api/chat/sessions/{session_id}", headers=env.headers("bob")
        ).status_code
        == 404
    )
    assert (
        env.client.delete(f"/api/chat/sessions/{session_id}", headers=env.headers()).status_code
        == 200
    )
    assert (
        env.client.get(
            f"/api/chat/sessions/{session_id}/messages", headers=env.headers()
        ).status_code
        == 404
    )


def test_rest_requires_login(env):
    assert env.client.get("/api/chat/sessions").status_code == 401
    assert env.client.get("/api/chat/config").status_code == 401


def test_config_when_enabled(env):
    body = env.client.get("/api/chat/config", headers=env.headers()).json()
    assert body == {"enabled": True, "milestone": "M1", "max_question_chars": 1000}


def test_chat_disabled_registers_only_config(tmp_path, monkeypatch):
    environment = Env(tmp_path, monkeypatch, chat_enabled=False)
    try:
        body = environment.client.get("/api/chat/config", headers=environment.headers()).json()
        assert body["enabled"] is False
        assert (
            environment.client.get("/api/chat/sessions", headers=environment.headers()).status_code
            == 404
        )
        paths = {getattr(route, "path", "") for route in environment.client.app.routes}
        assert "/ws/chat" not in paths
        assert not any(p.startswith("/api/chat/sessions") for p in paths)
    finally:
        environment.services.shutdown()


def test_answer_buffer_not_shared_warning(caplog, monkeypatch):
    buffer = InMemoryAnswerBuffer()
    with caplog.at_level(logging.WARNING, logger="dms-chat"):
        assert warn_if_buffer_not_shared(buffer, workers=1) is False
        assert warn_if_buffer_not_shared(buffer, workers=2) is True
    assert [r.getMessage() for r in caplog.records].count("answer_buffer_not_shared") == 1


def test_build_services_warns_once_with_multiple_workers(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("GUNICORN_WORKERS", "2")
    with caplog.at_level(logging.WARNING, logger="dms-chat"):
        environment = Env(tmp_path, monkeypatch)
    environment.services.shutdown()
    assert [r.getMessage() for r in caplog.records].count("answer_buffer_not_shared") == 1
