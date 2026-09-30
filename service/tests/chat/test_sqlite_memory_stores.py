"""Phần chỉ đúng với bản SQLite: sống qua khởi động lại, dọn theo phiên, và nối vào services."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from dms.chat.ai.budget.usage_ledger import InMemoryChatUsageLedger
from dms.chat.ai.memory.store import (
    InMemorySessionMemoryStore,
    MemoryVersionConflict,
    SessionMemory,
)
from dms.chat.db.chat_store import ChatStore
from dms.chat.db.memory_store import SqliteSessionMemoryStore
from dms.chat.db.migrations import apply_chat_migrations
from dms.chat.db.usage_ledger import SqliteChatUsageLedger

NOW = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "chat.db"
    conn = sqlite3.connect(path)
    apply_chat_migrations(conn)
    conn.close()
    return path


def connect(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def test_memory_survives_reopening_the_database(db_path):
    first = connect(db_path)
    SqliteSessionMemoryStore(first).save(
        "s1", SessionMemory(summary="Nha Trang tháng 8", topics=("Nha Trang",)), if_version=0
    )
    first.close()

    second = connect(db_path)
    loaded = SqliteSessionMemoryStore(second).load("s1")

    assert loaded is not None
    assert loaded.summary == "Nha Trang tháng 8"
    assert loaded.topics == ("Nha Trang",)
    assert loaded.version == 1


def test_version_column_is_the_source_of_truth(db_path):
    conn = connect(db_path)
    store = SqliteSessionMemoryStore(conn)
    store.save("s1", SessionMemory(summary="một"), if_version=0)
    # JSON bị sửa lệch (ví dụ do bản ghi cũ): version vẫn lấy từ cột.
    conn.execute(
        "UPDATE chat_session_memory SET memory_json = ? WHERE session_id = ?",
        ('{"summary": "một", "version": 99}', "s1"),
    )

    assert store.load("s1").version == 1


def test_two_writers_racing_on_a_new_session_leave_one_winner(db_path):
    one = SqliteSessionMemoryStore(connect(db_path))
    two = SqliteSessionMemoryStore(connect(db_path))

    one.save("s1", SessionMemory(summary="worker A"), if_version=0)
    with pytest.raises(MemoryVersionConflict):
        two.save("s1", SessionMemory(summary="worker B"), if_version=0)

    assert one.load("s1").summary == "worker A"


def test_deleting_a_session_drops_its_memory(db_path):
    conn = connect(db_path)
    store = ChatStore(lambda: conn)
    store.create_session("s1", "an")
    SqliteSessionMemoryStore(conn).save("s1", SessionMemory(summary="một"), if_version=0)

    assert store.delete_session("s1") is True
    assert SqliteSessionMemoryStore(conn).load("s1") is None


def test_expiring_sessions_drops_their_memory(db_path):
    conn = connect(db_path)
    store = ChatStore(lambda: conn)
    store.create_session("s1", "an", ttl_days=1)
    store.create_session("s2", "an", ttl_days=30)
    memory = SqliteSessionMemoryStore(conn)
    memory.save("s1", SessionMemory(summary="hết hạn"), if_version=0)
    memory.save("s2", SessionMemory(summary="còn hạn"), if_version=0)

    later = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    assert store.clear_expired_sessions(later) == 1
    assert memory.load("s1") is None
    assert memory.load("s2").summary == "còn hạn"


def test_usage_survives_reopening_the_database(db_path):
    first = connect(db_path)
    SqliteChatUsageLedger(first).record(
        username="an",
        request_id="r1",
        session_id="s1",
        call_type="chat_plan",
        model="gemini-test",
        prompt_tokens=60,
        completion_tokens=40,
        total_tokens=100,
        cost_usd=0.002,
        success=True,
        at=NOW,
    )
    first.close()

    ledger = SqliteChatUsageLedger(connect(db_path))
    assert ledger.total_tokens("an", NOW, NOW + timedelta(minutes=1)) == 100
    assert ledger.daily_summary(NOW.date(), NOW.date())[0].cost_usd == pytest.approx(0.002)


def test_usage_cleanup_removes_only_old_rows(db_path):
    ledger = SqliteChatUsageLedger(connect(db_path))
    for offset, request_id in ((timedelta(days=200), "cu"), (timedelta(0), "moi")):
        ledger.record(
            username="an",
            request_id=request_id,
            session_id=None,
            call_type="chat_plan",
            model="gemini-test",
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
            cost_usd=0.0,
            success=True,
            at=NOW - offset,
        )

    assert ledger.cleanup(NOW - timedelta(days=90)) == 1
    assert ledger.total_tokens("an", NOW - timedelta(days=365), NOW + timedelta(days=1)) == 2


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


def build_services(tmp_path, **kwargs):
    from dms.chat.ws.services import build_chat_services
    from dms.settings import Settings

    settings = Settings(**_REQUIRED, work_dir=str(tmp_path))
    return build_chat_services(settings, llm=object(), **kwargs)


def test_services_pick_the_sqlite_stores_when_they_own_the_connections(tmp_path):
    services = build_services(tmp_path)
    try:
        assert isinstance(services.memory, SqliteSessionMemoryStore)
        assert isinstance(services.ledger, SqliteChatUsageLedger)
    finally:
        if services.connections is not None:
            services.connections.close_all()


def test_services_fall_back_to_in_memory_when_the_store_is_injected(tmp_path, db_path):
    """Caller tự truyền ChatStore (test, script) thì không có kết nối để dùng SQLite."""
    services = build_services(tmp_path, store=ChatStore(lambda: connect(db_path)))
    assert isinstance(services.memory, InMemorySessionMemoryStore)
    assert isinstance(services.ledger, InMemoryChatUsageLedger)
