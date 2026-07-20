from __future__ import annotations

import json
from pathlib import Path

from conftest import TEST_ADMIN, apply_auth_overrides
from fastapi.testclient import TestClient

from dms.user_store import UserStore
from dms.web import deps
from dms.web.app import create_app


def test_user_store_persists_display_name_and_active_state(tmp_path: Path):
    store = UserStore(tmp_path / "users.json", default_admin_password="admin123")

    user = store.create_user(
        "alice",
        "password123",
        "user",
        display_name="Alice Nguyen",
        is_active=False,
    )

    assert user["display_name"] == "Alice Nguyen"
    assert user["is_active"] is False
    assert store.authenticate("alice", "password123") is None

    updated = store.update_user("alice", is_active=True)
    assert updated["is_active"] is True
    assert store.authenticate("alice", "password123")["username"] == "alice"


def test_user_store_persists_only_bcrypt_password_hash(tmp_path: Path):
    db_path = tmp_path / "users.json"
    store = UserStore(db_path, default_admin_password="admin123")
    store.create_user("alice", "password123", "user")
    store.update_user("alice", password="newpass123")

    raw = json.loads(db_path.read_text(encoding="utf-8"))
    alice = next(user for user in raw["users"] if user["username"] == "alice")

    assert "password" not in alice
    assert alice["password_hash"].startswith("$2")
    assert "newpass123" not in db_path.read_text(encoding="utf-8")
    assert store.authenticate("alice", "newpass123")["username"] == "alice"


def test_admin_cannot_disable_or_demote_self(tmp_path: Path, monkeypatch):
    store = UserStore(tmp_path / "users.json", default_admin_password="admin123")
    store.create_user("adminuser", "password123", "admin", is_active=True)
    monkeypatch.setattr(deps, "get_user_store", lambda: store)

    app = create_app()
    apply_auth_overrides(app, admin=TEST_ADMIN, user=TEST_ADMIN)
    client = TestClient(app)

    disable = client.put("/api/users/adminuser", json={"is_active": False})
    demote = client.put("/api/users/adminuser", json={"role": "user"})

    assert disable.status_code == 400
    assert demote.status_code == 400
