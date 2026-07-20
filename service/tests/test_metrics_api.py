"""Tests for metrics API endpoints (tasks 9.6-9.8)."""

from __future__ import annotations

import json
import uuid

import pytest
from conftest import TEST_USER, apply_auth_overrides
from fastapi.testclient import TestClient

from dms.web.app import create_app
from dms.web.deps import get_current_user


@pytest.fixture
def api_client(settings, monkeypatch):
    """Create a test client with admin auth and patched work_dir."""
    settings.ensure_runtime_dirs()
    # Patch _work_dir in the metrics_api module to use test settings
    monkeypatch.setattr("dms.web.api.metrics_api._work_dir", lambda: settings.work_dir)
    app = create_app()
    apply_auth_overrides(app)
    return TestClient(app)


@pytest.fixture
def api_client_non_admin(settings, monkeypatch):
    """Create a test client with non-admin auth."""
    settings.ensure_runtime_dirs()
    monkeypatch.setattr("dms.web.api.metrics_api._work_dir", lambda: settings.work_dir)
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: TEST_USER
    return TestClient(app)


# ---------- POST /api/metrics/reset-failed (task 9.6) ----------


def test_reset_failed_resets_all(settings, api_client):
    """Admin resets all failed files."""
    seen_data = {
        "f1": {"status": "failed", "failures": 3, "name": "a.xlsx"},
        "f2": {"status": "done", "name": "b.xlsx"},
        "f3": {"status": "failed", "failures": 3, "name": "c.xlsx"},
    }
    (settings.work_dir / "seen_files.json").write_text(json.dumps(seen_data), encoding="utf-8")
    metrics_data = {"files_failed": 2, "files_processed": 1}
    (settings.work_dir / "metrics.json").write_text(json.dumps(metrics_data), encoding="utf-8")

    resp = api_client.post("/api/metrics/reset-failed")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reset_count"] == 2

    # Verify seen_files updated
    seen_after = json.loads((settings.work_dir / "seen_files.json").read_text(encoding="utf-8"))
    assert seen_after["f1"]["status"] == "retry"
    assert seen_after["f1"]["failures"] == 0
    assert seen_after["f3"]["status"] == "retry"
    assert seen_after["f2"]["status"] == "done"  # unchanged

    # Verify metrics.json updated
    m_after = json.loads((settings.work_dir / "metrics.json").read_text(encoding="utf-8"))
    assert m_after["files_failed"] == 0


def test_reset_failed_by_ids(settings, api_client):
    """Admin resets specific files by IDs."""
    seen_data = {
        "f1": {"status": "failed", "failures": 3, "name": "a.xlsx"},
        "f2": {"status": "failed", "failures": 3, "name": "b.xlsx"},
    }
    (settings.work_dir / "seen_files.json").write_text(json.dumps(seen_data), encoding="utf-8")
    metrics_data = {"files_failed": 2}
    (settings.work_dir / "metrics.json").write_text(json.dumps(metrics_data), encoding="utf-8")

    resp = api_client.post("/api/metrics/reset-failed", json={"file_ids": ["f1"]})
    assert resp.status_code == 200
    assert resp.json()["reset_count"] == 1

    seen_after = json.loads((settings.work_dir / "seen_files.json").read_text(encoding="utf-8"))
    assert seen_after["f1"]["status"] == "retry"
    assert seen_after["f2"]["status"] == "failed"  # not reset


def test_reset_failed_non_admin_returns_403(api_client_non_admin):
    """Non-admin users get 403."""
    resp = api_client_non_admin.post("/api/metrics/reset-failed")
    assert resp.status_code == 403


# ---------- GET /api/metrics/by-user (task 9.7) ----------


def test_metrics_by_user_admin_returns_data(settings, monkeypatch):
    """Admin gets per-user stats."""
    settings.ensure_runtime_dirs()
    from dms.classification_jobs import ClassificationJobStore

    db_path = settings.work_dir / "classification_jobs.db"
    store = ClassificationJobStore(db_path)

    # Create some jobs using keyword-only args
    job1 = str(uuid.uuid4())
    store.create_job(
        job_id=job1,
        owner_username="alice",
        owner_role="user",
        filename="test.xlsx",
        mode="classify",
        input_path="/uploads/test.xlsx",
        output_path="/output/test.xlsx",
    )
    store.complete_job(
        job1,
        total_rows=50,
        rows_done=50,
        output_path="/output/test.xlsx",
        duration_seconds=5.0,
    )

    job2 = str(uuid.uuid4())
    store.create_job(
        job_id=job2,
        owner_username="alice",
        owner_role="user",
        filename="test2.xlsx",
        mode="classify",
        input_path="/uploads/test2.xlsx",
        output_path="/output/test2.xlsx",
    )
    store.fail_job(job2, "RuntimeError: boom")

    job3 = str(uuid.uuid4())
    store.create_job(
        job_id=job3,
        owner_username="bob",
        owner_role="user",
        filename="test3.xlsx",
        mode="classify",
        input_path="/uploads/test3.xlsx",
        output_path="/output/test3.xlsx",
    )
    store.complete_job(
        job3,
        total_rows=30,
        rows_done=30,
        output_path="/output/test3.xlsx",
        duration_seconds=3.0,
    )

    # Mock the job store
    monkeypatch.setattr("dms.web.api.metrics_api._work_dir", lambda: settings.work_dir)
    app = create_app()
    apply_auth_overrides(app)

    from dms.web import deps

    def mock_job_store():
        return store

    monkeypatch.setattr(deps, "get_classification_job_store", mock_job_store)

    client = TestClient(app)
    resp = client.get("/api/metrics/by-user")
    assert resp.status_code == 200
    data = resp.json()
    users = data["users"]
    assert len(users) >= 2

    # Find alice
    alice = next((u for u in users if u["username"] == "alice"), None)
    assert alice is not None
    assert alice["total_jobs"] == 2
    assert alice["completed"] == 1
    assert alice["failed"] == 1


def test_metrics_by_user_non_admin_returns_403(api_client_non_admin):
    """Non-admin users get 403."""
    resp = api_client_non_admin.get("/api/metrics/by-user")
    assert resp.status_code == 403


def test_metrics_by_user_empty_state(settings, monkeypatch):
    """Empty state returns empty users list."""
    settings.ensure_runtime_dirs()
    from dms.classification_jobs import ClassificationJobStore

    db_path = settings.work_dir / "classification_jobs.db"
    store = ClassificationJobStore(db_path)

    monkeypatch.setattr("dms.web.api.metrics_api._work_dir", lambda: settings.work_dir)
    app = create_app()
    apply_auth_overrides(app)

    from dms.web import deps

    monkeypatch.setattr(deps, "get_classification_job_store", lambda: store)

    client = TestClient(app)
    resp = client.get("/api/metrics/by-user")
    assert resp.status_code == 200
    assert resp.json()["users"] == []


# ---------- GET /api/metrics — file-level outcome (Hướng A) ----------


def test_metrics_retried_file_counts_as_success(settings, api_client):
    """File that failed then retried successfully → success rate 100%, not 98.9%.

    seen_files.json is the source of truth for file-level outcomes.
    failures > 0 AND status = 'done' → counted as success AND retried.
    """
    seen_data = {
        "file-always-done": {
            "name": "always_ok.xlsx",
            "status": "done",
            "failures": 0,
            "processed_at": "2026-07-15T10:00:00Z",
            "total_rows": 50,
            "duration_seconds": 3.0,
        },
        "file-retried-success": {
            "name": "retried_ok.xlsx",
            "status": "done",
            "past_failures": 2,  # ← field mới: Watcher preserve khi retry thành công
            "failures": 0,  # reset về 0 sau khi done
            "processed_at": "2026-07-16T09:00:00Z",
            "total_rows": 30,
            "duration_seconds": 5.0,
        },
        "file-still-failed": {
            "name": "stuck.xlsx",
            "status": "failed",  # vẫn thất bại, chưa được retry
            "failures": 3,
            "last_attempt": "2026-07-14T08:00:00Z",
        },
    }
    (settings.work_dir / "seen_files.json").write_text(json.dumps(seen_data), encoding="utf-8")

    resp = api_client.get("/api/metrics")
    assert resp.status_code == 200
    data = resp.json()

    watcher = data["watcher_stats"]
    # 2 files done (bao gồm cả file đã retry thành công) → success = 2
    assert watcher["success"] == 2
    # chỉ 1 file thực sự stuck ở failed → failed = 1
    assert watcher["failed"] == 1
    # 1 file từng thất bại nhưng cuối cùng thành công
    assert watcher["retried"] == 1

    # Tỉ lệ thành công = 2/(2+1) = 66.7% cho trường hợp này
    # Nhưng quan trọng: file retried_ok.xlsx KHÔNG bị tính là failed
    success_files = data["success_files"]
    failed_files = data["failed_files"]
    assert success_files >= 2  # ít nhất 2 watcher success
    assert failed_files >= 1  # ít nhất 1 watcher failed


def test_metrics_all_retried_success_gives_100_pct(settings, api_client):
    """Nếu tất cả file cuối cùng thành công → watcher failed = 0."""
    seen_data = {
        "f1": {
            "name": "a.xlsx",
            "status": "done",
            "past_failures": 0,
            "failures": 0,
            "processed_at": "2026-07-10T10:00:00Z",
            "total_rows": 10,
        },
        "f2": {
            "name": "b.xlsx",
            "status": "done",
            "past_failures": 3,
            "failures": 0,
            "processed_at": "2026-07-11T10:00:00Z",
            "total_rows": 20,
        },
        "f3": {
            "name": "c.xlsx",
            "status": "done",
            "past_failures": 1,
            "failures": 0,
            "processed_at": "2026-07-12T10:00:00Z",
            "total_rows": 15,
        },
    }
    (settings.work_dir / "seen_files.json").write_text(json.dumps(seen_data), encoding="utf-8")

    resp = api_client.get("/api/metrics")
    assert resp.status_code == 200
    data = resp.json()

    watcher = data["watcher_stats"]
    assert watcher["success"] == 3
    assert watcher["failed"] == 0  # không file nào stuck
    assert watcher["retried"] == 2  # 2 file từng thất bại nhưng thành công


# ---------- GET /api/metrics/daily (task 9.8) ----------


def test_daily_metrics_includes_success_and_failed_counts(settings, api_client, monkeypatch):
    """Daily response includes success_counts and failed_counts arrays.

    Updated to use SQLite (job_store) since get_daily_metrics now reads from SQLite only.
    """
    import uuid as _uuid

    from dms.classification_jobs import ClassificationJobStore

    db_path = settings.work_dir / "classification_jobs.db"
    job_store = ClassificationJobStore(db_path)

    # Patch get_classification_job_store to return our test job_store
    monkeypatch.setattr(
        "dms.web.api.metrics_api.get_classification_job_store",
        lambda: job_store,
        raising=False,
    )
    monkeypatch.setattr(
        "dms.web.deps.get_classification_job_store",
        lambda: job_store,
        raising=False,
    )

    def _make_completed(filename: str, completed_at: str):
        jid = str(_uuid.uuid4())
        job_store.create_job(
            job_id=jid,
            owner_username="system_watcher",
            owner_role="system",
            filename=filename,
            mode="watcher",
            input_path=f"/input/{filename}",
            output_path=f"/output/{filename}",
        )
        with job_store._lock, job_store._conn() as conn:
            conn.execute(
                """UPDATE classification_jobs
                   SET status = 'completed', total_rows = 10, rows_done = 10, percent = 100,
                       completed_at = ?, updated_at = ?
                   WHERE job_id = ?""",
                (completed_at, completed_at, jid),
            )
            conn.commit()

    def _make_failed(filename: str, completed_at: str):
        jid = str(_uuid.uuid4())
        job_store.create_job(
            job_id=jid,
            owner_username="system_watcher",
            owner_role="system",
            filename=filename,
            mode="watcher",
            input_path=f"/input/{filename}",
            output_path=f"/output/{filename}",
        )
        with job_store._lock, job_store._conn() as conn:
            conn.execute(
                """UPDATE classification_jobs
                   SET status = 'error', error = 'Test failure',
                       completed_at = ?, updated_at = ?
                   WHERE job_id = ?""",
                (completed_at, completed_at, jid),
            )
            conn.commit()

    _make_completed("a.xlsx", "2026-07-16T10:00:00Z")
    _make_completed("b.xlsx", "2026-07-16T11:00:00Z")
    _make_failed("c.xlsx", "2026-07-16T14:00:00Z")
    _make_completed("d.xlsx", "2026-07-17T09:00:00Z")

    resp = api_client.get("/api/metrics/daily")
    assert resp.status_code == 200
    data = resp.json()

    assert "dates" in data
    assert "counts" in data
    assert "success_counts" in data
    assert "failed_counts" in data

    # Verify arrays are the same length
    assert len(data["dates"]) == len(data["success_counts"])
    assert len(data["dates"]) == len(data["failed_counts"])
    assert len(data["dates"]) == len(data["counts"])

    # Verify backward compat: counts[i] = success_counts[i] + failed_counts[i]
    for i in range(len(data["dates"])):
        assert data["counts"][i] == data["success_counts"][i] + data["failed_counts"][i]

    # There should be at least one failed count
    assert sum(data["failed_counts"]) >= 1
    # There should be at least some success counts
    assert sum(data["success_counts"]) >= 2
