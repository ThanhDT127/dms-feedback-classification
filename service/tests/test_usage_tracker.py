from __future__ import annotations

from dms.usage_tracker import UsageTracker


def test_usage_tracker_context_manager_closes_connection(tmp_path):
    db_path = tmp_path / "usage.db"

    with UsageTracker(db_path) as tracker:
        tracker.record(model="gemini", call_type="classify_batch", total_tokens=7)
        assert tracker.query_usage()["summary"]["total_calls"] == 1

    assert tracker._conn is None
