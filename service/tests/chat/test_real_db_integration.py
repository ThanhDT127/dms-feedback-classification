"""Integration tests running directly on REAL classification_jobs.db (27,554 records).

Skipped automatically if work/classification_jobs.db does not exist (e.g. clean CI runner).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dms.analytics import FeedbackAnalyticsRepository
from dms.chat.contract import (
    AnswerShape,
    QueryPattern,
    QueryPlan,
    QueryStatus,
    UserScope,
)
from dms.chat.db.migrations import apply_chat_migrations, sync_fts_index
from dms.chat.db.query_executor import SecureQueryExecutor

_REAL_DB_PATH = Path(__file__).resolve().parents[2] / "work" / "classification_jobs.db"


@pytest.mark.skipif(not _REAL_DB_PATH.exists(), reason="classification_jobs.db not found")
class TestRealDatabaseIntegration:
    """Kiểm thử thực tế trên 27,554 bản ghi cơ sở dữ liệu thật của DMS."""

    @pytest.fixture(scope="class")
    def real_repo(self):
        repo = FeedbackAnalyticsRepository(_REAL_DB_PATH)
        with repo._conn() as conn:
            apply_chat_migrations(conn)
            sync_fts_index(conn)
        return repo

    def test_real_data_record_count(self, real_repo):
        """Xác nhận cơ sở dữ liệu thật có đủ hơn 20,000 bản ghi."""
        with real_repo._conn() as conn:
            count = conn.execute(
                "SELECT count(*) FROM feedback_records WHERE is_active = 1"
            ).fetchone()[0]
            assert count > 20000

    def test_real_pattern_1_overview_admin_vs_scoped_user(self, real_repo):
        """Kiểm tra get_overview trên dữ liệu thật với quyền Admin vs User."""
        executor = SecureQueryExecutor(real_repo)
        admin_scope = UserScope(username="admin", role="admin")
        user_scope = UserScope(
            username="user_vung2", role="user", unit_ids=["Truyền thống Vùng 2"]
        )

        plan = QueryPlan(
            pattern=QueryPattern.SQL_TEMPLATE,
            answer_shape=AnswerShape.NUMBER,
            original_query="Tổng quan",
            function_name="get_overview",
            params={},
        )

        # Admin
        res_admin = executor.execute(plan, admin_scope)
        assert res_admin.status == QueryStatus.OK
        total_admin = res_admin.data[0]["total_issues"]["value"]
        assert total_admin > 20000

        # User Truyền thống Vùng 2
        res_user = executor.execute(plan, user_scope)
        assert res_user.status == QueryStatus.OK
        total_user = res_user.data[0]["total_issues"]["value"]
        assert 0 < total_user < total_admin

    def test_real_pattern_2_semantic_view(self, real_repo):
        """Kiểm tra truy vấn SQL trên v_issues_current thật."""
        executor = SecureQueryExecutor(real_repo)
        admin_scope = UserScope(username="admin", role="admin")

        plan = QueryPlan(
            pattern=QueryPattern.SEMANTIC_VIEW,
            answer_shape=AnswerShape.TABLE,
            original_query="Thống kê sắc thái",
            sql="SELECT sentiment, count(*) as count FROM v_issues_current GROUP BY sentiment ORDER BY count DESC",
        )
        res = executor.execute(plan, admin_scope)
        assert res.status == QueryStatus.OK
        assert len(res.data) >= 2
        sentiments = {r["sentiment"] for r in res.data if r.get("sentiment")}
        assert "Tiêu cực" in sentiments or "Tích cực" in sentiments

    def test_real_pattern_3_fts5_vietnamese_search(self, real_repo):
        """Kiểm tra FTS5 tìm kiếm từ khóa tiếng Việt có dấu và không dấu."""
        executor = SecureQueryExecutor(real_repo)
        admin_scope = UserScope(username="admin", role="admin")

        # Có dấu: "đèn led"
        plan_raw = QueryPlan(
            pattern=QueryPattern.FTS5_SEARCH,
            answer_shape=AnswerShape.LIST,
            original_query="Tìm đèn led",
            fts_query="đèn led",
            fts_limit=5,
        )
        res_raw = executor.execute(plan_raw, admin_scope)
        assert res_raw.status == QueryStatus.OK
        assert len(res_raw.data) > 0

        # Không dấu: "bao hanh" tìm "bảo hành"
        plan_nodau = QueryPlan(
            pattern=QueryPattern.FTS5_SEARCH,
            answer_shape=AnswerShape.LIST,
            original_query="Tìm bao hanh",
            fts_query="bao hanh",
            fts_limit=5,
        )
        res_nodau = executor.execute(plan_nodau, admin_scope)
        assert res_nodau.status == QueryStatus.OK
        assert len(res_nodau.data) > 0

    def test_real_pattern_4_json_extract_real_keys(self, real_repo):
        """Kiểm tra trích xuất dynamic keys từ cột raw_data_json thật."""
        executor = SecureQueryExecutor(real_repo)
        admin_scope = UserScope(username="admin", role="admin")

        with real_repo._conn() as conn:
            sample = conn.execute(
                "SELECT feedback_id, raw_data_json FROM feedback_records WHERE is_active = 1 AND length(raw_data_json) > 10 LIMIT 1"
            ).fetchone()
            fid = sample[0]
            keys = list(json.loads(sample[1]).keys())[:3]

        plan = QueryPlan(
            pattern=QueryPattern.JSON_EXTRACT,
            answer_shape=AnswerShape.NARRATIVE,
            original_query="Chi tiết JSON",
            json_keys=keys,
            json_filters={"feedback_id": fid},
        )
        res = executor.execute(plan, admin_scope)
        assert res.status == QueryStatus.OK
        assert res.data[0]["feedback_id"] == fid
        for k in keys:
            assert k in res.data[0]
