from __future__ import annotations

import json

import pytest

from dms.chat.ws.protocol import (
    ERROR_TEXTS,
    AskMessage,
    BadMessage,
    CancelMessage,
    PingMessage,
    ProtocolError,
    ResumeMessage,
    ack,
    answer_event,
    answer_expired,
    error,
    parse_client_message,
    pong,
)


@pytest.mark.parametrize(
    ("raw", "cls"),
    [
        ({"type": "ask", "client_msg_id": "c1", "question": "Tổng quan tháng 8"}, AskMessage),
        ({"type": "ask", "client_msg_id": "c1", "session_id": "s1", "question": "x"}, AskMessage),
        ({"type": "resume", "answer_id": "a1", "last_seq": 0}, ResumeMessage),
        ({"type": "cancel", "answer_id": "a1"}, CancelMessage),
        ({"type": "ping"}, PingMessage),
    ],
)
def test_valid_client_messages(raw, cls):
    assert isinstance(parse_client_message(json.dumps(raw)), cls)
    assert isinstance(parse_client_message(raw), cls)


def test_ask_without_session_means_new_session():
    message = parse_client_message({"type": "ask", "client_msg_id": "c1", "question": "hi"})
    assert message.session_id is None


@pytest.mark.parametrize(
    "raw",
    [
        "không phải json",
        "[1, 2]",
        json.dumps({"type": "hello"}),
        json.dumps({"question": "thiếu type"}),
        json.dumps({"type": "ask", "client_msg_id": "c1"}),
        json.dumps({"type": "ask", "client_msg_id": "c1", "question": ""}),
        json.dumps({"type": "ask", "client_msg_id": "", "question": "x"}),
        json.dumps({"type": "ask", "client_msg_id": "c" * 65, "question": "x"}),
        json.dumps({"type": "resume", "answer_id": "a1", "last_seq": -1}),
        json.dumps({"type": "resume", "answer_id": "a1"}),
        json.dumps({"type": "cancel"}),
        json.dumps({"type": "ping", "extra": 1}),
        b"\xff\xfe",
    ],
)
def test_bad_messages_raise(raw):
    with pytest.raises(BadMessage):
        parse_client_message(raw)


def test_bad_ask_keeps_client_msg_id_for_error_reply():
    with pytest.raises(BadMessage) as caught:
        parse_client_message({"type": "ask", "client_msg_id": "c9", "question": ""})
    assert caught.value.client_msg_id == "c9"


def test_server_messages_shape():
    assert ack(client_msg_id="c1", answer_id="a1", session_id="s1") == {
        "type": "ack",
        "client_msg_id": "c1",
        "answer_id": "a1",
        "session_id": "s1",
    }
    event = {"seq": 3, "type": "commentary", "data": {"text": "..."}}
    assert answer_event("a1", event) == {"answer_id": "a1", **event}
    assert answer_expired("a1") == {"type": "answer_expired", "answer_id": "a1"}
    assert pong() == {"type": "pong"}


def test_error_message_has_no_seq_and_vietnamese_text():
    message = error(ProtocolError.BUSY, client_msg_id="c1")
    assert message == {
        "type": "error",
        "code": "BUSY",
        "text": ERROR_TEXTS[ProtocolError.BUSY],
        "client_msg_id": "c1",
    }
    assert "client_msg_id" not in error(ProtocolError.BAD_MESSAGE)
    assert set(ERROR_TEXTS) == set(ProtocolError)
