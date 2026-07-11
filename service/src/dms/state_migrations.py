"""Runtime state migration utilities."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .state_repository import JsonStateRepository


@dataclass(frozen=True)
class MigrationResult:
    source_path: Path
    backup_path: Path | None
    migrated_count: int
    dry_run: bool = False


def validate_users_state(data: dict[str, Any]) -> None:
    users = data.get("users")
    if not isinstance(users, list):
        raise ValueError("users state must contain a 'users' list")
    seen_usernames: set[str] = set()
    for idx, user in enumerate(users):
        if not isinstance(user, dict):
            raise ValueError(f"users[{idx}] must be an object")
        username = str(user.get("username") or "").strip()
        if not username:
            raise ValueError(f"users[{idx}] missing username")
        if username in seen_usernames:
            raise ValueError(f"duplicate username: {username}")
        seen_usernames.add(username)
        if not user.get("password_hash"):
            raise ValueError(f"user {username!r} missing password_hash")
        if user.get("role") not in {"admin", "user"}:
            raise ValueError(f"user {username!r} has invalid role")


def migrate_users_json(
    source_path: Path,
    *,
    backup_dir: Path | None = None,
    dry_run: bool = False,
) -> MigrationResult:
    """Validate and rewrite users.json through the repository boundary.

    The utility creates a restorable backup before any write. If validation after
    write fails, it restores the original backup and re-raises the failure.
    """
    source_path = Path(source_path)
    repo = JsonStateRepository(
        source_path,
        default_factory=lambda: {"users": []},
        validator=validate_users_state,
    )
    backup_path = repo.backup(backup_dir)
    data = json.loads(source_path.read_text(encoding="utf-8"))
    validate_users_state(data)
    if dry_run:
        return MigrationResult(
            source_path=source_path,
            backup_path=backup_path,
            migrated_count=len(data["users"]),
            dry_run=True,
        )
    try:
        repo.write(data)
        validate_users_state(repo.read())
    except Exception:
        if backup_path is not None:
            shutil.copy2(backup_path, source_path)
        raise
    return MigrationResult(
        source_path=source_path,
        backup_path=backup_path,
        migrated_count=len(data["users"]),
    )
