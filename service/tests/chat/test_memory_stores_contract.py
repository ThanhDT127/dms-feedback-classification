"""Bộ test contract của ``SessionMemoryStore`` và ``ChatUsageLedger`` (b11 task 1.3).

Tham số hoá theo factory: khi Dev A có store thật, thêm factory vào danh sách là chạy lại được
toàn bộ bộ test này mà không phải chép test.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta

import pytest

from dms.chat.ai.budget.usage_ledger import InMemoryChatUsageLedger
from dms.chat.ai.memory.store import (
    InMemorySessionMemoryStore,
    MemoryVersionConflict,
    SessionMemory,
)
from dms.chat.db.memory_store import SqliteSessionMemoryStore
from dms.chat.db.migrations import apply_chat_migrations
from dms.chat.db.usage_ledger import SqliteChatUsageLedger

NOW = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)


def _sqlite_conn() -> sqlite3.Connection:
    """Một DB riêng trong RAM cho mỗi lần dựng fixture."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    apply_chat_migrations(conn)
    return conn


def _sqlite_memory_store() -> SqliteSessionMemoryStore:
    return SqliteSessionMemoryStore(_sqlite_conn())


def _sqlite_ledger() -> SqliteChatUsageLedger:
    return SqliteChatUsageLedger(_sqlite_conn())


MEMORY_STORES = [
    pytest.param(InMemorySessionMemoryStore, id="in_memory"),
    pytest.param(_sqlite_memory_store, id="sqlite"),
]
USAGE_LEDGERS = [
    pytest.param(InMemoryChatUsageLedger, id="in_memory"),
    pytest.param(_sqlite_ledger, id="sqlite"),
]


# ── SessionMemoryStore ──


@pytest.fixture(params=MEMORY_STORES)
def memory_store(request):
    return request.param()


def test_load_returns_none_for_unknown_session(memory_store):
    assert memory_store.load("khong-co") is None


def test_save_then_load_round_trip(memory_store):
    saved = memory_store.save(
        "s1",
        SessionMemory(summary="Hỏi về Nha Trang tháng 8", topics=("Nha Trang",)),
        if_version=0,
    )
    loaded = memory_store.load("s1")

    assert loaded is not None
    assert loaded.summary == "Hỏi về Nha Trang tháng 8"
    assert loaded.topics == ("Nha Trang",)
    assert loaded.version == saved.version == 1
    assert loaded.updated_at


def test_save_increments_version_each_time(memory_store):
    first = memory_store.save("s1", SessionMemory(summary="một"), if_version=0)
    second = memory_store.save("s1", SessionMemory(summary="hai"), if_version=first.version)
    assert (first.version, second.version) == (1, 2)
    assert memory_store.load("s1").summary == "hai"


def test_stale_version_is_rejected(memory_store):
    memory_store.save("s1", SessionMemory(summary="một"), if_version=0)
    with pytest.raises(MemoryVersionConflict):
        memory_store.save("s1", SessionMemory(summary="ghi đè"), if_version=0)
    assert memory_store.load("s1").summary == "một"


def test_sessions_are_isolated(memory_store):
    memory_store.save("s1", SessionMemory(summary="một"), if_version=0)
    memory_store.save("s2", SessionMemory(summary="hai"), if_version=0)
    assert memory_store.load("s1").summary == "một"
    assert memory_store.load("s2").summary == "hai"


def test_memory_round_trips_through_dict():
    memory = SessionMemory(
        summary="tóm tắt",
        topics=("a", "b"),
        summarized_until_message_id=42,
        method="llm",
        version=3,
        updated_at=NOW.isoformat(),
    )
    assert SessionMemory.from_dict(memory.to_dict()) == memory
    assert SessionMemory.from_dict(None) == SessionMemory()


# ── ChatUsageLedger ──


@pytest.fixture(params=USAGE_LEDGERS)
def ledger(request):
    return request.param()


def add(ledger, username: str, *, tokens: int = 100, at: datetime = NOW, request_id: str = "r1"):
    ledger.record(
        username=username,
        request_id=request_id,
        session_id="s1",
        call_type="chat_plan",
        model="gemini-test",
        prompt_tokens=tokens // 2,
        completion_tokens=tokens - tokens // 2,
        total_tokens=tokens,
        cost_usd=0.001,
        success=True,
        at=at,
    )


def test_total_tokens_counts_only_that_user(ledger):
    add(ledger, "an", tokens=100)
    add(ledger, "binh", tokens=500)
    assert ledger.total_tokens("an", NOW - timedelta(hours=1), NOW + timedelta(hours=1)) == 100


def test_total_tokens_respects_the_window(ledger):
    add(ledger, "an", tokens=100, at=NOW - timedelta(hours=2))
    add(ledger, "an", tokens=40, at=NOW)
    assert ledger.total_tokens("an", NOW - timedelta(minutes=30), NOW + timedelta(hours=1)) == 40


def test_total_tokens_window_is_half_open(ledger):
    add(ledger, "an", tokens=7, at=NOW)
    assert ledger.total_tokens("an", NOW, NOW + timedelta(seconds=1)) == 7
    assert ledger.total_tokens("an", NOW + timedelta(seconds=1), NOW + timedelta(hours=1)) == 0


def test_total_tokens_is_zero_for_unknown_user(ledger):
    add(ledger, "an")
    assert ledger.total_tokens("khong-co", NOW - timedelta(days=1), NOW + timedelta(days=1)) == 0


def test_daily_summary_groups_by_user_and_day(ledger):
    add(ledger, "an", tokens=100, at=NOW, request_id="r1")
    add(ledger, "an", tokens=50, at=NOW + timedelta(minutes=5), request_id="r1")
    add(ledger, "an", tokens=30, at=NOW + timedelta(days=1), request_id="r2")
    add(ledger, "binh", tokens=20, at=NOW, request_id="r3")

    rows = ledger.daily_summary(NOW.date(), NOW.date())

    assert [(row.username, row.total_tokens, row.turns) for row in rows] == [
        ("an", 150, 1),
        ("binh", 20, 1),
    ]


def test_daily_summary_excludes_days_outside_the_range(ledger):
    add(ledger, "an", at=NOW - timedelta(days=5))
    assert ledger.daily_summary(NOW.date(), NOW.date()) == []


def test_daily_summary_row_is_json_ready(ledger):
    add(ledger, "an", tokens=10)
    row = ledger.daily_summary(NOW.date(), NOW.date())[0].to_dict()
    assert set(row) == {"username", "date", "total_tokens", "cost_usd", "turns"}
    assert row["date"] == date(2026, 9, 17).isoformat()


def test_naive_datetimes_are_treated_as_utc(ledger):
    add(ledger, "an", tokens=11, at=datetime(2026, 9, 17, 10, 0))
    assert ledger.total_tokens("an", NOW - timedelta(minutes=1), NOW + timedelta(minutes=1)) == 11
