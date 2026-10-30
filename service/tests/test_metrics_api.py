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


def test_usage_tracker_backfills_gateway_model_cost_and_user_stats(settings, monkeypatch):
    """Gateway model usage logs with zero cost are backfilled and reflected in user & usage stats."""
    import json as _json

    settings.ensure_runtime_dirs()
    from dms.classification_jobs import ClassificationJobStore
    from dms.usage_tracker import DEFAULT_MODEL_PRICING, UsageTracker, calculate_cost

    pricing_map = dict(DEFAULT_MODEL_PRICING)
    pricing_map.update(_json.loads(settings.gemini_model_pricing))

    job_db = settings.work_dir / "classification_jobs.db"
    store = ClassificationJobStore(job_db)
    job_id = str(uuid.uuid4())
    store.create_job(
        job_id=job_id,
        owner_username="alice",
        owner_role="admin",
        filename="gateway_test.xlsx",
        mode="classify",
        input_path="/uploads/gateway_test.xlsx",
        output_path="/output/gateway_test.xlsx",
    )
    store.complete_job(
        job_id,
        total_rows=20,
        rows_done=20,
        output_path="/output/gateway_test.xlsx",
        duration_seconds=2.5,
    )

    tracker = UsageTracker(job_db)
    # Simulate a gateway attempt recorded before pricing fix (estimated_cost_usd=None)
    tracker.record_attempt(
        event_kind="attempt_finished",
        operation_id="op-1",
        attempt_id="att-1",
        actor="alice",
        job_id=job_id,
        route="gateway",
        incident_id=None,
        model_requested="gemini-flash-lite",
        model_actual="gemini-flash-lite",
        outcome="success",
        usage={"prompt_tokens": 100_000, "completion_tokens": 20_000, "total_tokens": 120_000},
        estimated_cost_usd=None,
        call_type="issue_classify",
        duration_ms=250,
    )

    updated = tracker.backfill_missing_costs(pricing_map)
    assert updated == 1

    expected_cost = calculate_cost("gemini-flash-lite", 100_000, 20_000, pricing_map)
    assert expected_cost > 0

    usage_summary = tracker.query_usage("day", include_partial=True)
    assert usage_summary["summary"]["total_tokens"] == 120_000
    assert usage_summary["summary"]["total_cost_usd"] == pytest.approx(expected_cost, rel=1e-4)

    user_stats = store.aggregate_stats_by_user()
    alice = next((u for u in user_stats if u["username"] == "alice"), None)
    assert alice is not None
    assert alice["total_tokens"] == 120_000
    assert alice["estimated_cost"] == pytest.approx(expected_cost, rel=1e-4)


def test_reset_failed_retries_web_upload_job_id(settings, monkeypatch):
    """POST /api/metrics/reset-failed with job:<job_id> re-queues failed web upload job."""
    settings.ensure_runtime_dirs()
    from dms.classification_jobs import ClassificationJobStore
    from dms.web import deps

    input_file = settings.work_dir / "failed_upload.xlsx"
    input_file.write_bytes(b"dummy")

    store = ClassificationJobStore(settings.work_dir / "classification_jobs.db")
    job_id = str(uuid.uuid4())
    store.create_job(
        job_id=job_id,
        owner_username="admin",
        owner_role="admin",
        filename="failed_upload.xlsx",
        mode="file",
        input_path=input_file,
        output_path=settings.work_dir / "output.xlsx",
    )
    store.fail_job(job_id, "Gateway timeout")

    monkeypatch.setattr("dms.web.api.metrics_api._work_dir", lambda: settings.work_dir)
    monkeypatch.setattr(deps, "get_classification_job_store", lambda: store)
    monkeypatch.setattr(deps, "get_classification_worker_manager", lambda: None)

    app = create_app()
    apply_auth_overrides(app)
    client = TestClient(app)

    resp = client.post("/api/metrics/reset-failed", json={"file_ids": [f"job:{job_id}"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reset_count"] == 1
    assert job_id in body["retried_jobs"]
    assert store.get_job(job_id)["status"] == "queued"


# ---------- Canonical version & Health endpoints ----------


def test_canonical_version_matches_pyproject_toml(settings, monkeypatch):
    """Canonical version from get_version() matches pyproject.toml across backend layers."""
    import tomllib
    from importlib.metadata import PackageNotFoundError
    from pathlib import Path

    import dms
    from dms import get_version
    from dms.metrics import MetricsCollector

    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    expected_version = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))["project"][
        "version"
    ]

    get_version.cache_clear()
    assert get_version() == expected_version
    assert create_app().version == expected_version

    collector = MetricsCollector(settings.work_dir / "metrics.json")
    health_payload = collector.get_health_data(cycle=1, queue_size=0)
    assert health_payload["version"] == expected_version

    # Verify tomllib fallback when package metadata is not installed
    get_version.cache_clear()
    monkeypatch.setattr(
        dms,
        "_package_version",
        lambda _name: (_ for _ in ()).throw(PackageNotFoundError("dms-feedback-classifier")),
    )
    assert get_version() == expected_version
    get_version.cache_clear()


def test_health_endpoint_includes_canonical_version_with_and_without_health_file(
    settings, api_client
):
    """Authenticated GET /api/health always returns canonical version."""
    from dms import get_version

    # 1. Without health.json on disk
    resp_missing = api_client.get("/api/health")
    assert resp_missing.status_code == 200
    body_missing = resp_missing.json()
    assert body_missing["status"] == "unknown"
    assert body_missing["version"] == get_version()

    # 2. With health.json on disk lacking version field
    (settings.work_dir / "health.json").write_text(
        json.dumps({"status": "ok", "uptime": "3h 10m"}),
        encoding="utf-8",
    )
    resp_present = api_client.get("/api/health")
    assert resp_present.status_code == 200
    body_present = resp_present.json()
    assert body_present["status"] == "ok"
    assert body_present["version"] == get_version()


def test_public_health_endpoint_works_unauthenticated_and_filters_sensitive_fields(
    settings, monkeypatch
):
    """GET /api/health/public works without JWT and exposes strictly {status, version}."""
    from dms import get_version

    settings.ensure_runtime_dirs()
    monkeypatch.setattr("dms.web.api.metrics_api._work_dir", lambda: settings.work_dir)
    app = create_app()
    unauth_client = TestClient(app)

    # Authenticated /api/health must still reject unauthenticated requests
    assert unauth_client.get("/api/health").status_code == 401

    # 1. Missing health.json -> web-only ready ("ok") + canonical version
    r_fresh = unauth_client.get("/api/health/public")
    assert r_fresh.status_code == 200
    assert r_fresh.json() == {"status": "ok", "version": get_version()}
    assert "X-RateLimit-Limit" in r_fresh.headers

    # 2. health.json with internal operational details -> zero info leak
    (settings.work_dir / "health.json").write_text(
        json.dumps(
            {
                "status": "degraded",
                "last_success": "Confidential_Customer_Q3.xlsx (120 rows, 4.1s)",
                "last_error": "GatewayError: upstream secret host timeout",
                "model": "gemini-2.5-flash-lite",
                "metrics_summary": {"processed_24h": 12, "failed_24h": 3, "success_rate": "80%"},
                "config_assets": {"status": "error", "errors": ["internal path"]},
            }
        ),
        encoding="utf-8",
    )
    r_degraded = unauth_client.get("/api/health/public")
    assert r_degraded.status_code == 200
    payload = r_degraded.json()
    assert set(payload.keys()) == {"status", "version"}
    assert payload == {"status": "degraded", "version": get_version()}
    for forbidden in ("last_success", "last_error", "model", "metrics_summary", "config_assets"):
        assert forbidden not in payload

    # 3. health.json with error status
    (settings.work_dir / "health.json").write_text(
        json.dumps({"status": "error"}),
        encoding="utf-8",
    )
    r_error = unauth_client.get("/api/health/public")
    assert r_error.status_code == 200
    assert r_error.json() == {"status": "error", "version": get_version()}
