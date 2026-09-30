"""DB fixture cho Pattern 2 (b09 task 1.3).

Dữ liệu đi qua ``FeedbackAnalyticsRepository`` thật (schema của Dev A) để test nhất quán với
``FeedbackAnalyticsService``. Hai view đã áp phạm vi ``v_issues_current_scoped`` và
``v_issue_labels_scoped`` được dựng **tạm** bằng TEMP VIEW theo đề xuất gửi Dev A, cho tới khi
có bản thật.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from analytics_support import seed_classified_records

from dms.analytics import FeedbackAnalyticsRepository, FeedbackAnalyticsService
from dms.chat.contract import QueryPattern, QueryPlan, QueryResult, UserScope
from dms.chat.db.migrations import apply_chat_migrations

TV1, TV2, NT, BH = "Truyền thống Vùng 1", "Truyền thống Vùng 2", "Nha Trang", "Biên Hòa"

ISSUE_COLUMNS = (
    "feedback_id",
    "issue_code",
    "issue_date",
    "unit_name",
    "sentiment",
    "business_status",
    "product",
    "product_line",
    "model",
    "source",
    "brand",
    "content",
    "source_file_name",
    "source_row_number",
    "classification_state",
    "created_at",
    "updated_at",
)


def _entries() -> list[dict[str, object]]:
    products = ["Đèn LED Bulb", "Đèn LED Tube", "Phích nước", "Đèn bàn", "Ổ cắm"]
    units = [TV1, TV2, NT, BH]
    sentiments = ["Tiêu cực", "Tích cực", "Trung lập"]
    statuses = ["Chờ xử lý", "Đã xử lý"]
    sources = ["Zalo", "Hotline", "Email"]
    labels = [["Báo lỗi"], ["Bảo hành"], ["Báo CL tốt"], ["Y/c cải tiến"], ["Báo lỗi", "Bảo hành"]]
    entries: list[dict[str, object]] = []
    for i in range(48):
        month = 7 if i % 4 == 0 else 8
        code: str | None = f"VD-{i // 3 + 1:03d}" if i % 11 != 10 else None  # vài dòng thiếu mã
        entries.append(
            {
                "content": f"Phản hồi số {i} về {products[i % 5].lower()}",
                "issue_code": code,
                "issue_date": f"2026-{month:02d}-{(i % 27) + 1:02d}",
                "unit_name": units[i % 4],
                "business_status": statuses[i % 2],
                "source": sources[i % 3],
                "labels": labels[i % 5],
                "product": products[(i * 7) % 5],
                "sentiment": sentiments[(i * 5) % 3],
            }
        )
    return entries


ENTRIES = _entries()


def build_sql_fixture(db_path: Path) -> FeedbackAnalyticsService:
    repo = FeedbackAnalyticsRepository(db_path)
    seed_classified_records(repo, db_path=db_path, entries=ENTRIES)
    with sqlite3.connect(db_path) as conn:
        apply_chat_migrations(conn)
    return FeedbackAnalyticsService(repo)


def create_scoped_views(conn: sqlite3.Connection, scope: UserScope) -> None:
    """TEMP VIEW theo phạm vi của người hỏi — đề xuất cho Dev A (b09 D5, câu hỏi mở)."""
    params: list[str] = []
    where = ""
    if not scope.is_admin:
        units = list(scope.unit_ids) or ["\x00"]
        where = f" WHERE unit_name IN ({','.join('?' * len(units))})"
        params = units
    conn.execute("DROP VIEW IF EXISTS temp.v_issue_labels_scoped")
    conn.execute("DROP VIEW IF EXISTS temp.v_issues_current_scoped")
    # SQLite không cho tham số trong CREATE VIEW: chèn literal đã escape (chỉ dùng cho fixture).
    literal_where = where
    for value in params:
        literal_where = literal_where.replace("?", "'" + value.replace("'", "''") + "'", 1)
    conn.execute(
        f"CREATE TEMP VIEW v_issues_current_scoped AS SELECT {', '.join(ISSUE_COLUMNS)} "
        f"FROM main.v_issues_current{literal_where}"
    )
    conn.execute(
        "CREATE TEMP VIEW v_issue_labels_scoped AS "
        "SELECT l.feedback_id, s.issue_code, l.label, l.major_group "
        "FROM main.feedback_labels l JOIN v_issues_current_scoped s ON s.feedback_id = l.feedback_id"
    )


class SqliteSemanticExecutor:
    """Executor Pattern 2 giả trên fixture: connection chỉ đọc + view đã áp phạm vi."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.calls: list[QueryPlan] = []
        self.scopes: list[UserScope] = []

    def execute(self, plan: QueryPlan, scope: UserScope) -> QueryResult:
        self.calls.append(plan)
        self.scopes.append(scope)
        if plan.pattern != QueryPattern.SEMANTIC_VIEW:
            return QueryResult.error("SqliteSemanticExecutor chỉ hỗ trợ semantic_view")
        return run_scoped_sql(self.db_path, str(plan.sql or ""), scope)


def run_scoped_sql(db_path: Path, sql: str, scope: UserScope) -> QueryResult:
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        create_scoped_views(conn, scope)
        conn.execute("PRAGMA query_only = ON")
        rows = conn.execute(sql).fetchall()
    except sqlite3.Error as exc:
        return QueryResult.error(f"Lỗi thực thi: {exc}")
    finally:
        conn.close()
    return QueryResult.ok([dict(r) for r in rows]) if rows else QueryResult.no_data()
