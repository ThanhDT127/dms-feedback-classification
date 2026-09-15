"""Unit tests for ChatStore (session and message persistence)."""

from __future__ import annotations

import sqlite3

import pytest

from dms.chat.contract import MessageRole
from dms.chat.db.chat_store import ChatStore
from dms.chat.db.migrations import apply_chat_migrations


@pytest.fixture
def chat_store_memory():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    apply_chat_migrations(conn)
    return ChatStore(lambda: conn)


def test_session_lifecycle(chat_store_memory):
    store = chat_store_memory

    # 1. Tạo session
    session = store.create_session(
        session_id="sess-001",
        user_id="user1",
        title="Hỏi về đèn LED",
        active_domain="products",
    )
    assert session.session_id == "sess-001"
    assert session.title == "Hỏi về đèn LED"
    assert session.active_domain == "products"

    # 2. Lấy session
    fetched = store.get_session("sess-001")
    assert fetched is not None
    assert fetched.session_id == "sess-001"

    # 3. Cập nhật session
    updated = store.update_session("sess-001", title="Đã đổi tiêu đề", token_count=150)
    assert updated is True
    fetched_updated = store.get_session("sess-001")
    assert fetched_updated.title == "Đã đổi tiêu đề"
    assert fetched_updated.token_count == 150

    # 4. Liệt kê session
    store.create_session("sess-002", "user1", "Phiên thứ 2")
    store.create_session("sess-003", "user2", "Phiên của user khác")
    user1_sessions = store.list_sessions("user1")
    assert len(user1_sessions) == 2
    assert {s.session_id for s in user1_sessions} == {"sess-001", "sess-002"}

    # 5. Xóa session
    deleted = store.delete_session("sess-001")
    assert deleted is True
    assert store.get_session("sess-001") is None


def test_message_history(chat_store_memory):
    store = chat_store_memory
    store.create_session("sess-msg-test", "user1")

    # Thêm tin nhắn
    msg1 = store.add_message("sess-msg-test", MessageRole.USER, "Chào bot, đèn AT04 giá bao nhiêu?")
    msg2 = store.add_message(
        "sess-msg-test",
        MessageRole.ASSISTANT,
        "Đèn AT04 có giá 120.000 VNĐ.",
        metadata={"pattern": "sql_template"},
    )

    assert msg1.message_id is not None
    assert msg2.message_id is not None
    assert msg1.role == MessageRole.USER
    assert msg2.role == MessageRole.ASSISTANT

    # Lấy lịch sử tin nhắn
    messages = store.get_messages("sess-msg-test")
    assert len(messages) == 2
    assert messages[0].content == "Chào bot, đèn AT04 giá bao nhiêu?"
    assert messages[1].content == "Đèn AT04 có giá 120.000 VNĐ."
    assert "sql_template" in messages[1].metadata_json


def test_clear_expired_sessions(chat_store_memory):
    store = chat_store_memory

    # Tạo 1 session đã hết hạn (ttl âm)
    store.create_session("sess-expired", "user1", ttl_days=-1)
    # Tạo 1 session còn hạn
    store.create_session("sess-active", "user1", ttl_days=7)

    cleared = store.clear_expired_sessions()
    assert cleared == 1
    assert store.get_session("sess-expired") is None
    assert store.get_session("sess-active") is not None
