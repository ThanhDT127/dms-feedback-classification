from __future__ import annotations

from fastapi.testclient import TestClient

from conftest import TEST_USER
from dms.web.app import create_app
from dms.web.deps import get_current_user


def test_admin_endpoint_requires_authentication():
    app = create_app()
    client = TestClient(app)

    response = client.get("/api/settings")

    assert response.status_code == 401


def test_admin_endpoint_rejects_non_admin_user():
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: TEST_USER
    client = TestClient(app)

    response = client.get("/api/settings")

    assert response.status_code == 403
