"""Unit tests for Chatbot database migrations, FTS5 indexes, and scoped views."""

from __future__ import annotations

import sqlite3

from dms.chat.contract import UserScope
from dms.chat.db.migrations import apply_chat_migrations, sync_fts_index
from dms.chat.db.scoped_views import build_view_query


def _create_sample_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    # Giả lập bảng feedback_records tối giản
    conn.execute(
        """
        CREATE TABLE feedback_records (
            feedback_id INTEGER PRIMARY KEY,
            source_file_name TEXT,
            source_row_number INTEGER,
            raw_data_json TEXT,
            content TEXT,
            normalized_content TEXT,
            issue_code TEXT,
            issue_date TEXT,
            source TEXT,
            unit_name TEXT,
            business_status TEXT,
            product TEXT,
            product_line TEXT,
            model TEXT,
            sentiment TEXT,
            brand TEXT,
            bm25_score REAL,
            classification_state TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    # Thêm dữ liệu mẫu
    conn.execute(
        """
        INSERT INTO feedback_records (
            feedback_id, content, product, unit_name, source, is_active
        ) VALUES
        (1, 'Đèn LED AT04 bị cháy sau 1 tháng', 'Đèn LED AT04', 'CN Miền Nam', 'Hotline', 1),
        (2, 'Ống nhựa PPR chất lượng rất tốt', 'Ống nhựa PPR', 'CN Miền Bắc', 'Zalo', 1),
        (3, 'Đèn năng lượng mặt trời bị hỏng pin', 'Đèn NLMT', 'CN Miền Nam', 'DMS', 0)
        """
    )
    conn.commit()
    return conn


def test_migrations_create_tables_and_view():
    conn = _create_sample_db()
    apply_chat_migrations(conn)

    # Kiểm tra các bảng đã được tạo
    cursor = conn.cursor()
    tables = {
        row[0]
        for row in cursor.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
    }

    assert "chat_sessions" in tables
    assert "chat_messages" in tables
    assert "feedback_fts_raw" in tables
    assert "feedback_fts_nodau" in tables
    assert "v_issues_current" in tables

    # Kiểm tra view chỉ trả về bản ghi is_active = 1
    view_rows = cursor.execute("SELECT feedback_id FROM v_issues_current").fetchall()
    assert len(view_rows) == 2
    assert {r[0] for r in view_rows} == {1, 2}


def test_sync_fts_index_dual_search():
    conn = _create_sample_db()
    apply_chat_migrations(conn)

    synced_count = sync_fts_index(conn)
    assert synced_count == 2

    cursor = conn.cursor()

    # Tìm kiếm có dấu trên bảng raw
    raw_res = cursor.execute(
        "SELECT feedback_id FROM feedback_fts_raw WHERE feedback_fts_raw MATCH 'cháy'"
    ).fetchall()
    assert len(raw_res) == 1
    assert raw_res[0][0] == 1

    # Tìm kiếm KHÔNG DẤU trên bảng nodau (gõ "chay" vẫn tìm thấy "cháy")
    nodau_res = cursor.execute(
        "SELECT feedback_id FROM feedback_fts_nodau WHERE feedback_fts_nodau MATCH 'chay'"
    ).fetchall()
    assert len(nodau_res) == 1
    assert nodau_res[0][0] == 1

    # Tìm kiếm sản phẩm không dấu
    nodau_prod = cursor.execute(
        "SELECT feedback_id FROM feedback_fts_nodau WHERE feedback_fts_nodau MATCH 'ong nhua'"
    ).fetchall()
    assert len(nodau_prod) == 1
    assert nodau_prod[0][0] == 2


def test_build_view_query_scope_enforcement():
    user_scope = UserScope(username="u1", role="user", unit_ids=["CN Miền Nam"])
    sql, params = build_view_query(
        "v_issues_current",
        user_scope,
        where_clauses=["sentiment = ?"],
        params=["Tiêu cực"],
        order_by="feedback_id DESC",
        limit=10,
    )

    assert "WHERE sentiment = ? AND unit_name IN (?)" in sql
    assert params == ["Tiêu cực", "CN Miền Nam"]
    assert "LIMIT 10" in sql
