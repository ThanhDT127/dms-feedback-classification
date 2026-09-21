"""Dựng phiên chat có sẵn N lượt trên ChatStore SQLite trong RAM (b11)."""

from __future__ import annotations

import json
import sqlite3

from dms.chat.contract import MessageRole
from dms.chat.db.chat_store import ChatStore
from dms.chat.db.migrations import apply_chat_migrations


def memory_chat_store() -> ChatStore:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    apply_chat_migrations(conn)
    return ChatStore(lambda: conn)


def add_turns(
    store: ChatStore,
    session_id: str,
    count: int,
    *,
    start: int = 1,
    question: str = "Câu hỏi số {n} về Nha Trang",
    answer: str = "Tổng quan lượt {n}. Tổng số vấn đề {n}00",
    quotes: dict[int, list[dict]] | None = None,
) -> list[int]:
    """Thêm ``count`` cặp user/assistant; trả id tin nhắn assistant của từng lượt."""
    if store.get_session(session_id) is None:
        store.create_session(session_id=session_id, user_id="an", title="phiên thử")
    ids: list[int] = []
    for n in range(start, start + count):
        answer_id = f"a{n}"
        store.add_message(
            session_id,
            MessageRole.USER,
            question.format(n=n),
            metadata={"client_msg_id": f"c{n}", "answer_id": answer_id},
        )
        events = []
        if quotes and n in quotes:
            events.append(
                {"seq": 1, "type": "data_block", "data": {"kind": "quote", "payload": {"quotes": quotes[n]}}}
            )
        store.add_message(
            session_id,
            MessageRole.ASSISTANT,
            answer.format(n=n),
            metadata={"answer_id": answer_id, "events": events},
        )
        ids.append(int(store.get_messages(session_id, 10_000)[-1].message_id or 0))
    return ids


def summary_json(summary: str, topics: list[str] | None = None) -> str:
    return json.dumps({"summary": summary, "topics": topics or []}, ensure_ascii=False)
