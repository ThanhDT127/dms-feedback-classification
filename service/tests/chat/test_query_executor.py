"""Comprehensive tests for SecureQueryExecutor across all 4 patterns and security guards."""

from __future__ import annotations

import json

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
from dms.classification_jobs import ClassificationJobStore


@pytest.fixture
def test_db_repo(tmp_path):
    """Tạo database SQLite đầy đủ và repository kiểm thử."""
    db_file = tmp_path / "test_jobs.db"
    ClassificationJobStore(db_file)
    repo = FeedbackAnalyticsRepository(db_file)

    with repo._conn() as conn:
        conn.execute(
            """
            INSERT INTO classification_jobs (
                job_id, owner_username, filename, status, input_path, output_path, created_at, updated_at
            ) VALUES
            ('j1', 'admin', 'f1.xlsx', 'completed', 'in1.xlsx', 'out1.xlsx', '2026-08-10T00:00:00Z', '2026-08-10T00:00:00Z'),
            ('j2', 'admin', 'f2.xlsx', 'completed', 'in2.xlsx', 'out2.xlsx', '2026-08-12T00:00:00Z', '2026-08-12T00:00:00Z')
            """
        )

        raw_data_1 = json.dumps({
            "Tên khách hàng": "Nguyễn Văn A",
            "Số điện thoại": "0912345678",
            "Địa chỉ": "Hồ Chí Minh",
            "Lỗi chi tiết": "Cháy tụ điện",
        }, ensure_ascii=False)

        raw_data_2 = json.dumps({
            "Tên khách hàng": "Trần Thị B",
            "Số điện thoại": "0987654321",
            "Địa chỉ": "Hà Nội",
            "Lỗi chi tiết": "Mối hàn ống hở",
        }, ensure_ascii=False)

        conn.execute(
            """
            INSERT INTO feedback_records (
                feedback_id, source_file_key, source_file_name, source_row_number,
                last_job_id, issue_code, issue_date, source, unit_name,
                business_status, product, product_line, model, sentiment,
                content, normalized_content, raw_data_json, is_active,
                classification_state, created_at, updated_at
            ) VALUES
            (101, 'k1', 'f1.xlsx', 1, 'j1', 'FB-101', '2026-08-10', 'Hotline', 'CN Miền Nam', 'Đã xử lý',
             'Đèn LED AT04', 'Đèn LED', 'AT04', 'Tiêu cực',
             'Đèn LED AT04 bị cháy sau 1 tháng sử dụng', 'den led at04 bi chay', ?, 1,
             'completed', '2026-08-10', '2026-08-10'),
            (102, 'k2', 'f2.xlsx', 2, 'j2', 'FB-102', '2026-08-12', 'Zalo', 'CN Miền Bắc', 'Chờ xử lý',
             'Ống nhựa PPR', 'Ống nhựa', 'PPR20', 'Tích cực',
             'Ống nhựa PPR chất lượng rất tốt, độ bền cao', 'ong nhua ppr chat luong tot', ?, 1,
             'completed', '2026-08-12', '2026-08-12')
            """,
            (raw_data_1, raw_data_2),
        )
        # Áp dụng migration của Chatbot (bảng FTS5, scoped views, chat tables)
        apply_chat_migrations(conn)
        sync_fts_index(conn)

    return repo


class TestSecureQueryExecutorPatterns:
    """Kiểm thử thực thi 4 pattern của SecureQueryExecutor."""

    def test_pattern_1_sql_template(self, test_db_repo):
        executor = SecureQueryExecutor(test_db_repo)
        admin = UserScope(username="admin", role="admin")

        plan = QueryPlan(
            pattern=QueryPattern.SQL_TEMPLATE,
            answer_shape=AnswerShape.NUMBER,
            original_query="Tổng quan phản hồi tháng 8",
            function_name="get_overview",
            params={"date_from": "2026-08-01", "date_to": "2026-08-31"},
        )
        res = executor.execute(plan, admin)
        assert res.status == QueryStatus.OK
        assert len(res.data) == 1
        assert res.data[0]["total_issues"]["value"] == 2
        assert res.metadata.pattern_used == "sql_template"

    def test_pattern_2_semantic_view(self, test_db_repo):
        executor = SecureQueryExecutor(test_db_repo)
        admin = UserScope(username="admin", role="admin")

        plan = QueryPlan(
            pattern=QueryPattern.SEMANTIC_VIEW,
            answer_shape=AnswerShape.TABLE,
            original_query="Danh sách sản phẩm phản hồi tiêu cực",
            sql="SELECT product, count(*) as cnt FROM v_issues_current WHERE sentiment = 'Tiêu cực' GROUP BY product",
        )
        res = executor.execute(plan, admin)
        assert res.status == QueryStatus.OK
        assert len(res.data) == 1
        assert res.data[0]["product"] == "Đèn LED AT04"
        assert res.data[0]["cnt"] == 1

    def test_pattern_2_semantic_view_scope_enforced(self, test_db_repo):
        executor = SecureQueryExecutor(test_db_repo)
        user_mn = UserScope(username="user_mn", role="user", unit_ids=["CN Miền Nam"])

        plan = QueryPlan(
            pattern=QueryPattern.SEMANTIC_VIEW,
            answer_shape=AnswerShape.TABLE,
            original_query="Danh sách tất cả sản phẩm",
            sql="SELECT feedback_id, product, unit_name FROM v_issues_current",
        )
        res = executor.execute(plan, user_mn)
        assert res.status == QueryStatus.OK
        # User Miền Nam chỉ nhìn thấy bản ghi của CN Miền Nam
        assert len(res.data) == 1
        assert res.data[0]["unit_name"] == "CN Miền Nam"
        assert res.data[0]["feedback_id"] == 101

    def test_pattern_2_rejects_forbidden_dml(self, test_db_repo):
        executor = SecureQueryExecutor(test_db_repo)
        admin = UserScope(username="admin", role="admin")

        plan = QueryPlan(
            pattern=QueryPattern.SEMANTIC_VIEW,
            answer_shape=AnswerShape.TABLE,
            original_query="Tấn công xóa dữ liệu",
            sql="DELETE FROM feedback_records WHERE 1=1",
        )
        res = executor.execute(plan, admin)
        assert res.status == QueryStatus.ERROR
        assert "Chỉ cho phép câu lệnh truy vấn đọc" in res.error_message

    def test_pattern_3_fts5_search_raw(self, test_db_repo):
        executor = SecureQueryExecutor(test_db_repo)
        admin = UserScope(username="admin", role="admin")

        plan = QueryPlan(
            pattern=QueryPattern.FTS5_SEARCH,
            answer_shape=AnswerShape.LIST,
            original_query="Tìm phản hồi về cháy đèn",
            fts_query="cháy",
        )
        res = executor.execute(plan, admin)
        assert res.status == QueryStatus.OK
        assert len(res.data) == 1
        assert res.data[0]["feedback_id"] == 101
        assert "cháy" in res.data[0]["content"].lower()

    def test_pattern_3_fts5_search_nodau_fallback(self, test_db_repo):
        executor = SecureQueryExecutor(test_db_repo)
        admin = UserScope(username="admin", role="admin")

        # Gõ không dấu "bi chay"
        plan = QueryPlan(
            pattern=QueryPattern.FTS5_SEARCH,
            answer_shape=AnswerShape.LIST,
            original_query="Tìm phản hồi den bi chay",
            fts_query="bi chay",
        )
        res = executor.execute(plan, admin)
        assert res.status == QueryStatus.OK
        assert len(res.data) == 1
        assert res.data[0]["feedback_id"] == 101

    def test_pattern_3_fts5_scope_isolation(self, test_db_repo):
        executor = SecureQueryExecutor(test_db_repo)
        # User Miền Bắc tìm kiếm "cháy" (thuộc Miền Nam)
        user_mb = UserScope(username="user_mb", role="user", unit_ids=["CN Miền Bắc"])

        plan = QueryPlan(
            pattern=QueryPattern.FTS5_SEARCH,
            answer_shape=AnswerShape.LIST,
            original_query="Tìm phản hồi cháy",
            fts_query="cháy",
        )
        res = executor.execute(plan, user_mb)
        # User Miền Bắc không thấy kết quả của Miền Nam
        assert res.status == QueryStatus.NO_DATA
        assert res.data == []

    def test_pattern_4_json_extract_allowed(self, test_db_repo):
        executor = SecureQueryExecutor(test_db_repo)
        admin = UserScope(username="admin", role="admin")

        plan = QueryPlan(
            pattern=QueryPattern.JSON_EXTRACT,
            answer_shape=AnswerShape.NARRATIVE,
            original_query="Chi tiết khách hàng của FB-101",
            json_keys=["Tên khách hàng", "Số điện thoại", "Lỗi chi tiết"],
            json_filters={"feedback_id": 101},
        )
        res = executor.execute(plan, admin)
        assert res.status == QueryStatus.OK
        assert len(res.data) == 1
        data = res.data[0]
        assert data["Tên khách hàng"] == "Nguyễn Văn A"
        assert data["Số điện thoại"] == "0912345678"
        assert data["Lỗi chi tiết"] == "Cháy tụ điện"

    def test_pattern_4_json_extract_forbidden_unit_blocked(self, test_db_repo):
        executor = SecureQueryExecutor(test_db_repo)
        # User Miền Bắc cố truy cập FB-101 thuộc Miền Nam
        user_mb = UserScope(username="user_mb", role="user", unit_ids=["CN Miền Bắc"])

        plan = QueryPlan(
            pattern=QueryPattern.JSON_EXTRACT,
            answer_shape=AnswerShape.NARRATIVE,
            original_query="Chi tiết FB-101",
            json_keys=["Tên khách hàng"],
            json_filters={"feedback_id": 101},
        )
        res = executor.execute(plan, user_mb)
        assert res.status == QueryStatus.ERROR
        assert "nằm ngoài phạm vi được phân quyền" in res.error_message
