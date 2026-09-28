"""Bản SQLite của ``SessionMemoryStore`` (spec ``chat-conversation-memory``, design b11 D2).

Ghi có điều kiện ``WHERE version = ?`` nên hai worker cùng tóm tắt một phiên thì chỉ một bên
thắng; bên thua nhận ``MemoryVersionConflict`` và bỏ lượt tóm tắt đó.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..ai.memory.store import MemoryVersionConflict, SessionMemory


class SqliteSessionMemoryStore:
    """Lưu ``memory_json`` vào bảng ``chat_session_memory``."""

    def __init__(self, conn_factory: Any) -> None:
        """Nhận cùng kiểu ``conn_factory`` như ``ChatStore``: callable hoặc connection."""
        self._conn_factory = conn_factory

    def _conn(self) -> sqlite3.Connection:
        # sqlite3.Connection cũng callable (biên dịch câu lệnh) nên phải xét kiểu trước.
        if isinstance(self._conn_factory, sqlite3.Connection):
            return self._conn_factory
        return self._conn_factory()

    def load(self, session_id: str) -> SessionMemory | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT memory_json, version FROM chat_session_memory WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        data = dict(json.loads(row[0]))
        # Phiên bản lấy từ cột, không lấy từ JSON: cột mới là nguồn chuẩn cho ghi có điều kiện.
        data["version"] = int(row[1])
        return SessionMemory.from_dict(data)

    def save(self, session_id: str, memory: SessionMemory, *, if_version: int) -> SessionMemory:
        saved = memory.at_version(if_version + 1)
        payload = json.dumps(saved.to_dict(), ensure_ascii=False)
        with self._conn() as conn:
            if if_version == 0:
                # Chưa có dòng nào: INSERT thất bại nghĩa là bên khác vừa ghi trước.
                try:
                    conn.execute(
                        """
                        INSERT INTO chat_session_memory (
                            session_id, memory_json, version, updated_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (session_id, payload, saved.version, saved.updated_at),
                    )
                except sqlite3.IntegrityError as exc:
                    raise MemoryVersionConflict(
                        f"session={session_id} đã có tóm tắt, expected={if_version}"
                    ) from exc
                return saved

            cursor = conn.execute(
                """
                UPDATE chat_session_memory
                SET memory_json = ?, version = ?, updated_at = ?
                WHERE session_id = ? AND version = ?
                """,
                (payload, saved.version, saved.updated_at, session_id, if_version),
            )
            if cursor.rowcount == 0:
                current = conn.execute(
                    "SELECT version FROM chat_session_memory WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                raise MemoryVersionConflict(
                    f"session={session_id} version={current[0] if current else 0} "
                    f"expected={if_version}"
                )
        return saved


__all__ = ["SqliteSessionMemoryStore"]
