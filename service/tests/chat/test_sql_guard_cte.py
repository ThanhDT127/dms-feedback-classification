"""Tests for Milestone 2: CTE aliases, transform functions, measure alias fixes, and scoped views."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from analytics_support import seed_classified_records

from dms.analytics import FeedbackAnalyticsRepository
from dms.chat.ai.semantic_schema import ISSUES_VIEW, LABELS_VIEW
from dms.chat.ai.sql_generator import SqlGenerator
from dms.chat.contract import AnswerShape, QueryPattern, QueryPlan, UserScope
from dms.chat.db.migrations import apply_chat_migrations
from dms.chat.db.query_executor import SecureQueryExecutor
from dms.chat.db.scoped_views import create_scoped_views
from dms.chat.guardrails.sql_guard import (
    SqlErrorCode,
    SqlGuard,
    SqlGuardContext,
    SqlGuardError,
)

TV1 = "Truyền thống Vùng 1"
TV2 = "Truyền thống Vùng 2"
USER_SCOPE_TV1 = UserScope(username="user1", role="user", unit_ids=[TV1])
ADMIN_SCOPE = UserScope(username="admin", role="admin")


@pytest.fixture
def repo(tmp_path: Path) -> FeedbackAnalyticsRepository:
    db_path = tmp_path / "test_m2.db"
    repository = FeedbackAnalyticsRepository(db_path)
    seed_classified_records(
        repository,
        db_path=db_path,
        entries=[
            {
                "unit_name": TV1,
                "issue_code": "A-001",
                "issue_date": "2026-08-15",
                "product": "Đèn LED Bulb",
                "sentiment": "Tiêu cực",
                "source": "Zalo",
                "labels": ["Báo lỗi"],
                "content": "Đèn hỏng",
            },
            {
                "unit_name": TV2,
                "issue_code": "B-001",
                "issue_date": "2026-08-20",
                "product": "Đèn LED Tube",
                "sentiment": "Tích cực",
                "source": "Hotline",
                "labels": ["Báo CL tốt"],
                "content": "Chất lượng tốt",
            },
        ],
    )
    with repository._conn() as conn:
        apply_chat_migrations(conn)
    return repository


def test_cte_alias_allowed_in_sql_guard():
    guard = SqlGuard()
    ctx = SqlGuardContext(
        scope_units=(TV1,),
        valid_values={"products": ["Đèn LED Bulb", "Đèn LED Tube"]},
        dates=frozenset({"2026-08-01", "2026-08-31"}),
    )
    sql = (
        "WITH monthly_data AS ("
        "  SELECT product, COUNT(*) AS cnt FROM v_issues_current_scoped GROUP BY product"
        ") "
        "SELECT b.product, b.cnt FROM monthly_data AS b"
    )
    guarded = guard.check(sql, ctx)
    assert guarded is not None
    assert "monthly_data" in guarded.sql
    assert "b.product" in guarded.sql


def test_transform_functions_bypass_literal_check():
    guard = SqlGuard()
    ctx = SqlGuardContext(
        dates=frozenset({"2026-08-01", "2026-08-31"}),
        valid_values={"products": ["Đèn LED Bulb"]},
    )

    # STRFTIME format month string
    sql_strftime = (
        "SELECT product FROM v_issues_current_scoped "
        "WHERE STRFTIME('%Y-%m', issue_date) = '2026-08'"
    )
    guarded = guard.check(sql_strftime, ctx)
    assert guarded is not None

    # SUBSTR expression
    sql_substr = (
        "SELECT product FROM v_issues_current_scoped "
        "WHERE SUBSTR(product, 1, 3) = 'LED'"
    )
    guarded_substr = guard.check(sql_substr, ctx)
    assert guarded_substr is not None

    # COALESCE expression
    sql_coalesce = (
        "SELECT product FROM v_issues_current_scoped "
        "WHERE COALESCE(model, 'Khác') = 'Khác'"
    )
    guarded_coalesce = guard.check(sql_coalesce, ctx)
    assert guarded_coalesce is not None


def test_measure_aliases_not_falsely_rejected():
    guard = SqlGuard()
    ctx = SqlGuardContext()

    # ma_van_de, ty_le_van_de, tong_van_de are valid column aliases
    sql_valid_aliases = (
        "SELECT issue_code AS ma_van_de, COUNT(*) AS tong_van_de "
        "FROM v_issues_current_scoped GROUP BY issue_code"
    )
    guarded = guard.check(sql_valid_aliases, ctx)
    assert guarded is not None

    # so_van_de still strictly requires COUNT(DISTINCT issue_code)
    sql_bad_measure = "SELECT COUNT(*) AS so_van_de FROM v_issues_current_scoped"
    with pytest.raises(SqlGuardError) as exc_info:
        guard.check(sql_bad_measure, ctx)
    assert exc_info.value.code == SqlErrorCode.SQL_MEASURE_MISMATCH

    sql_good_measure = (
        "SELECT COUNT(DISTINCT issue_code) AS so_van_de FROM v_issues_current_scoped"
    )
    assert guard.check(sql_good_measure, ctx) is not None


def test_scoped_views_isolation_and_columns(repo: FeedbackAnalyticsRepository):
    with repo._conn() as conn:
        conn.row_factory = sqlite3.Row
        create_scoped_views(conn, USER_SCOPE_TV1)

        # v_issue_labels_scoped must have unit_name and issue_date
        labels_rows = conn.execute(
            f"SELECT feedback_id, issue_code, unit_name, issue_date, label FROM {LABELS_VIEW}"
        ).fetchall()
        assert len(labels_rows) == 1
        assert labels_rows[0]["unit_name"] == TV1
        assert labels_rows[0]["issue_date"] == "2026-08-15"
        assert labels_rows[0]["label"] == "Báo lỗi"

        # v_issues_current_scoped has raw_data_json
        issues_rows = conn.execute(
            f"SELECT raw_data_json FROM {ISSUES_VIEW} WHERE unit_name = '{TV1}'"
        ).fetchall()
        assert len(issues_rows) == 1
        assert "raw_data_json" in dict(issues_rows[0])


def test_query_executor_semantic_view_with_cte_and_scope(repo: FeedbackAnalyticsRepository):
    executor = SecureQueryExecutor(repo)

    # WITH query on semantic view with user scope
    plan = QueryPlan(
        pattern=QueryPattern.SEMANTIC_VIEW,
        answer_shape=AnswerShape.TABLE,
        original_query="Phân tích nhãn theo đơn vị",
        sql=(
            "WITH summary AS ("
            "  SELECT label, unit_name, issue_date FROM v_issue_labels_scoped"
            ") "
            "SELECT label, unit_name, issue_date FROM summary"
        ),
    )
    rows = executor._execute_semantic_view(plan, USER_SCOPE_TV1)
    assert len(rows) == 1
    assert rows[0]["unit_name"] == TV1
    assert rows[0]["label"] == "Báo lỗi"


def test_sql_generator_user_prompt_formats_valid_values():
    class DummyLLM:
        def generate_json(self, *args, **kwargs):
            return type("Result", (), {"text": '{"sql": "SELECT 1"}'})()

    generator = SqlGenerator(DummyLLM())
    prompt = generator.render_user_prompt(
        request={"measures": ["so_van_de"]},
        filters={"unit_name": "Vùng 1"},
        question="Số vấn đề vùng 1",
        valid_values={
            "sources": ["Zalo", "Hotline"],
            "statuses": ["Chờ xử lý", "Đã xử lý"],
        },
    )
    assert "GIÁ TRỊ DANH MỤC HỢP LỆ" in prompt
    assert "- sources: Zalo, Hotline" in prompt
    assert "- statuses: Chờ xử lý, Đã xử lý" in prompt
    assert "- sentiments:" in prompt
    assert "- labels:" in prompt
