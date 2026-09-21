"""Spec ``chat-sql-guard`` (b09 task 3.1–3.5)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from dms.chat.guardrails.sql_guard import (
    NON_REPAIRABLE,
    SqlErrorCode,
    SqlGuard,
    SqlGuardConfig,
    SqlGuardContext,
    SqlGuardError,
)

V = "v_issues_current_scoped"
TV1 = "Truyền thống Vùng 1"
ATTACKS = [
    json.loads(line)
    for line in (Path(__file__).resolve().parent / "golden" / "sql_attacks.jsonl")
    .read_text(encoding="utf-8")
    .splitlines()
    if line.strip()
]
CTX = SqlGuardContext(
    scope_units=(TV1,),
    valid_values={"sentiments": ["Tiêu cực", "Tích cực", "Trung lập"], "units": [TV1, "Nha Trang"]},
    dates=frozenset({"2026-08-01", "2026-08-31"}),
)


def code_of(sql: str, ctx: SqlGuardContext = CTX, guard: SqlGuard | None = None) -> SqlErrorCode:
    with pytest.raises(SqlGuardError) as caught:
        (guard or SqlGuard()).check(sql, ctx)
    return caught.value.code


# ── Bộ tấn công ──


def test_attack_set_is_large_and_includes_review_cases():
    assert len(ATTACKS) >= 40
    ids = {a["id"] for a in ATTACKS}
    assert {"r01_trailing_dash_comment", "r02_subquery_base_table"} <= ids


@pytest.mark.parametrize("attack", ATTACKS, ids=[a["id"] for a in ATTACKS])
def test_every_attack_is_blocked(attack):
    ctx = SqlGuardContext(scope_units=tuple(attack["scope_units"]))
    code = code_of(attack["sql"], ctx)
    if attack["expected_code"]:
        assert code.value == attack["expected_code"]


# ── Cấu trúc ──


def test_rebuilt_sql_is_sent_not_the_llm_string():
    raw = f"select   product ,  count(distinct issue_code)   as so_van_de\n from {V}\n group by product"
    guarded = SqlGuard().check(raw, CTX)
    assert guarded.sql == (
        f"SELECT product, COUNT(DISTINCT issue_code) AS so_van_de FROM {V} GROUP BY product LIMIT 200"
    )
    assert guarded.tables == (V,) and guarded.output_aliases == ("so_van_de",)


def test_multi_statement():
    assert code_of(f"SELECT 1; DELETE FROM {V}") is SqlErrorCode.SQL_MULTI_STATEMENT


def test_r01_comment():
    assert code_of(f"SELECT unit_name, content FROM {V} --") is SqlErrorCode.SQL_COMMENT


def test_recursive_cte():
    sql = "WITH RECURSIVE c(x) AS (SELECT 1) SELECT x FROM c"
    assert code_of(sql) is SqlErrorCode.SQL_TOO_COMPLEX


def test_cte_on_view_is_allowed_and_star_inside_cte_ok():
    sql = f"WITH t AS (SELECT * FROM {V} WHERE sentiment = 'Tiêu cực') SELECT product, COUNT(DISTINCT issue_code) AS so_van_de FROM t GROUP BY product"
    assert SqlGuard().check(sql, CTX).sql.startswith("WITH t AS")


def test_limits_configurable():
    guard = SqlGuard(SqlGuardConfig(max_joins=0, max_subquery_depth=1))
    join = f"SELECT COUNT(*) AS n FROM {V} i JOIN v_issue_labels_scoped l ON l.feedback_id = i.feedback_id"
    assert code_of(join, guard=guard) is SqlErrorCode.SQL_TOO_COMPLEX
    nested = f"SELECT (SELECT (SELECT COUNT(*) FROM {V})) AS n"
    assert code_of(nested, guard=guard) is SqlErrorCode.SQL_TOO_COMPLEX


# ── Whitelist ──


def test_r02_subquery_to_base_table_logs_security_block(caplog):
    with caplog.at_level(logging.WARNING, logger="dms-chat-sql-guard"):
        code = code_of(f"SELECT (SELECT group_concat(content) FROM feedback_records) FROM {V}")
    assert code is SqlErrorCode.SQL_TABLE_FORBIDDEN
    assert any(r.getMessage() == "sql_guard_security_block" for r in caplog.records)


def test_system_schema():
    assert code_of("SELECT name FROM sqlite_master") is SqlErrorCode.SQL_TABLE_FORBIDDEN


def test_forbidden_column():
    assert code_of(f"SELECT raw_data_json FROM {V}") is SqlErrorCode.SQL_COLUMN_FORBIDDEN


def test_unknown_column_is_repairable():
    with pytest.raises(SqlGuardError) as caught:
        SqlGuard().check(f"SELECT status, COUNT(*) AS n FROM {V} GROUP BY status", CTX)
    assert caught.value.code is SqlErrorCode.SQL_COLUMN_UNKNOWN and caught.value.repairable
    assert "status" in caught.value.detail


def test_function_whitelist():
    assert code_of(f"SELECT randomblob(10) AS x FROM {V}") is SqlErrorCode.SQL_FUNCTION_FORBIDDEN
    ok = (
        f"SELECT strftime('%Y-%m', issue_date) AS thang, ROUND(AVG(julianday(issue_date)), 1) AS ngay_tb, "
        f"COALESCE(product, 'khác') AS san_pham, COUNT(DISTINCT issue_code) AS so_van_de FROM {V} "
        "GROUP BY thang, san_pham"
    )
    assert SqlGuard().check(ok, CTX)


def test_qualified_unknown_column():
    sql = f"SELECT i.label FROM {V} i"
    assert code_of(sql) is SqlErrorCode.SQL_COLUMN_UNKNOWN


# ── Literal và phạm vi ──


def test_unit_out_of_scope_is_not_repairable():
    with pytest.raises(SqlGuardError) as caught:
        SqlGuard().check(f"SELECT COUNT(*) AS n FROM {V} WHERE unit_name = 'Nha Trang'", CTX)
    assert caught.value.code is SqlErrorCode.SQL_UNIT_OUT_OF_SCOPE
    assert caught.value.code in NON_REPAIRABLE


def test_admin_scope_allows_any_known_unit():
    admin = SqlGuardContext(scope_units=None, valid_values=CTX.valid_values, dates=CTX.dates)
    assert SqlGuard().check(f"SELECT COUNT(*) AS n FROM {V} WHERE unit_name = 'Nha Trang'", admin)


def test_unknown_categorical_value():
    sql = f"SELECT COUNT(*) AS n FROM {V} WHERE sentiment = 'Tiêu cực nặng'"
    assert code_of(sql) is SqlErrorCode.SQL_LITERAL_UNKNOWN_VALUE


def test_self_computed_date():
    sql = f"SELECT COUNT(*) AS n FROM {V} WHERE issue_date >= '2026-07-01'"
    assert code_of(sql) is SqlErrorCode.SQL_DATE_MISMATCH
    ok = f"SELECT COUNT(*) AS n FROM {V} WHERE issue_date BETWEEN '2026-08-01' AND '2026-08-31'"
    assert SqlGuard().check(ok, CTX)


def test_like_only_on_product_columns():
    assert (
        code_of(f"SELECT COUNT(*) AS n FROM {V} WHERE unit_name LIKE '%Vùng%'")
        is SqlErrorCode.SQL_LIKE_NOT_ALLOWED
    )
    assert SqlGuard().check(f"SELECT COUNT(*) AS n FROM {V} WHERE product LIKE '%LED%'", CTX)


# ── Độ đo, alias, LIMIT ──


def test_measure_mismatch():
    assert code_of(f"SELECT COUNT(*) AS so_van_de FROM {V}") is SqlErrorCode.SQL_MEASURE_MISMATCH


def test_alias_must_be_lower_ascii():
    assert code_of(f'SELECT COUNT(*) AS "Số lượng" FROM {V}') is SqlErrorCode.SQL_ALIAS_INVALID


@pytest.mark.parametrize(
    ("sql_limit", "expected"), [("", 200), (" LIMIT 5", 5), (" LIMIT 5000", 200)]
)
def test_limit_added_or_capped(sql_limit, expected):
    guarded = SqlGuard().check(f"SELECT product FROM {V}{sql_limit}", CTX)
    assert guarded.limit == expected and guarded.sql.endswith(f"LIMIT {expected}")


def test_json_extract_only_when_enabled():
    sql = f"SELECT json_extract(raw_data_json, '$.\"Tỉnh\"') AS tinh, COUNT(*) AS n FROM {V} GROUP BY tinh"
    assert code_of(sql) is SqlErrorCode.SQL_COLUMN_FORBIDDEN
    enabled = SqlGuard(SqlGuardConfig(json_extract_enabled=True))
    guarded = enabled.check(sql, SqlGuardContext(scope_units=None, raw_keys=frozenset({"Tỉnh"})))
    assert "JSON_EXTRACT(raw_data_json, '$.\"Tỉnh\"')" in guarded.sql and "->" not in guarded.sql
    unknown_key = sql.replace("Tỉnh", "Mật khẩu")
    ctx = SqlGuardContext(scope_units=None, raw_keys=frozenset({"Tỉnh"}))
    assert code_of(unknown_key, ctx, enabled) is SqlErrorCode.SQL_LITERAL_UNKNOWN_VALUE
    raw_column = f"SELECT raw_data_json FROM {V}"
    assert code_of(raw_column, ctx, enabled) is SqlErrorCode.SQL_COLUMN_FORBIDDEN
