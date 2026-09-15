"""ChatStore — Persistence for Chat Sessions & Messages (Khối [4] & Khối [6])."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

from ..contract import ChatMessage, ChatSession, MessageRole

logger = logging.getLogger("dms-chat-store")


class ChatStore:
    """SQLite-backed storage for chat sessions and message history."""

    def __init__(self, conn_factory) -> None:
        """Nhận conn_factory (hàm hoặc callable trả về sqlite3.Connection)."""
        self._conn_factory = conn_factory

    def _conn(self) -> sqlite3.Connection:
        if callable(self._conn_factory):
            return self._conn_factory()
        return self._conn_factory

    def create_session(
        self,
        session_id: str,
        user_id: str,
        title: str = "",
        active_domain: str = "",
        ttl_days: int = 7,
    ) -> ChatSession:
        """Tạo mới một phiên hội thoại."""
        now = datetime.now(UTC)
        now_iso = now.isoformat()
        expires_at = (now + timedelta(days=ttl_days)).isoformat()

        session = ChatSession(
            session_id=session_id,
            user_id=user_id,
            title=title or "Cuộc trò chuyện mới",
            active_domain=active_domain,
            slots_json="{}",
            token_count=0,
            created_at=now_iso,
            expires_at=expires_at,
        )

        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO chat_sessions (
                    session_id, user_id, title, active_domain, slots_json,
                    token_count, created_at, updated_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.session_id,
                    session.user_id,
                    session.title,
                    session.active_domain,
                    session.slots_json,
                    session.token_count,
                    session.created_at,
                    now_iso,
                    session.expires_at,
                ),
            )
        return session

    def get_session(self, session_id: str) -> ChatSession | None:
        """Tìm session theo session_id."""
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM chat_sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if not row:
                return None
            return ChatSession(
                session_id=row["session_id"],
                user_id=row["user_id"],
                title=row["title"],
                active_domain=row["active_domain"],
                slots_json=row["slots_json"],
                token_count=row["token_count"],
                created_at=row["created_at"],
                expires_at=row["expires_at"],
            )

    def list_sessions(self, user_id: str, limit: int = 50) -> list[ChatSession]:
        """Liệt kê các session gần nhất của một user."""
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT * FROM chat_sessions
                WHERE user_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (user_id, int(limit)),
            ).fetchall()
            return [
                ChatSession(
                    session_id=r["session_id"],
                    user_id=r["user_id"],
                    title=r["title"],
                    active_domain=r["active_domain"],
                    slots_json=r["slots_json"],
                    token_count=r["token_count"],
                    created_at=r["created_at"],
                    expires_at=r["expires_at"],
                )
                for r in rows
            ]

    def update_session(self, session_id: str, **fields: Any) -> bool:
        """Cập nhật các trường của session (title, slots_json, token_count...)."""
        allowed = {"title", "active_domain", "slots_json", "token_count", "expires_at"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False

        now_iso = datetime.now(UTC).isoformat()
        updates["updated_at"] = now_iso

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [session_id]

        with self._conn() as conn:
            cursor = conn.execute(
                f"UPDATE chat_sessions SET {set_clause} WHERE session_id = ?",
                params,
            )
            return cursor.rowcount > 0

    def delete_session(self, session_id: str) -> bool:
        """Xóa session và toàn bộ tin nhắn liên quan (CASCADE)."""
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM chat_sessions WHERE session_id = ?", (session_id,)
            )
            return cursor.rowcount > 0

    def add_message(
        self,
        session_id: str,
        role: MessageRole,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> ChatMessage:
        """Thêm tin nhắn mới vào phiên hội thoại."""
        now_iso = datetime.now(UTC).isoformat()
        meta_str = json.dumps(metadata, ensure_ascii=False) if metadata else None

        with self._conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO chat_messages (session_id, role, content, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, role.value, content, meta_str, now_iso),
            )
            msg_id = cursor.lastrowid
            # Cập nhật updated_at cho session
            conn.execute(
                "UPDATE chat_sessions SET updated_at = ? WHERE session_id = ?",
                (now_iso, session_id),
            )

        return ChatMessage(
            role=role,
            content=content,
            metadata_json=meta_str,
            created_at=now_iso,
            message_id=msg_id,
        )

    def get_messages(self, session_id: str, limit: int = 100) -> list[ChatMessage]:
        """Lấy danh sách tin nhắn theo thứ tự thời gian tăng dần."""
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT message_id, role, content, metadata_json, created_at
                FROM chat_messages
                WHERE session_id = ?
                ORDER BY message_id ASC
                LIMIT ?
                """,
                (session_id, int(limit)),
            ).fetchall()

            return [
                ChatMessage(
                    role=MessageRole(r["role"]),
                    content=r["content"],
                    metadata_json=r["metadata_json"],
                    created_at=r["created_at"],
                    message_id=r["message_id"],
                )
                for r in rows
            ]

    def clear_expired_sessions(self, now_iso: str | None = None) -> int:
        """Dọn dẹp các session đã quá hạn TTL."""
        now = now_iso or datetime.now(UTC).isoformat()
        with self._conn() as conn:
            cursor = conn.execute(
                "DELETE FROM chat_sessions WHERE expires_at < ?", (now,)
            )
            return cursor.rowcount
