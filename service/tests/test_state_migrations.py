from __future__ import annotations

import json

import pytest

from dms.state_migrations import migrate_users_json
from dms.state_repository import JsonStateRepository
from dms.user_store import UserStore


def _users_payload():
    return {
        "users": [
            {
                "username": "admin",
                "display_name": "Admin",
                "password_hash": "$2b$12$abcdefghijklmnopqrstuvwxyzABCDEFGHijklmno",
                "role": "admin",
                "is_active": True,
                "created_at": "2026-07-11T00:00:00+00:00",
            }
        ]
    }


def test_users_json_migration_success_creates_backup(tmp_path):
    users_path = tmp_path / "users.json"
    users_path.write_text(json.dumps(_users_payload()), encoding="utf-8")

    result = migrate_users_json(users_path, backup_dir=tmp_path / "backups")

    assert result.migrated_count == 1
    assert result.backup_path is not None
    assert result.backup_path.exists()
    assert json.loads(users_path.read_text(encoding="utf-8"))["users"][0]["username"] == "admin"


def test_users_json_migration_invalid_source_preserves_backup(tmp_path):
    users_path = tmp_path / "users.json"
    users_path.write_text(
        json.dumps({"users": [{"username": "broken", "role": "admin"}]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        migrate_users_json(users_path, backup_dir=tmp_path / "backups")

    backups = list((tmp_path / "backups").glob("users.json.*.bak"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8"))["users"][0]["username"] == "broken"


def test_users_json_migration_rolls_back_when_post_write_validation_fails(tmp_path, monkeypatch):
    users_path = tmp_path / "users.json"
    original = _users_payload()
    users_path.write_text(json.dumps(original), encoding="utf-8")
    original_read = JsonStateRepository.read
    calls = {"count": 0}

    def fail_after_write(self):
        calls["count"] += 1
        if calls["count"] >= 1:
            raise ValueError("post-write validation failed")
        return original_read(self)

    monkeypatch.setattr(JsonStateRepository, "read", fail_after_write)

    with pytest.raises(ValueError):
        migrate_users_json(users_path, backup_dir=tmp_path / "backups")

    assert json.loads(users_path.read_text(encoding="utf-8")) == original


def test_user_store_reads_existing_users_json_through_repository(tmp_path):
    users_path = tmp_path / "users.json"
    users_path.write_text(json.dumps(_users_payload()), encoding="utf-8")

    store = UserStore(users_path, default_admin_password="unused")

    users = store.list_users()
    assert users == [
        {
            "username": "admin",
            "display_name": "Admin",
            "role": "admin",
            "is_active": True,
            "created_at": "2026-07-11T00:00:00+00:00",
            "must_change_password": False,
        }
    ]
