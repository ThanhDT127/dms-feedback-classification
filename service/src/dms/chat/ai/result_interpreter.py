"""Adapter duy nhất đọc ``QueryResult`` của contract v1.0 (design b03 D8).

Executor v1.0 chưa có ``forbidden`` / ``error_code`` (review C01), nên phải đoán lỗi quyền
từ câu thông báo của ``ScopePolicy``. Mọi chỗ đoán nằm ở file này: khi contract lên v1.1,
chỉ sửa ``interpret`` chứ không đụng Orchestrator.
"""

from __future__ import annotations

import logging

from ..contract import QueryResult, QueryStatus
from .types import ErrorCode, Reason, StepState, StepStatus

logger = logging.getLogger("dms-chat-result")

# Dấu hiệu lỗi quyền, chép từ chữ của ScopePolicy/FunctionRegistry (nhánh anthanh).
# Dev A đổi câu chữ mà chưa thêm error_code thì test ghim chuỗi sẽ đỏ.
SCOPE_ERROR_MARKERS: tuple[str, ...] = (
    "không có quyền xem dữ liệu của đơn vị",
    "chưa được phân công đơn vị",
    "nằm ngoài phạm vi được phân quyền",
)
INVALID_PLAN_MARKERS: tuple[str, ...] = (
    "kế hoạch truy vấn không hợp lệ",
    "invalid plan",
)
# Lỗi SQLite của SQL Pattern 2 (executor v1.0 bọc thành "Lỗi thực thi: <sqlite message>").
SQL_ERROR_MARKERS: tuple[str, ...] = (
    "no such column",
    "no such table",
    "no such function",
    "syntax error",
    "ambiguous column",
    "misuse of aggregate",
    "wrong number of arguments",
    "incomplete input",
    'near "',
)
MAX_LOGGED_ERROR_CHARS = 500

_CODE_TO_REASON: dict[ErrorCode, Reason] = {
    ErrorCode.SCOPE_VIOLATION: Reason.UNAUTHORIZED_SCOPE,
    ErrorCode.PARAM_INVALID: Reason.INVALID_PLAN,
    ErrorCode.TIMEOUT: Reason.TIMEOUT,
    ErrorCode.INTERNAL: Reason.INTERNAL,
    ErrorCode.SQL_INVALID: Reason.SQL_GENERATION_FAILED,
}


class ResultInterpreter:
    def interpret(
        self, result: QueryResult, *, request_id: str = "", step: int = 0, sql_step: bool = False
    ) -> StepStatus:
        """``sql_step``: bước Pattern 2 — lỗi SQLite được phân loại ``SQL_INVALID`` để vòng sửa xử lý."""
        if result.status is QueryStatus.OK:
            return StepStatus(StepState.OK)
        if result.status is QueryStatus.NO_DATA:
            # Không có dữ liệu là câu trả lời hợp lệ, không phải lỗi.
            return StepStatus(StepState.NO_DATA)

        raw = result.error_message or ""
        lowered = raw.casefold()
        if any(marker in lowered for marker in SCOPE_ERROR_MARKERS):
            status = StepStatus(StepState.FORBIDDEN, ErrorCode.SCOPE_VIOLATION)
        elif any(marker in lowered for marker in INVALID_PLAN_MARKERS):
            status = StepStatus(StepState.ERROR, ErrorCode.PARAM_INVALID)
        elif sql_step and (
            getattr(result, "error_code", None) == "SQL_INVALID"
            or any(marker in lowered for marker in SQL_ERROR_MARKERS)
        ):
            status = StepStatus(StepState.ERROR, ErrorCode.SQL_INVALID)
        else:
            status = StepStatus(StepState.ERROR, ErrorCode.INTERNAL)

        # Nguyên văn lỗi chỉ được ghi log, không bao giờ tới người dùng.
        logger.warning(
            "executor_error",
            extra={
                "request_id": request_id,
                "step": step,
                "code": status.code.value if status.code else None,
                "error_message": raw[:MAX_LOGGED_ERROR_CHARS],
            },
        )
        return status

    @staticmethod
    def reason_for(status: StepStatus) -> Reason | None:
        """Lý do dùng để chọn template thông báo cho người dùng."""
        if status.code is None:
            return None
        return _CODE_TO_REASON.get(status.code)
