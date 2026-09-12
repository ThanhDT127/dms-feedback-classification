from __future__ import annotations

import gc
import multiprocessing
import sqlite3
import traceback
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
from analytics_support import seed_classified_records

from dms.analytics.repository import FeedbackAnalyticsRepository
from dms.analytics.result_cache import cached_result, init_cache_revision
from dms.classification_jobs import ClassificationJobStore


@pytest.fixture
def repository(tmp_path: Path) -> FeedbackAnalyticsRepository:
    db_path = tmp_path / "source.db"
    ClassificationJobStore(db_path)
    repo = FeedbackAnalyticsRepository(db_path)
    with closing(sqlite3.connect(db_path)) as conn, conn:
        init_cache_revision(conn)
    seed_classified_records(repo, db_path=db_path, entries=[{"labels": ["Báo lỗi"]}])
    return repo


def test_two_repository_instances_share_json_result(repository):
    calls = []

    def compute():
        calls.append(1)
        return {"total": 1, "labels": ["Báo lỗi"]}

    first = cached_result(repository, "dashboard", compute)
    first["labels"].append("mutated by caller")
    other = FeedbackAnalyticsRepository(repository.db_path)
    assert cached_result(other, "dashboard", compute) == {"total": 1, "labels": ["Báo lỗi"]}
    assert len(calls) == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE feedback_records SET content = 'changed'",
        "DELETE FROM feedback_records",
        "INSERT INTO feedback_records (source_file_key, source_file_name, source_row_number, "
        "last_job_id, raw_data_json, content, normalized_content, classification_state, "
        "created_at, updated_at) VALUES ('new', 'new', 3, 'seed-0', '{}', 'new', 'new', "
        "'pending', 'now', 'now')",
        "UPDATE feedback_labels SET label = 'changed'",
        "DELETE FROM feedback_labels",
        "INSERT INTO feedback_labels VALUES (1, 'new', 'group', 'now')",
    ],
)
def test_external_source_write_invalidates_other_instance(repository, mutation):
    from dms.analytics import result_cache

    with closing(sqlite3.connect(repository.db_path)) as conn, conn:
        result_cache.init_cache_revision(conn)
    other = FeedbackAnalyticsRepository(repository.db_path)
    assert cached_result(repository, "key", lambda: {"value": "before"}) == {"value": "before"}
    with closing(sqlite3.connect(repository.db_path)) as conn, conn:
        conn.execute(mutation)
    assert cached_result(other, "key", lambda: {"value": "after"}) == {"value": "after"}


def _cache_worker(db_path, entered, release, observed_read, calls, output, leader):
    try:
        # A connection trace is a deterministic signal that the follower has entered
        # its cold lookup, rather than relying on scheduler timing or sleeps.
        original_connect = sqlite3.connect

        def connect(*args, **kwargs):
            conn = original_connect(*args, **kwargs)
            if not leader:
                conn.set_trace_callback(
                    lambda statement: (
                        observed_read.set() if statement.startswith("SELECT payload") else None
                    )
                )
            return conn

        sqlite3.connect = connect

        def compute():
            with calls.get_lock():
                calls.value += 1
            if leader:
                entered.set()
                assert release.wait(15), "parent did not release leader"
            return {"count": 42}

        output.put(("ok", cached_result(SimpleNamespace(db_path=db_path), "shared", compute)))
    except BaseException:
        output.put(("error", traceback.format_exc()))


def _legacy_migration_worker(db_path, start, output):
    try:
        assert start.wait(15), "parent did not release migration workers"
        value = cached_result(
            SimpleNamespace(db_path=db_path),
            "legacy-shared",
            lambda: {"count": 42},
        )
        output.put(("ok", value))
    except BaseException:
        output.put(("error", traceback.format_exc()))


def test_spawned_processes_migrate_legacy_sidecar_without_racing(repository):
    from dms.analytics.result_cache import cache_path

    with closing(sqlite3.connect(cache_path(repository))) as conn, conn:
        conn.execute(
            "CREATE TABLE results "
            "(cache_key TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL)"
        )

    context = multiprocessing.get_context("spawn")
    start, output = context.Event(), context.Queue()
    workers = [
        context.Process(
            target=_legacy_migration_worker,
            args=(repository.db_path, start, output),
        )
        for _ in range(12)
    ]
    try:
        for worker in workers:
            worker.start()
        start.set()
        results = [output.get(timeout=30) for _ in workers]
        for worker in workers:
            worker.join(30)
            assert worker.exitcode == 0
        assert results == [("ok", {"count": 42})] * len(workers)
    finally:
        start.set()
        for worker in workers:
            if worker.pid is not None:
                if worker.is_alive():
                    worker.terminate()
                worker.join(5)
                worker.close()
        output.close()
        output.join_thread()


def test_spawned_processes_single_flight(repository):
    context = multiprocessing.get_context("spawn")
    entered, release, observed_read = (context.Event() for _ in range(3))
    calls, output = context.Value("i", 0), context.Queue()
    args = (repository.db_path, entered, release, observed_read, calls, output)
    workers = [
        context.Process(target=_cache_worker, args=(*args, leader)) for leader in (True, False)
    ]
    try:
        workers[0].start()
        assert entered.wait(15), "leader did not start compute"
        workers[1].start()
        assert observed_read.wait(15), "follower did not reach cache lookup"
        release.set()
        results = [output.get(timeout=20) for _ in workers]
        for worker in workers:
            worker.join(20)
            assert worker.exitcode == 0
        assert results == [("ok", {"count": 42})] * 2
        assert calls.value == 1
    finally:
        release.set()
        for worker in workers:
            if worker.pid is not None:
                if worker.is_alive():
                    worker.terminate()
                worker.join(5)
                worker.close()
        output.close()
        output.join_thread()


def test_cache_bounds_entries(repository):
    for index in range(140):
        assert cached_result(repository, f"key-{index}", lambda index=index: {"i": index}) == {
            "i": index
        }
    from dms.analytics.result_cache import cache_path

    with closing(sqlite3.connect(cache_path(repository))) as conn:
        assert conn.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 128


def test_recreated_source_at_same_path_never_reuses_old_payload(tmp_path):
    db_path = tmp_path / "replaced.db"
    first = FeedbackAnalyticsRepository(db_path)
    seed_classified_records(first, db_path=db_path, entries=[{"issue_code": "OLD"}])
    assert cached_result(first, "dashboard", lambda: {"value": "OLD"}) == {"value": "OLD"}
    assert first.fetch_analytics_rows()[0]["issue_code"] == "OLD"
    gc.collect()

    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(db_path) + suffix)
        if candidate.exists():
            candidate.unlink()
    second = FeedbackAnalyticsRepository(db_path)
    seed_classified_records(second, db_path=db_path, entries=[{"issue_code": "NEW"}])
    assert cached_result(second, "dashboard", lambda: {"value": "NEW"}) == {"value": "NEW"}
    assert first.fetch_analytics_rows()[0]["issue_code"] == "NEW"


def test_compute_exception_rolls_back_and_can_retry(repository):
    def fail():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        cached_result(repository, "failure", fail)
    assert cached_result(repository, "failure", lambda: {"fresh": True}) == {"fresh": True}


def test_source_write_during_compute_is_not_blocked_or_cached(repository):
    from dms.analytics.result_cache import cache_path

    def compute():
        with closing(sqlite3.connect(repository.db_path, timeout=0.1)) as conn, conn:
            conn.execute("UPDATE feedback_labels SET label = 'changed'")
        return {"possibly_stale": True}

    assert cached_result(repository, "racing", compute) == {"possibly_stale": True}
    with closing(sqlite3.connect(cache_path(repository))) as conn:
        assert conn.execute("SELECT COUNT(*) FROM results").fetchone()[0] == 0
    assert cached_result(repository, "racing", lambda: {"fresh": True}) == {"fresh": True}
