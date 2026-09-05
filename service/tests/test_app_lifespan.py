from __future__ import annotations

from fastapi.testclient import TestClient

from dms.web import app as app_module


def test_app_lifespan_replaces_on_event_handlers(monkeypatch):
    calls: list[str] = []

    async def fake_restore_state() -> None:
        calls.append("restore")

    async def fake_sync() -> None:
        calls.append("sync")

    monkeypatch.setattr(app_module, "_restore_state_from_sharepoint", fake_restore_state)
    monkeypatch.setattr(app_module, "_start_classification_worker", lambda: calls.append("start"))
    monkeypatch.setattr(app_module, "_stop_classification_worker", lambda: calls.append("stop"))
    monkeypatch.setattr(app_module, "_sync_sharepoint_in_background", fake_sync)

    app = app_module.create_app()
    assert app.router.on_startup == []
    assert app.router.on_shutdown == []

    with TestClient(app) as client:
        assert client.get("/").status_code in (200, 404)
        assert "restore" in calls
        assert "start" in calls

    assert "stop" in calls
