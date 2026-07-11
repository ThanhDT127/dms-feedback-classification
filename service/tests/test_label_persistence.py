from __future__ import annotations

import json
import threading
from pathlib import Path

from conftest import TEST_ADMIN, apply_auth_overrides
from fastapi.testclient import TestClient

from dms.pipeline.issue_classifier import (
    get_label_config_snapshot,
    publish_label_config,
)
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


def test_label_publish_never_exposes_empty_config():
    base = get_label_config_snapshot()
    seen_empty: list[dict] = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            snapshot = get_label_config_snapshot()
            if (
                not snapshot["minor_order"]
                or not snapshot["label_definitions"]
                or not snapshot["minor_to_major"]
            ):
                seen_empty.append(snapshot)

    thread = threading.Thread(target=reader)
    thread.start()
    try:
        for idx in range(100):
            payload = {
                "minor_order": list(base["minor_order"]),
                "minor_to_major": dict(base["minor_to_major"]),
                "label_definitions": {
                    **base["label_definitions"],
                    base["minor_order"][0]: f"definition {idx}",
                },
            }
            publish_label_config(payload)
    finally:
        stop.set()
        thread.join(timeout=5)
        publish_label_config(base)

    assert seen_empty == []


def test_invalid_label_payload_does_not_modify_active_config():
    before = get_label_config_snapshot()
    invalid = {
        "minor_order": [],
        "minor_to_major": {},
        "label_definitions": {},
    }

    try:
        publish_label_config(invalid)
    except ValueError:
        pass

    assert get_label_config_snapshot() == before
