"""Failed usage writes must release SQLite's shared writer lock."""

import sqlite3

import pytest

from dms.usage_tracker import UsageTracker


@pytest.mark.parametrize("operation", ["backfill", "record", "cleanup"])
def test_failed_usage_write_releases_writer(tmp_path, operation):
    with UsageTracker(tmp_path / "usage.db") as tracker:
        for _ in range(2):
            tracker.record(model="gemini-2.5-flash", call_type="test", prompt_tokens=100)
        if operation == "backfill":
            event = "UPDATE OF estimated_cost_usd"

            def action():
                return tracker.backfill_missing_costs()
        elif operation == "record":
            event = "INSERT"

            def action():
                return tracker.record(model="gemini-2.5-flash", call_type="test")
        else:
            event = "DELETE"

            def action():
                return tracker.cleanup(retention_days=-1)

        with tracker._conn:
            tracker._conn.execute(
                f"CREATE TRIGGER reject_write BEFORE {event} ON gemini_usage_log "
                "BEGIN SELECT RAISE(ABORT, 'injected write failure'); END"
            )
        with pytest.raises(sqlite3.IntegrityError, match="injected write failure"):
            action()
        with sqlite3.connect(tmp_path / "usage.db", timeout=0.05) as other:
            other.execute("BEGIN IMMEDIATE")
            other.execute("CREATE TABLE recovery_probe (id INTEGER)")
        assert not tracker._conn.in_transaction
        assert tracker.query_usage()["summary"]["total_calls"] == 2
