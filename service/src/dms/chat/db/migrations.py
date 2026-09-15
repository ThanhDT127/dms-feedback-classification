"""SQLite schema migrations for DMS Chatbot (Khối [4] DATA).

Tạo bảng:
  - chat_sessions: Lưu trữ phiên hội thoại
  - chat_messages: Lưu trữ tin nhắn từng phiên
  - feedback_fts_raw: Bảng ảo FTS5 có dấu
  - feedback_fts_nodau: Bảng ảo FTS5 không dấu
  - v_issues_current: View chuẩn hóa phản hồi kích hoạt
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from unidecode import unidecode

logger = logging.getLogger("dms-chat-migrations")


def apply_chat_migrations(conn: sqlite3.Connection) -> None:
    """Áp dụng các bảng và index cho Chatbot vào SQLite connection (Idempotent)."""
    with conn:
        # 1. Bảng chat_sessions
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_sessions (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                active_domain TEXT NOT NULL DEFAULT '',
                slots_json TEXT NOT NULL DEFAULT '{}',
                token_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_sessions_user ON chat_sessions(user_id, updated_at)"
        )

        # 2. Bảng chat_messages
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, created_at)"
        )

        # 3. FTS5 Virtual Tables
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS feedback_fts_raw USING fts5(
                feedback_id UNINDEXED,
                content,
                product,
                unit_name,
                source,
                tokenize='unicode61'
            )
            """
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS feedback_fts_nodau USING fts5(
                feedback_id UNINDEXED,
                content_nodau,
                product_nodau,
                unit_name,
                tokenize='unicode61'
            )
            """
        )

        # 4. Scoped View v_issues_current (nếu bảng feedback_records tồn tại)
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='feedback_records'"
        ).fetchone()

        if table_exists:
            conn.execute(
                """
                CREATE VIEW IF NOT EXISTS v_issues_current AS
                SELECT
                    feedback_id,
                    source_file_name,
                    source_row_number,
                    raw_data_json,
                    content,
                    normalized_content,
                    issue_code,
                    issue_date,
                    source,
                    unit_name,
                    business_status,
                    product,
                    product_line,
                    model,
                    sentiment,
                    brand,
                    bm25_score,
                    classification_state,
                    created_at,
                    updated_at
                FROM feedback_records
                WHERE is_active = 1
                """
            )


def sync_fts_index(conn: sqlite3.Connection, limit: int | None = None) -> int:
    """Đồng bộ dữ liệu từ feedback_records vào 2 bảng FTS5.

    Returns:
        Số lượng bản ghi đã đồng bộ.
    """
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='feedback_records'"
    ).fetchone()
    if not table_exists:
        logger.warning("Bảng feedback_records chưa tồn tại, bỏ qua sync FTS5.")
        return 0

    cursor = conn.cursor()
    query = """
        SELECT feedback_id, content, product, unit_name, source
        FROM feedback_records
        WHERE is_active = 1
    """
    if limit is not None:
        query += f" LIMIT {int(limit)}"

    rows = cursor.execute(query).fetchall()

    with conn:
        # Xóa các index cũ để rebuild sạch sẽ
        conn.execute("DELETE FROM feedback_fts_raw")
        conn.execute("DELETE FROM feedback_fts_nodau")

        raw_records: list[tuple[Any, ...]] = []
        nodau_records: list[tuple[Any, ...]] = []

        for row in rows:
            fid = row[0]
            content = str(row[1] or "")
            product = str(row[2] or "")
            unit = str(row[3] or "")
            source = str(row[4] or "")

            raw_records.append((fid, content, product, unit, source))
            nodau_records.append((
                fid,
                unidecode(content).lower(),
                unidecode(product).lower(),
                unit,
            ))

        conn.executemany(
            "INSERT INTO feedback_fts_raw(feedback_id, content, product, unit_name, source) VALUES (?, ?, ?, ?, ?)",
            raw_records,
        )
        conn.executemany(
            "INSERT INTO feedback_fts_nodau(feedback_id, content_nodau, product_nodau, unit_name) VALUES (?, ?, ?, ?)",
            nodau_records,
        )

    logger.info("Đã đồng bộ %d bản ghi vào FTS5 dual-index.", len(rows))
    return len(rows)
