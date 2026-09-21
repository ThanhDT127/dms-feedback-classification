from __future__ import annotations

import logging

import pytest

from dms.chat.ai.result_interpreter import MAX_LOGGED_ERROR_CHARS, ResultInterpreter
from dms.chat.ai.types import ErrorCode, Reason, StepState
from dms.chat.contract import QueryResult

from .ai_fakes import FORBIDDEN_UNIT_MESSAGE, NO_UNIT_MESSAGE


@pytest.fixture
def interpreter() -> ResultInterpreter:
    return ResultInterpreter()


def test_ok_result(interpreter):
    status = interpreter.interpret(QueryResult.ok([{"issue_count": 3}]))
    assert status.state is StepState.OK
    assert status.ok is True
    assert status.code is None


def test_no_data_is_not_an_error(interpreter):
    status = interpreter.interpret(QueryResult.no_data())
    assert status.state is StepState.NO_DATA
    assert status.code is None


# Chuỗi ghim theo câu chữ của ScopePolicy/SecureQueryExecutor (nhánh anthanh).
@pytest.mark.parametrize(
    "message",
    [
        FORBIDDEN_UNIT_MESSAGE.format(unit="Nha Trang", allowed="Truyền thống Vùng 1"),
        NO_UNIT_MESSAGE.format(username="nv01"),
        "Bạn không có quyền xem dữ liệu của đơn vị 'Biên Hòa'.",
        "Đơn vị yêu cầu nằm ngoài phạm vi được phân quyền của tài khoản.",
    ],
)
def test_scope_errors_become_forbidden(interpreter, message):
    status = interpreter.interpret(QueryResult.error(message))
    assert status.state is StepState.FORBIDDEN
    assert status.code is ErrorCode.SCOPE_VIOLATION
    assert ResultInterpreter.reason_for(status) is Reason.UNAUTHORIZED_SCOPE


@pytest.mark.parametrize(
    "message",
    [
        "Kế hoạch truy vấn không hợp lệ: Pattern 'sql_template' bắt buộc có 'function_name'",
        "Invalid plan: missing function_name",
    ],
)
def test_invalid_plan_errors_are_param_invalid(interpreter, message):
    status = interpreter.interpret(QueryResult.error(message))
    assert status.state is StepState.ERROR
    assert status.code is ErrorCode.PARAM_INVALID
    assert ResultInterpreter.reason_for(status) is Reason.INVALID_PLAN


def test_other_errors_are_internal(interpreter):
    status = interpreter.interpret(QueryResult.error("Lỗi thực thi: near GROUP: syntax error"))
    assert status.state is StepState.ERROR
    assert status.code is ErrorCode.INTERNAL
    assert ResultInterpreter.reason_for(status) is Reason.INTERNAL


def test_raw_error_never_leaves_the_status_object(interpreter):
    raw = "Lỗi thực thi: near GROUP: syntax error"
    status = interpreter.interpret(QueryResult.error(raw))
    assert "syntax error" not in str(status.to_dict())


def test_raw_error_is_logged_but_truncated(interpreter, caplog):
    raw = "Lỗi thực thi: " + "x" * 2000
    with caplog.at_level(logging.WARNING, logger="dms-chat-result"):
        interpreter.interpret(QueryResult.error(raw), request_id="req-9", step=1)
    record = next(r for r in caplog.records if r.message == "executor_error")
    assert record.request_id == "req-9"
    assert len(record.error_message) == MAX_LOGGED_ERROR_CHARS


def test_sql_errors_of_semantic_steps_are_sql_invalid(interpreter):
    status = interpreter.interpret(
        QueryResult.error("Lỗi thực thi: no such column: status"), sql_step=True
    )
    assert status.code is ErrorCode.SQL_INVALID
    assert ResultInterpreter.reason_for(status) is Reason.SQL_GENERATION_FAILED
    # Cùng thông báo ở bước Pattern 1 vẫn là lỗi nội bộ.
    assert (
        interpreter.interpret(QueryResult.error("Lỗi thực thi: no such column: status")).code
        is ErrorCode.INTERNAL
    )
