from __future__ import annotations

from datetime import UTC

from dms.label_history import LabelHistoryStore
from dms.metrics import MetricsCollector
from dms.time_utils import parse_utc_datetime
from dms.usage_tracker import UsageTracker


def test_usage_tracker_records_and_filters_utc_timestamps(tmp_path):
    tracker = UsageTracker(tmp_path / "usage.db")
    try:
        tracker.record(model="gemini", call_type="classify_batch", total_tokens=7)
        row = tracker._conn.execute("SELECT timestamp FROM gemini_usage_log").fetchone()

        recorded_at = parse_utc_datetime(row[0])
        assert recorded_at.tzinfo == UTC
        assert row[0].endswith("+00:00")

        result = tracker.query_usage(period="day")
        assert result["summary"]["total_calls"] == 1
    finally:
        tracker.close()


def test_label_history_date_filter_uses_utc_day_bounds(tmp_path):
    store = LabelHistoryStore(tmp_path / "labels.db")
    store.record("edit", "Bao loi", "definition", "old", "new", user="admin")

    history = store.get_history(date_from="2000-01-01", date_to="2999-12-31")

    assert history["total"] == 1
    timestamp = history["items"][0]["timestamp"]
    assert parse_utc_datetime(timestamp).tzinfo == UTC
    assert timestamp.endswith("+00:00")


def test_metrics_persist_utc_timestamp_suffix(tmp_path):
    metrics = MetricsCollector(tmp_path / "metrics.json")
    metrics.record_success("a.xlsx", rows=1, duration=0.2)

    assert metrics.last_success["at"].endswith("+00:00")
    health = metrics.get_health_data()
    assert health["last_poll"].endswith("+00:00")
