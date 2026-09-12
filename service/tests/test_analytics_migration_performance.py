"""Upgrade coverage for analytics performance schema additions."""

from __future__ import annotations

import sqlite3

from analytics_support import seed_classified_records

from dms.analytics import FeedbackAnalyticsRepository


def test_existing_analytics_database_upgrades_idempotently_without_data_loss(tmp_path):
    db_path = tmp_path / "existing.db"
    original = FeedbackAnalyticsRepository(db_path)
    seed_classified_records(
        original,
        db_path=db_path,
        entries=[{"issue_code": "PRESERVED", "labels": ["Báo lỗi"]}],
    )
    with sqlite3.connect(db_path) as conn:
        conn.execute("DROP INDEX IF EXISTS idx_feedback_records_active_page_order")
        conn.execute("DELETE FROM analytics_schema_migrations WHERE version = 2")
        for table in ("feedback_records", "feedback_labels"):
            for operation in ("insert", "update", "delete"):
                conn.execute(f"DROP TRIGGER IF EXISTS cache_revision_{table}_{operation}")
        conn.execute("DROP TABLE IF EXISTS analytics_cache_identity")
        conn.execute("DROP TABLE IF EXISTS analytics_cache_revision")

    first_upgrade = FeedbackAnalyticsRepository(db_path)
    second_worker = FeedbackAnalyticsRepository(db_path)

    assert first_upgrade.fetch_current_records()[0]["issue_code"] == "PRESERVED"
    assert second_worker.fetch_current_labels(row=2) == ["Báo lỗi"]
    with sqlite3.connect(db_path) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM analytics_schema_migrations WHERE version = 2"
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'index' "
                "AND name = 'idx_feedback_records_active_page_order'"
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute("SELECT length(database_id) FROM analytics_cache_identity").fetchone()[0]
            == 32
        )
        before = conn.execute("SELECT revision FROM analytics_cache_revision").fetchone()[0]
        conn.execute("UPDATE feedback_records SET content = content WHERE feedback_id = 1")
        after = conn.execute("SELECT revision FROM analytics_cache_revision").fetchone()[0]
        assert after == before + 1
