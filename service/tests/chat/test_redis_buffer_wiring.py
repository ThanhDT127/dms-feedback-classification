"""Chọn Answer Buffer theo CHAT_REDIS_URL và chạy lượt chat thật trên buffer Redis (b06 D4)."""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from dms.chat.ws.answer_buffer import InMemoryAnswerBuffer
from dms.chat.ws.redis_answer_buffer import RedisAnswerBuffer
from dms.chat.ws.services import build_answer_buffer, warn_if_buffer_not_shared
from dms.settings import Settings

from .test_chat_ws import Env, ask, receive_until_done, receive_until_seq

_REQUIRED = {
    "azure_tenant_id": "t",
    "azure_client_id": "c",
    "azure_client_secret": "s",
    "sharepoint_drive_id": "d",
    "sharepoint_root_folder_id": "r",
    "gemini_backend": "vertex",
    "gcp_project_id": "p",
    "jwt_secret_key": "test-secret-key-that-is-at-least-32-bytes-long",
}


def fake_redis():
    import fakeredis

    return fakeredis.FakeRedis(decode_responses=True)


# ── Chọn bản cài ──


def test_no_url_keeps_the_in_process_buffer():
    buffer = build_answer_buffer(Settings(**_REQUIRED))
    assert isinstance(buffer, InMemoryAnswerBuffer)


def test_url_is_validated():
    with pytest.raises(ValidationError):
        Settings(**_REQUIRED, chat_redis_url="localhost:6379")


def test_unreachable_redis_falls_back_and_logs(caplog):
    settings = Settings(**_REQUIRED, chat_redis_url="redis://127.0.0.1:59999/0")
    with caplog.at_level(logging.ERROR, logger="dms-chat"):
        buffer = build_answer_buffer(settings)
    assert isinstance(buffer, InMemoryAnswerBuffer)
    assert any(r.message == "chat_answer_buffer_redis_unavailable" for r in caplog.records)


def test_redis_buffer_is_treated_as_shared(caplog):
    buffer = RedisAnswerBuffer(fake_redis())
    with caplog.at_level(logging.WARNING, logger="dms-chat"):
        assert warn_if_buffer_not_shared(buffer, workers=4) is False
    assert not caplog.records
    assert warn_if_buffer_not_shared(InMemoryAnswerBuffer(), workers=4) is True


# ── Một lượt chat thật chạy trên buffer Redis ──


@pytest.fixture
def env(tmp_path, monkeypatch):
    environment = Env(tmp_path, monkeypatch, buffer=RedisAnswerBuffer(fake_redis()))
    yield environment
    environment.llm.gate.set()
    environment.services.shutdown()


def test_turn_streams_through_the_redis_buffer(env):
    with env.ws() as ws:
        reply = ask(ws)
        events = receive_until_done(ws, reply["answer_id"])
    seqs = [e["seq"] for e in events]
    assert seqs == list(range(1, len(seqs) + 1))
    assert events[-1]["type"] == "done"
    assert isinstance(env.services.buffer, RedisAnswerBuffer)


def test_resume_reads_events_written_by_another_connection(env):
    """Rớt kết nối rồi resume: event nằm ở Redis nên kết nối mới đọc tiếp được."""
    with env.ws() as ws:
        reply = ask(ws)
        answer_id = reply["answer_id"]
        first = receive_until_seq(ws, answer_id, 2)
    assert [e["seq"] for e in first][:2] == [1, 2]

    with env.ws() as ws2:
        ws2.send_json({"type": "resume", "answer_id": answer_id, "last_seq": 2})
        rest = receive_until_done(ws2, answer_id)

    assert [e["seq"] for e in rest] == list(range(3, 3 + len(rest)))
    assert rest[-1]["type"] == "done"
