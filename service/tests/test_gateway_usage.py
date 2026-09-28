"""SQLite audit migration and Gateway accounting, with synthetic data only."""

import sqlite3

import pytest

from dms.time_utils import utc_now_iso
from dms.usage_tracker import UsageTracker

LEGACY_SCHEMA = """
CREATE TABLE gemini_usage_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
    model TEXT NOT NULL, call_type TEXT NOT NULL, job_id TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL NOT NULL DEFAULT 0,
    duration_ms INTEGER, success INTEGER NOT NULL DEFAULT 1
);
"""


def attempt(tracker, event_kind="attempt_finished", **overrides):
    fields = dict(
        event_kind=event_kind,
        operation_id="op-1",
        attempt_id="attempt-1",
        actor="alice",
        job_id="job-1",
        route="gateway",
        incident_id=None,
        model_requested="Exact/Alias",
        outcome="success",
    )
    fields.update(overrides)
    return tracker.record_attempt(**fields)


def test_additive_migration_preserves_legacy_rows_and_reopens_idempotently(tmp_path):
    path = tmp_path / "usage.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(LEGACY_SCHEMA)
        conn.execute(
            "INSERT INTO gemini_usage_log (timestamp, model, call_type, total_tokens) "
            "VALUES (?, 'legacy', 'classify_batch', 7)",
            (utc_now_iso(),),
        )
        original = conn.execute("SELECT * FROM gemini_usage_log").fetchone()
    for _ in range(2):
        with UsageTracker(path) as tracker:
            assert tracker.query_usage()["summary"]["total_tokens"] == 7
            assert tracker.query_usage()["summary"]["total_calls"] == 1
        with sqlite3.connect(path) as conn:
            row = conn.execute("SELECT * FROM gemini_usage_log").fetchone()
            assert row[: len(original)] == original
            columns = {r[1] for r in conn.execute("PRAGMA table_info(gemini_usage_log)")}
            assert {
                "event_kind",
                "operation_id",
                "attempt_id",
                "actor",
                "route",
                "response_id",
                "incident_id",
                "model_requested",
                "model_actual",
                "outcome",
                "error_category",
                "usage_known",
            } <= columns
            assert conn.execute("SELECT event_kind FROM gemini_usage_log").fetchone() == ("legacy",)


def test_attempt_events_are_idempotent_and_summaries_count_finished_only(tmp_path):
    path = tmp_path / "usage.db"
    usage = {"prompt_tokens": 11, "completion_tokens": 4, "total_tokens": 15}
    with UsageTracker(path) as tracker:
        tracker.record(model="legacy", call_type="generate", job_id="job-1", total_tokens=7)
        for _ in range(2):
            attempt(tracker, "attempt_started", outcome="unknown")
            attempt(
                tracker,
                usage=usage,
                estimated_cost_usd=0.01,
                response_id="response-1",
                model_actual="actual-model",
            )
        summary = tracker.query_usage()["summary"]
        assert summary["total_calls"] == 2
        assert summary["success_count"] == 2
        assert summary["total_tokens"] == 22
        assert summary["total_cost_usd"] == 0.01
        assert tracker.query_usage()["daily"][0]["calls"] == 2
        assert tracker.query_usage()["by_type"]["generate"]["calls"] == 2
        assert tracker.get_top_jobs()[0]["calls"] == 2
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM gemini_usage_log").fetchone() == (3,)
        assert conn.execute(
            "SELECT response_id, model_actual, actor FROM gemini_usage_log "
            "WHERE event_kind='attempt_finished'"
        ).fetchone() == ("response-1", "actual-model", "alice")


def test_unknown_usage_and_cost_remain_null_in_audit_and_all_summaries(tmp_path):
    with UsageTracker(tmp_path / "usage.db") as tracker:
        attempt(tracker, "attempt_started", attempt_id="crashed", outcome="unknown")
        attempt(tracker, usage=None)
        events = tracker.query_attempts()
        assert len(events) == 2
        assert events[0]["outcome"] == "unknown"
        for event in events:
            assert event["usage"] is None
            assert event["estimated_cost_usd"] is None
            assert event["model_actual"] is None
        report = tracker.query_usage()
        assert report["summary"]["total_calls"] == 1
        assert report["summary"]["total_tokens"] is None
        assert report["summary"]["total_cost_usd"] is None
        for row in [report["daily"][0], report["by_type"]["generate"], tracker.get_top_jobs()[0]]:
            assert row["total_tokens"] is None
            assert row["cost_usd"] is None


def test_partial_usage_and_unmapped_cost_do_not_become_zero(tmp_path):
    with UsageTracker(tmp_path / "usage.db") as tracker:
        attempt(tracker, usage={"prompt_tokens": 0, "total_tokens": 9})
        event = tracker.query_attempts()[0]
        assert event["usage"] == {"prompt_tokens": 0, "total_tokens": 9}
        assert event["estimated_cost_usd"] is None
        summary = tracker.query_usage()["summary"]
        assert summary["total_prompt_tokens"] == 0
        assert summary["total_completion_tokens"] is None
        assert summary["total_tokens"] == 9
        assert summary["total_cost_usd"] is None


def test_failed_usage_is_retained_and_unknown_is_not_a_success(tmp_path):
    with UsageTracker(tmp_path / "usage.db") as tracker:
        attempt(
            tracker,
            outcome="error",
            error_category="response_parse",
            response_id="response-1",
            usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            estimated_cost_usd=0.01,
        )
        summary = tracker.query_usage()["summary"]
        assert summary["total_tokens"] == 12
        assert summary["total_cost_usd"] == 0.01
        assert summary["success_count"] == 0
        assert summary["fail_count"] == 1
        attempt(tracker, attempt_id="unknown", outcome="unknown")
        summary = tracker.query_usage()["summary"]
        assert summary["total_calls"] == 2
        assert summary["success_count"] == 0
        assert summary["total_tokens"] is None
        assert summary["total_cost_usd"] is None


def test_usage_whitelist_retains_provider_counters_not_raw_payload(tmp_path):
    with UsageTracker(tmp_path / "usage.db") as tracker:
        attempt(
            tracker,
            usage={
                "prompt_tokens": 5,
                "completion_tokens": 2,
                "total_tokens": 10,
                "thinking_tokens": 3,
                "cached_tokens": 1,
                "prompt": "sensitive-content",
                "api_key": "sensitive-key",
            },
        )
        usage = tracker.query_attempts()[0]["usage"]
        assert usage == {
            "prompt_tokens": 5,
            "completion_tokens": 2,
            "total_tokens": 10,
            "thinking_tokens": 3,
            "cached_tokens": 1,
        }
        assert "sensitive" not in " ".join(tracker._conn.iterdump())


@pytest.mark.parametrize(
    "overrides",
    [
        {"event_kind": "invalid"},
        {"attempt_id": ""},
        {"operation_id": ""},
        {"actor": ""},
        {"error_category": "raw error body\nsecret"},
        {"usage": {"prompt_tokens": -1}},
        {"usage": {"total_tokens": 1.5}},
        {"usage": {"total_tokens": True}},
        {"usage": {"total_tokens": "secret"}},
        {"estimated_cost_usd": -1},
        {"estimated_cost_usd": float("nan")},
        {"estimated_cost_usd": float("inf")},
        {"event_kind": "attempt_started", "usage": {"total_tokens": 2}},
    ],
)
def test_invalid_audit_evidence_is_rejected_before_persistence(tmp_path, overrides):
    with UsageTracker(tmp_path / "usage.db") as tracker:
        with pytest.raises(ValueError) as caught:
            attempt(tracker, **overrides)
        assert "secret" not in str(caught.value)
        assert tracker.query_attempts() == []


def test_sqlite_failure_propagates_without_hidden_retry(tmp_path):
    with UsageTracker(tmp_path / "usage.db") as tracker:
        tracker._conn.execute("PRAGMA query_only=ON")
        with pytest.raises(sqlite3.OperationalError):
            attempt(tracker)
        tracker._conn.execute("PRAGMA query_only=OFF")
        assert tracker.query_attempts() == []


def test_started_without_finish_reports_unknown_after_reopen(tmp_path):
    path = tmp_path / "usage.db"
    with UsageTracker(path) as tracker:
        attempt(tracker, "attempt_started", outcome="started")
    with UsageTracker(path) as tracker:
        assert tracker.query_attempts()[0]["outcome"] == "unknown"
        assert tracker.query_attempts()[0]["usage"] is None
        assert tracker.query_usage()["summary"]["success_count"] == 0
        assert tracker.query_usage()["summary"]["total_calls"] == 0


def test_duplicate_events_across_connections_use_database_uniqueness(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "usage.db"

    def write_event(_):
        with UsageTracker(path) as tracker:
            attempt(
                tracker,
                usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                estimated_cost_usd=0,
            )

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(write_event, range(8)))
    with UsageTracker(path) as tracker:
        assert len(tracker.query_attempts()) == 1
        assert tracker.query_usage()["summary"]["total_calls"] == 1
        assert tracker.query_usage()["summary"]["total_tokens"] == 0
        assert tracker.query_usage()["summary"]["total_cost_usd"] == 0
