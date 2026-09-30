"""View đã áp phạm vi phải tồn tại trên DB thật, không chỉ trong fixture của test (b09 D5).

Bộ test Pattern 2 cũ tự dựng TEMP VIEW trong ``sql_fixture`` nên không phát hiện được việc
production chưa hề tạo hai view này — câu hỏi text2sql nào cũng chết bằng
``no such table: v_issues_current_scoped``.
"""

from __future__ import annotations

import sqlite3

import pytest
from analytics_support import seed_classified_records

from dms.analytics import FeedbackAnalyticsRepository
from dms.chat.ai.semantic_schema import ISSUES_VIEW, LABELS_VIEW, VIEWS
from dms.chat.contract import AnswerShape, QueryPattern, QueryPlan, UserScope
from dms.chat.db.migrations import apply_chat_migrations
from dms.chat.db.query_executor import SecureQueryExecutor
from dms.chat.db.scoped_views import ScopedViewError, create_scoped_views

TV1, TV2 = "Truyền thống Vùng 1", "Truyền thống Vùng 2"
ADMIN = UserScope(username="admin", role="admin")
NV_TV1 = UserScope(username="nv", role="user", unit_ids=[TV1])


@pytest.fixture
def repo(tmp_path):
    db_path = tmp_path / "jobs.db"
    repository = FeedbackAnalyticsRepository(db_path)
    seed_classified_records(
        repository,
        db_path=db_path,
        entries=[
            {"unit_name": TV1, "issue_code": "A-001", "product": "Bulb", "labels": ["Báo lỗi"]},
            {"unit_name": TV1, "issue_code": "A-002", "product": "Bulb", "labels": ["Báo lỗi"]},
            {
                "unit_name": TV2,
                "issue_code": "B-001",
                "product": "Downlight",
                "labels": ["Báo lỗi"],
            },
        ],
    )
    with repository._conn() as conn:
        apply_chat_migrations(conn)
    return repository


def _views(conn) -> set[str]:
    return {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'view'")
    } | {row[0] for row in conn.execute("SELECT name FROM temp.sqlite_master WHERE type = 'view'")}


def test_both_views_are_created(repo):
    with repo._conn() as conn:
        create_scoped_views(conn, ADMIN)
        assert {ISSUES_VIEW, LABELS_VIEW} <= _views(conn)


def test_columns_match_the_declared_schema(repo):
    """Cột của view thật phải khớp semantic_schema — prompt và SQL Guard cùng đọc bảng đó."""
    with repo._conn() as conn:
        create_scoped_views(conn, ADMIN)
        for view in (ISSUES_VIEW, LABELS_VIEW):
            actual = {row[1] for row in conn.execute(f"PRAGMA table_info({view})")}
            assert actual == set(VIEWS[view].columns), view


def test_forbidden_columns_never_reach_the_view(repo):
    with repo._conn() as conn:
        create_scoped_views(conn, ADMIN)
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({ISSUES_VIEW})")}
        assert not (columns & VIEWS[ISSUES_VIEW].forbidden_columns)


def test_view_hides_rows_outside_the_scope(repo):
    with repo._conn() as conn:
        create_scoped_views(conn, NV_TV1)
        units = {row[0] for row in conn.execute(f"SELECT unit_name FROM {ISSUES_VIEW}")}
        assert units == {TV1}


def test_labels_inherit_the_scope_through_the_join(repo):
    with repo._conn() as conn:
        create_scoped_views(conn, NV_TV1)
        codes = {row[0] for row in conn.execute(f"SELECT issue_code FROM {LABELS_VIEW}")}
        assert codes == {"A-001", "A-002"}


def test_user_without_any_unit_sees_nothing(repo):
    with repo._conn() as conn:
        create_scoped_views(conn, UserScope(username="x", role="user", unit_ids=[]))
        assert conn.execute(f"SELECT COUNT(*) FROM {ISSUES_VIEW}").fetchone()[0] == 0


def test_unit_name_with_a_quote_is_escaped(repo):
    """Tên đơn vị có dấu nháy không được làm hỏng câu CREATE VIEW."""
    with repo._conn() as conn:
        create_scoped_views(conn, UserScope(username="x", role="user", unit_ids=["O'Brien"]))
        assert conn.execute(f"SELECT COUNT(*) FROM {ISSUES_VIEW}").fetchone()[0] == 0


def test_missing_source_view_is_reported_clearly(tmp_path):
    conn = sqlite3.connect(tmp_path / "trong.db")
    with pytest.raises(ScopedViewError, match="v_issues_current"):
        create_scoped_views(conn, ADMIN)


def test_executor_runs_a_semantic_query_end_to_end(repo):
    """Đúng đường đã hỏng trong production: SELECT trên view đã áp phạm vi."""
    executor = SecureQueryExecutor(repo)
    plan = QueryPlan(
        pattern=QueryPattern.SEMANTIC_VIEW,
        answer_shape=AnswerShape.TABLE,
        original_query="sản phẩm nào bị Báo lỗi nhiều nhất",
        sql=(
            f"SELECT s.product, COUNT(DISTINCT s.issue_code) AS so_van_de "
            f"FROM {ISSUES_VIEW} s JOIN {LABELS_VIEW} l ON l.feedback_id = s.feedback_id "
            f"WHERE l.label = 'Báo lỗi' GROUP BY s.product ORDER BY so_van_de DESC"
        ),
        confidence=0.9,
    )

    result = executor.execute(plan, ADMIN)

    assert result.status.value == "ok", result.error_message
    assert result.data[0]["product"] == "Bulb"
    assert result.data[0]["so_van_de"] == 2


def test_executor_applies_the_scope_to_a_semantic_query(repo):
    executor = SecureQueryExecutor(repo)
    plan = QueryPlan(
        pattern=QueryPattern.SEMANTIC_VIEW,
        answer_shape=AnswerShape.TABLE,
        original_query="tổng số vấn đề",
        sql=f"SELECT COUNT(DISTINCT issue_code) AS so_van_de FROM {ISSUES_VIEW}",
        confidence=0.9,
    )

    assert executor.execute(plan, ADMIN).data[0]["so_van_de"] == 3
    assert executor.execute(plan, NV_TV1).data[0]["so_van_de"] == 2
