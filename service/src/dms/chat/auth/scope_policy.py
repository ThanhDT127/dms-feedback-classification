"""ScopePolicy — Data Scoping Guard (Khối [2] AUTHEN & Khối [4] DATA).

Đảm bảo nguyên tắc bảo mật:
  - User role `user` TUYỆT ĐỐI không thể xem dữ liệu ngoài danh sách `unit_ids` được cấp.
  - LLM (Khối [5]) KHÔNG THỂ nhìn thấy hay sửa đổi điều kiện lọc phân quyền.
  - Mọi query parameter hoặc câu lệnh SQL đều được ép điều kiện an toàn tại tầng máy chủ.
"""

from __future__ import annotations

import logging
from typing import Any

from ..contract import UserScope

logger = logging.getLogger("dms-chat-scope")


class ScopePolicy:
    """Security policy engine enforcing unit-level data scoping."""

    @staticmethod
    def extract_scope(user: dict[str, Any] | None) -> UserScope:
        """Trích xuất UserScope từ user dict (từ get_current_user / JWT)."""
        if not user:
            return UserScope(
                username="anonymous",
                role="user",
                display_name="Khách",
                unit_ids=[],
            )

        username = str(user.get("username") or "").strip()
        role = str(user.get("role") or "user").strip().lower()
        display_name = str(user.get("display_name") or username).strip()

        # Admin có toàn quyền, không bị giới hạn đơn vị
        if role == "admin":
            return UserScope(
                username=username,
                role="admin",
                display_name=display_name,
                unit_ids=[],
            )

        raw_unit_ids = user.get("unit_ids") or []
        if isinstance(raw_unit_ids, str):
            raw_unit_ids = [raw_unit_ids]

        cleaned_units = [
            str(u).strip() for u in raw_unit_ids if str(u).strip()
        ]

        return UserScope(
            username=username,
            role="user",
            display_name=display_name,
            unit_ids=cleaned_units,
        )

    @classmethod
    def enforce_scope_on_params(
        cls, params: dict[str, Any], scope: UserScope
    ) -> dict[str, Any]:
        """Ép ràng buộc đơn vị vào dictionary tham số của truy vấn.

        Raises:
            PermissionError: Khi user thường cố tình truy vấn đơn vị ngoài phạm vi được cấp.
        """
        scoped_params = dict(params)

        if scope.is_admin:
            return scoped_params

        # Nếu user không được gán bất kỳ đơn vị nào
        if not scope.unit_ids:
            raise PermissionError(
                f"Tài khoản '{scope.username}' chưa được phân công đơn vị nào để tra cứu dữ liệu."
            )

        requested_unit = scoped_params.get("unit_name")
        allowed_lookup = {u.casefold(): u for u in scope.unit_ids}

        if requested_unit:
            clean_requested = str(requested_unit).strip()
            matched_canonical = allowed_lookup.get(clean_requested.casefold())
            if matched_canonical is None:
                raise PermissionError(
                    f"Bạn không có quyền xem dữ liệu của đơn vị '{clean_requested}'. "
                    f"Các đơn vị được phép: {', '.join(scope.unit_ids)}"
                )
            # Chuẩn hóa về tên đơn vị chính thức trong scope
            scoped_params["unit_name"] = matched_canonical
        else:
            # Nếu user có đúng 1 đơn vị, tự động gán unit_name
            if len(scope.unit_ids) == 1:
                scoped_params["unit_name"] = scope.unit_ids[0]
            else:
                # Nếu có nhiều đơn vị, để None ở unit_name nhưng thêm _allowed_unit_ids
                scoped_params["_allowed_unit_ids"] = list(scope.unit_ids)

        return scoped_params

    @classmethod
    def enforce_sql_where(
        cls, sql: str, scope: UserScope, unit_column: str = "unit_name"
    ) -> str:
        """Chèn điều kiện lọc đơn vị an toàn vào câu lệnh SQL SELECT.

        Nếu câu lệnh đã có WHERE: chèn ({condition}) AND ...
        Nếu chưa có WHERE: chèn trước GROUP BY / HAVING / ORDER BY / LIMIT.
        """
        clean_sql = sql.strip().rstrip(";")

        if scope.is_admin:
            return clean_sql

        if not scope.unit_ids:
            condition = "1 = 0"
        else:
            quote = "'"
            escaped_units = ", ".join(
                quote + u.replace("'", "''") + quote for u in scope.unit_ids
            )
            condition = f"{unit_column} IN ({escaped_units})"

        # Tìm vị trí WHERE
        import re

        match_where = re.search(r"\bWHERE\b", clean_sql, re.IGNORECASE)
        if match_where:
            pos = match_where.end()
            return f"{clean_sql[:pos]} ({condition}) AND ({clean_sql[pos:].strip()})"

        # Nếu không có WHERE, tìm vị trí trước GROUP BY / HAVING / ORDER BY / LIMIT / WINDOW
        clause_split = re.compile(
            r"\b(GROUP\s+BY|HAVING|ORDER\s+BY|LIMIT|WINDOW)\b", re.IGNORECASE
        )
        match_clause = clause_split.search(clean_sql)
        if match_clause:
            pos = match_clause.start()
            return f"{clean_sql[:pos].rstrip()} WHERE {condition} {clean_sql[pos:]}"

        return f"{clean_sql} WHERE {condition}"

    @classmethod
    def filter_rows_by_scope(
        cls,
        rows: list[dict[str, Any]],
        scope: UserScope,
        unit_key: str = "unit_name",
    ) -> list[dict[str, Any]]:
        """Lọc danh sách dict theo scope trong bộ nhớ (hậu xử lý an toàn)."""
        if scope.is_admin:
            return rows

        if not scope.unit_ids:
            return []

        allowed_set = {u.casefold() for u in scope.unit_ids}
        return [
            row
            for row in rows
            if str(row.get(unit_key) or "").strip().casefold() in allowed_set
        ]
