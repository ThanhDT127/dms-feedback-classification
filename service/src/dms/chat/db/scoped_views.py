"""Scoped Views and query builder helpers for Pattern 2 (Khối [4] DATA)."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..ai.semantic_schema import ISSUES_VIEW, LABELS_VIEW, VIEWS
from ..contract import UserScope

V_ISSUES_CURRENT = "v_issues_current"
V_FEEDBACK_LABELS = "feedback_labels"


class ScopedViewError(RuntimeError):
    """Không dựng được view đã áp phạm vi (thường do thiếu bảng nguồn)."""


def _quote(value: str) -> str:
    """Literal chuỗi cho SQLite: CREATE VIEW không nhận tham số ``?``."""
    return "'" + str(value).replace("'", "''") + "'"


def _scope_where(scope: UserScope, column: str = "unit_name") -> str:
    if scope.is_admin:
        return ""
    if not scope.unit_ids:
        return " WHERE 1 = 0"
    units = ", ".join(_quote(unit) for unit in scope.unit_ids)
    return f" WHERE {column} IN ({units})"


def create_scoped_views(conn: sqlite3.Connection, scope: UserScope) -> None:
    """Dựng TEMP VIEW đã áp phạm vi cho Pattern 2 (design b09 D5).

    TEMP VIEW sống theo từng connection, và ``FeedbackAnalyticsRepository._conn()`` mở connection
    mới mỗi lần, nên gọi ngay trước khi chạy SQL là đủ và không đụng phiên của người khác.

    Cột lấy từ ``semantic_schema.VIEWS`` — nguồn chuẩn mà prompt sinh SQL và SQL Guard cùng đọc.
    Nhờ vậy cột bị cấm (``content``, ``raw_data_json``…) không lọt ra view, thay vì chỉ trông chờ
    vào Guard chặn ở tầng câu lệnh.
    """
    source_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('view', 'table') AND name = ?",
        (V_ISSUES_CURRENT,),
    ).fetchone()
    if not source_exists:
        raise ScopedViewError(
            f"Thiếu {V_ISSUES_CURRENT}; chạy apply_chat_migrations trên DB có feedback_records."
        )

    issue_columns = ", ".join(VIEWS[ISSUES_VIEW].columns)
    conn.execute(f"DROP VIEW IF EXISTS temp.{LABELS_VIEW}")
    conn.execute(f"DROP VIEW IF EXISTS temp.{ISSUES_VIEW}")
    conn.execute(
        f"CREATE TEMP VIEW {ISSUES_VIEW} AS "
        f"SELECT {issue_columns} FROM main.{V_ISSUES_CURRENT}{_scope_where(scope)}"
    )
    # Nhãn thừa hưởng phạm vi qua phép nối: dòng ngoài phạm vi không có bên phải nên tự rụng.
    conn.execute(
        f"CREATE TEMP VIEW {LABELS_VIEW} AS "
        f"SELECT l.feedback_id, s.issue_code, l.label, l.major_group "
        f"FROM main.{V_FEEDBACK_LABELS} l JOIN {ISSUES_VIEW} s ON s.feedback_id = l.feedback_id"
    )


def build_view_query(
    view_name: str,
    scope: UserScope,
    *,
    columns: list[str] | None = None,
    where_clauses: list[str] | None = None,
    params: list[Any] | None = None,
    order_by: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> tuple[str, list[Any]]:
    """Xây dựng câu lệnh SQL an toàn trên View có tự động ép quyền đơn vị."""
    selected_cols = ", ".join(columns) if columns else "*"
    conditions: list[str] = list(where_clauses or [])
    query_params: list[Any] = list(params or [])

    # Cưỡng chế phân quyền theo đơn vị
    if not scope.is_admin:
        if not scope.unit_ids:
            conditions.append("1 = 0")
        else:
            placeholders = ", ".join("?" for _ in scope.unit_ids)
            conditions.append(f"unit_name IN ({placeholders})")
            query_params.extend(scope.unit_ids)

    where_stmt = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    order_stmt = f" ORDER BY {order_by}" if order_by else ""
    limit_stmt = f" LIMIT {int(limit)}" if limit is not None else ""
    offset_stmt = f" OFFSET {int(offset)}" if offset is not None else ""

    sql = (
        f"SELECT {selected_cols} FROM {view_name}{where_stmt}{order_stmt}{limit_stmt}{offset_stmt}"
    )
    return sql, query_params
