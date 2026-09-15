"""Scoped Views and query builder helpers for Pattern 2 (Khối [4] DATA)."""

from __future__ import annotations

from typing import Any

from ..contract import UserScope

V_ISSUES_CURRENT = "v_issues_current"


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

    sql = f"SELECT {selected_cols} FROM {view_name}{where_stmt}{order_stmt}{limit_stmt}{offset_stmt}"
    return sql, query_params
