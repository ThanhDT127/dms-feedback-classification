from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from dms.jwt_utils import create_token
from dms.settings import Settings
from dms.web import deps
from dms.web.api import classify as classify_module
from dms.web.app import create_app
from dms.web.deps import get_current_user

ALICE = {"username": "alice", "display_name": "Alice", "role": "user", "is_active": True}
BOB = {"username": "bob", "display_name": "Bob", "role": "user", "is_active": True}
ADMIN = {"username": "admin", "display_name": "Admin", "role": "admin", "is_active": True}


class FakeRunner:
    def run_pipeline(
        self,
        input_path,
        output_path,
        ckpt_path,
        progress_callback=None,
        cancellation_check=None,
        job_id=None,
    ):
        if cancellation_check and cancellation_check():
            raise RuntimeError("cancelled")
        if progress_callback:
            progress_callback(
                done=1,
                total=2,
                new_results=[{"text": "alpha", "labels": ["Bao loi"]}],
                step=3,
                step_status="running",
            )
            progress_callback(
                done=2,
                total=2,
                new_results=[{"text": "beta", "labels": ["Bao hanh"]}],
                step=3,
                step_status="done",
            )
        Path(output_path).write_bytes(b"fake-xlsx")
        return {
            "total_rows": 2,
            "processed_rows": 2,
            "output_path": str(output_path),
            "duration_seconds": 0.1,
        }


class FakeUserStore:
    def get_user(self, username: str):
        return {"alice": ALICE, "bob": BOB, "admin": ADMIN}.get(username)


def _settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "azure_tenant_id": "tenant",
        "azure_client_id": "client",
        "azure_client_secret": "secret",
        "sharepoint_drive_id": "drive",
        "sharepoint_root_folder_id": "root",
        "gemini_backend": "vertex",
        "gcp_project_id": "project",
        "data_dir": tmp_path / "data",
        "work_dir": tmp_path / "work",
        "log_dir": tmp_path / "logs",
        "jwt_secret_key": "x" * 40,
        "classification_worker_poll_interval_seconds": 0.01,
        "classification_worker_heartbeat_seconds": 0.01,
    }
    values.update(overrides)
    return Settings(
        **values,
    )


def _client(tmp_path: Path, monkeypatch, user: dict = ALICE, **settings_overrides) -> TestClient:
    deps.reset()
    settings = _settings(tmp_path, **settings_overrides)
    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    monkeypatch.setattr(deps, "get_pipeline_runner", lambda: FakeRunner())
    monkeypatch.setattr(deps, "get_sharepoint_client", lambda: None)
    monkeypatch.setattr(deps, "get_user_store", lambda: FakeUserStore())
    # Redirect WORK_DIR to tmp_path so output files don't pollute the real work/output/
    monkeypatch.setattr(classify_module, "WORK_DIR", tmp_path / "work")
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _wait_for_completed(client: TestClient, job_id: str) -> dict:
    for _ in range(50):
        job = client.get(f"/api/classify/jobs/{job_id}").json()
        if job["status"] == "completed":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not complete")


def test_classify_file_creates_durable_owner_scoped_job(tmp_path: Path, monkeypatch):
    client = _client(tmp_path, monkeypatch, ALICE)

    response = client.post(
        "/api/classify/file",
        files={
            "file": (
                "feedback.xlsx",
                b"fake",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        data={"mode": "single"},
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    job = _wait_for_completed(client, job_id)

    assert job["owner_username"] == "alice"
    assert job["rows_done"] == 2
    assert job["total_rows"] == 2
    assert len(job["results"]) == 2

    assert [j["job_id"] for j in client.get("/api/classify/jobs").json()] == [job_id]

    client.app.dependency_overrides[get_current_user] = lambda: BOB
    assert client.get("/api/classify/jobs").json() == []
    assert client.get(f"/api/classify/jobs/{job_id}").status_code == 404
    assert client.get(f"/api/classify/jobs/{job_id}/download").status_code == 404

    client.app.dependency_overrides[get_current_user] = lambda: ADMIN
    assert client.get(f"/api/classify/jobs/{job_id}").status_code == 200
    download = client.get(f"/api/classify/jobs/{job_id}/download")
    assert download.status_code == 200
    assert download.content == b"fake-xlsx"


def test_classification_job_survives_dependency_reset(tmp_path: Path, monkeypatch):
    client = _client(tmp_path, monkeypatch, ALICE)

    response = client.post(
        "/api/classify/file",
        files={
            "file": (
                "feedback.xlsx",
                b"fake",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        data={"mode": "single"},
    )
    job_id = response.json()["job_id"]
    _wait_for_completed(client, job_id)

    deps.reset()
    restored = client.get(f"/api/classify/jobs/{job_id}")
    assert restored.status_code == 200
    assert restored.json()["status"] == "completed"
    assert restored.json()["results"][0]["text"] == "alpha"


def test_classify_websocket_streams_durable_results(tmp_path: Path, monkeypatch):
    client = _client(tmp_path, monkeypatch, ALICE)
    store = deps.get_classification_job_store()
    output_path = tmp_path / "work" / "output" / "out.xlsx"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"fake-xlsx")
    store.create_job(
        job_id="ws-job",
        owner_username="alice",
        owner_role="user",
        filename="feedback.xlsx",
        mode="single",
        input_path=tmp_path / "in.xlsx",
        output_path=output_path,
    )
    store.mark_running("ws-job")
    store.update_progress("ws-job", done=1, total=2, step=3, step_status="running")
    store.append_results("ws-job", [{"text": "alpha", "labels": ["Bao loi"]}])

    token = create_token("alice", "access", _settings(tmp_path).jwt_secret_key)
    with client.websocket_connect(f"/ws/classify/ws-job?token={token}") as websocket:
        first = websocket.receive_json()
        second = websocket.receive_json()

    assert first["type"] == "batch_result"
    assert first["data"]["results"][0]["text"] == "alpha"
    assert second["type"] == "progress"
    assert second["data"]["rows_done"] == 1


def test_admin_metrics_cancel_and_retry_authorization(tmp_path: Path, monkeypatch):
    client = _client(tmp_path, monkeypatch, ALICE)
    store = deps.get_classification_job_store()

    input_path = tmp_path / "work" / "input" / "retry.xlsx"
    output_path = tmp_path / "work" / "output" / "retry_out.xlsx"
    input_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_bytes(b"fake-xlsx")

    store.create_job(
        job_id="retry-job",
        owner_username="alice",
        owner_role="user",
        filename="retry.xlsx",
        mode="single",
        input_path=input_path,
        output_path=output_path,
    )
    store.fail_job("retry-job", "temporary failure")

    store.create_job(
        job_id="cancel-job",
        owner_username="bob",
        owner_role="user",
        filename="cancel.xlsx",
        mode="single",
        input_path=tmp_path / "cancel.xlsx",
        output_path=tmp_path / "cancel_out.xlsx",
    )

    assert client.get("/api/classify/jobs/metrics").status_code == 403
    assert client.post("/api/classify/jobs/retry-job/retry").status_code == 403

    client.app.dependency_overrides[get_current_user] = lambda: ADMIN
    metrics = client.get("/api/classify/jobs/metrics")
    assert metrics.status_code == 200
    assert metrics.json()["counts"]["failed"] == 1

    cancelled = client.delete("/api/classify/jobs/cancel-job")
    assert cancelled.status_code == 200
    assert store.get_job("cancel-job", include_results=False)["status"] == "cancelled"

    retry = client.post("/api/classify/jobs/retry-job/retry")
    assert retry.status_code == 200
    retried_job = _wait_for_completed(client, "retry-job")
    assert retried_job["retry_count"] == 1
    assert retried_job["status"] == "completed"


def test_admin_retry_rejects_missing_input(tmp_path: Path, monkeypatch):
    client = _client(tmp_path, monkeypatch, ADMIN)
    store = deps.get_classification_job_store()
    store.create_job(
        job_id="missing-input",
        owner_username="alice",
        owner_role="user",
        filename="missing.xlsx",
        mode="single",
        input_path=tmp_path / "missing.xlsx",
        output_path=tmp_path / "out.xlsx",
    )
    store.fail_job("missing-input", "temporary failure")

    response = client.post("/api/classify/jobs/missing-input/retry")
    assert response.status_code == 404
    assert response.json()["detail"] == "Input file does not exist"


def test_classify_upload_rejects_per_user_queue_limit(tmp_path: Path, monkeypatch):
    client = _client(
        tmp_path,
        monkeypatch,
        ALICE,
        classification_per_user_queued_limit=1,
        classification_per_user_running_limit=10,
    )
    store = deps.get_classification_job_store()
    store.create_job(
        job_id="queued-limit",
        owner_username="alice",
        owner_role="user",
        filename="queued.xlsx",
        mode="single",
        input_path=tmp_path / "queued.xlsx",
        output_path=tmp_path / "queued_out.xlsx",
    )

    response = client.post(
        "/api/classify/file",
        files={
            "file": (
                "feedback.xlsx",
                b"fake",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        data={"mode": "single"},
    )

    assert response.status_code == 429
    assert "giới hạn job đang chờ" in response.json()["detail"]


def test_classify_upload_rejects_per_user_running_limit(tmp_path: Path, monkeypatch):
    client = _client(
        tmp_path,
        monkeypatch,
        ALICE,
        classification_per_user_running_limit=1,
        classification_per_user_queued_limit=10,
    )
    store = deps.get_classification_job_store()
    store.create_job(
        job_id="running-limit",
        owner_username="alice",
        owner_role="user",
        filename="running.xlsx",
        mode="single",
        input_path=tmp_path / "running.xlsx",
        output_path=tmp_path / "running_out.xlsx",
    )
    store.mark_running("running-limit")

    response = client.post(
        "/api/classify/file",
        files={
            "file": (
                "feedback.xlsx",
                b"fake",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        data={"mode": "single"},
    )

    assert response.status_code == 429
    assert "giới hạn job phân loại đang chạy" in response.json()["detail"]
