"""SecureQueryExecutor — Khối [4] DATA (Production Implementation).

Thực thi 4 pattern truy vấn với cơ chế bảo vệ phân quyền chặt chẽ:
  1. Pattern 1: SQL Template (Function Registry)
  2. Pattern 2: Semantic View (SELECT an toàn + WHERE scope injection)
  3. Pattern 3: FTS5 Search (Tìm kiếm toàn văn tiếng Việt có dấu / không dấu)
  4. Pattern 4: JSON Extract (Trích xuất dynamic keys từ raw_data_json)
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from typing import Any

from unidecode import unidecode

from ...analytics import FeedbackAnalyticsRepository
from ..auth.scope_policy import ScopePolicy
from ..contract import (
    QueryPattern,
    QueryPlan,
    QueryResult,
    QueryResultMetadata,
    QueryStatus,
    UserScope,
)
from .function_registry import FunctionRegistry

logger = logging.getLogger("dms-chat-query-executor")

_FORBIDDEN_SQL_PATTERNS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|PRAGMA|ATTACH|DETACH|VACUUM|TRUNCATE|EXEC|EXECUTE)\b",
    re.IGNORECASE,
)


def _sanitize_fts_query(query: str) -> str:
    """Loại bỏ ký tự đặc biệt có thể làm lỗi cú pháp truy vấn FTS5 SQLite."""
    # Bỏ các ký tự cú pháp SQLite FTS5 nhạy cảm: *, ^, :, (, ), "
    cleaned = re.sub(r'[\*\^\:\(\)\"]+', " ", query).strip()
    words = [w for w in cleaned.split() if w]
    if not words:
        return ""
    # Tìm kiếm theo cụm AND mặc định
    return " ".join(words)


class SecureQueryExecutor:
    """Production-grade secure query executor enforcing data isolation."""

    def __init__(
        self,
        repository: FeedbackAnalyticsRepository,
        function_registry: FunctionRegistry | None = None,
    ) -> None:
        self.repository = repository
        self.function_registry = function_registry or FunctionRegistry(repository)

    def execute(self, plan: QueryPlan, scope: UserScope) -> QueryResult:
        """Thực thi QueryPlan theo UserScope đã được xác thực."""
        start = time.monotonic()

        # 1. Kiểm tra validation của plan
        validation_errors = plan.validate()
        if validation_errors:
            return QueryResult.error(f"Kế hoạch truy vấn không hợp lệ: {'; '.join(validation_errors)}")

        try:
            # 2. Điều phối theo pattern
            if plan.pattern == QueryPattern.SQL_TEMPLATE:
                data = self._execute_sql_template(plan, scope)
            elif plan.pattern == QueryPattern.SEMANTIC_VIEW:
                data = self._execute_semantic_view(plan, scope)
            elif plan.pattern == QueryPattern.FTS5_SEARCH:
                data = self._execute_fts5_search(plan, scope)
            elif plan.pattern == QueryPattern.JSON_EXTRACT:
                data = self._execute_json_extract(plan, scope)
            else:
                return QueryResult.error(f"Pattern không hỗ trợ: {plan.pattern}")

        except PermissionError as perm_err:
            logger.warning("Cảnh báo vi phạm quyền: %s", perm_err)
            return QueryResult.error(str(perm_err))
        except Exception as exc:
            logger.exception("Lỗi thực thi truy vấn: %s", exc)
            return QueryResult.error(f"Lỗi thực thi: {exc}")

        elapsed_ms = int((time.monotonic() - start) * 1000)

        if not data:
            res = QueryResult.no_data()
            res.metadata = QueryResultMetadata(
                total_rows=0,
                query_time_ms=elapsed_ms,
                scope_applied=scope.scope_description,
                pattern_used=plan.pattern.value,
            )
            return res

        return QueryResult(
            status=QueryStatus.OK,
            data=data,
            metadata=QueryResultMetadata(
                total_rows=len(data),
                query_time_ms=elapsed_ms,
                scope_applied=scope.scope_description,
                cache_hit=False,
                pattern_used=plan.pattern.value,
            ),
        )

    # ═══════════════════════════════════════════════════════════════════
    # PATTERN IMPLEMENTATIONS
    # ═══════════════════════════════════════════════════════════════════

    def _execute_sql_template(
        self, plan: QueryPlan, scope: UserScope
    ) -> list[dict[str, Any]]:
        """Pattern 1: Chạy hàm SQL mẫu qua FunctionRegistry."""
        return self.function_registry.execute(
            plan.function_name or "", plan.params, scope
        )

    def _execute_semantic_view(
        self, plan: QueryPlan, scope: UserScope
    ) -> list[dict[str, Any]]:
        """Pattern 2: Chạy SQL an toàn trên Semantic View."""
        raw_sql = str(plan.sql or "").strip()

        # Kiểm tra chỉ cho phép câu lệnh SELECT
        if not raw_sql.lower().startswith("select"):
            raise PermissionError("Chỉ cho phép câu lệnh truy vấn đọc (SELECT).")

        if _FORBIDDEN_SQL_PATTERNS.search(raw_sql):
            raise PermissionError("Câu lệnh chứa từ khóa bị cấm sửa đổi cơ sở dữ liệu.")

        # Ép điều kiện phân quyền đơn vị
        scoped_sql = ScopePolicy.enforce_sql_where(raw_sql, scope, unit_column="unit_name")

        with self.repository._conn() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            rows = cursor.execute(scoped_sql).fetchall()
            return [dict(r) for r in rows]

    def _execute_fts5_search(
        self, plan: QueryPlan, scope: UserScope
    ) -> list[dict[str, Any]]:
        """Pattern 3: Tìm kiếm toàn văn FTS5 dual-index tiếng Việt."""
        raw_query = str(plan.fts_query or "").strip()
        sanitized_raw = _sanitize_fts_query(raw_query)
        if not sanitized_raw:
            return []

        limit = min(max(1, plan.fts_limit), 100)
        sanitized_nodau = unidecode(sanitized_raw).lower().strip()

        with self.repository._conn() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Thử tìm kiếm có dấu trước
            base_sql = """
                SELECT r.feedback_id, r.issue_code, r.content, r.product,
                       r.unit_name, r.source, r.issue_date, r.sentiment,
                       r.business_status, r.raw_data_json
                FROM feedback_fts_raw f
                JOIN feedback_records r ON r.feedback_id = f.feedback_id
                WHERE f.feedback_fts_raw MATCH ? AND r.is_active = 1
            """
            scoped_sql = ScopePolicy.enforce_sql_where(base_sql, scope, unit_column="unit_name")
            scoped_sql += f" LIMIT {limit}"

            rows = cursor.execute(scoped_sql, (sanitized_raw,)).fetchall()

            # Nếu không tìm thấy, fallback sang tìm kiếm không dấu
            if not rows and sanitized_nodau:
                fallback_sql = """
                    SELECT r.feedback_id, r.issue_code, r.content, r.product,
                           r.unit_name, r.source, r.issue_date, r.sentiment,
                           r.business_status, r.raw_data_json
                    FROM feedback_fts_nodau f
                    JOIN feedback_records r ON r.feedback_id = f.feedback_id
                    WHERE f.feedback_fts_nodau MATCH ? AND r.is_active = 1
                """
                scoped_fallback_sql = ScopePolicy.enforce_sql_where(
                    fallback_sql, scope, unit_column="unit_name"
                )
                scoped_fallback_sql += f" LIMIT {limit}"
                rows = cursor.execute(scoped_fallback_sql, (sanitized_nodau,)).fetchall()

            return [dict(r) for r in rows]

    def _execute_json_extract(
        self, plan: QueryPlan, scope: UserScope
    ) -> list[dict[str, Any]]:
        """Pattern 4: Trích xuất dynamic key từ raw_data_json của một bản ghi."""
        filters = plan.json_filters or {}
        feedback_id = filters.get("feedback_id")
        issue_code = filters.get("issue_code")

        if not feedback_id and not issue_code:
            raise ValueError("Pattern 'json_extract' yêu cầu 'feedback_id' hoặc 'issue_code' trong json_filters.")

        with self.repository._conn() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            if feedback_id:
                row = cursor.execute(
                    "SELECT feedback_id, unit_name, raw_data_json FROM feedback_records WHERE feedback_id = ? AND is_active = 1",
                    (feedback_id,),
                ).fetchone()
            else:
                row = cursor.execute(
                    "SELECT feedback_id, unit_name, raw_data_json FROM feedback_records WHERE issue_code = ? AND is_active = 1",
                    (str(issue_code).strip(),),
                ).fetchone()

            if not row:
                return []

            # Kiểm tra quyền xem bản ghi này
            if not scope.is_admin:
                record_unit = str(row["unit_name"] or "").strip()
                if record_unit not in scope.unit_ids:
                    raise PermissionError(
                        f"Bản ghi thuộc đơn vị '{record_unit}' nằm ngoài phạm vi được phân quyền của bạn."
                    )

            try:
                raw_data = json.loads(row["raw_data_json"] or "{}")
            except Exception:
                raw_data = {}

            # Trích xuất các keys yêu cầu
            if plan.json_keys:
                extracted = {k: raw_data.get(k) for k in plan.json_keys}
            else:
                extracted = raw_data

            extracted["feedback_id"] = row["feedback_id"]
            extracted["unit_name"] = row["unit_name"]
            return [extracted]
