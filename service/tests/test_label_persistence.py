from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import TEST_ADMIN, apply_auth_overrides
from dms.settings import Settings
from dms.web import deps
from dms.web.app import create_app


def test_label_update_persists_and_records_admin_user(tmp_path: Path, monkeypatch):
    settings = Settings(
        azure_tenant_id="tenant",
        azure_client_id="client",
        azure_client_secret="secret",
        sharepoint_drive_id="drive",
        sharepoint_root_folder_id="root",
        gemini_backend="vertex",
        gcp_project_id="project",
        data_dir=tmp_path,
        work_dir=tmp_path / "work",
        log_dir=tmp_path / "logs",
    )
    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    deps.reset()

    app = create_app()
    apply_auth_overrides(app, user=TEST_ADMIN, admin=TEST_ADMIN)
    client = TestClient(app)

    current = client.get("/api/pipeline/labels").json()
    payload = {
        "label_definitions": {
            **current["label_definitions"],
            "Tin trung lập": "Updated neutral definition",
        },
        "minor_order": current["minor_order"],
        "minor_to_major": current["minor_to_major"],
    }

    response = client.put("/api/pipeline/labels", json=payload)

    assert response.status_code == 200
    saved = json.loads(settings.label_config_path.read_text(encoding="utf-8"))
    assert saved["label_definitions"]["Tin trung lập"] == "Updated neutral definition"

    history = client.get("/api/pipeline/labels/history").json()
    assert history["items"]
    assert history["items"][0]["user"] == TEST_ADMIN["username"]
