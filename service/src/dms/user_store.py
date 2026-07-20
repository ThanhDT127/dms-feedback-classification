"""JSON-file-based user storage with bcrypt password hashing."""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime
from pathlib import Path

import bcrypt

from .state_migrations import validate_users_state
from .state_repository import JsonStateRepository

logger = logging.getLogger("dms-auth")


class UserStore:
    """Manages users in a JSON file with bcrypt-hashed passwords.

    Store format::

        {"users": [
            {"username": "...", "password_hash": "...", "role": "...", "created_at": "..."},
            ...
        ]}
    """

    def __init__(self, db_path: Path, default_admin_password: str = "") -> None:
        self.db_path = db_path
        self.repository = JsonStateRepository(
            self.db_path,
            default_factory=lambda: {"users": []},
            validator=validate_users_state,
        )

        # Ensure a default admin user exists when the store is empty.
        users = self._read()["users"]
        if not users:
            admin_password = default_admin_password
            must_change = False
            if not admin_password or admin_password == "admin123":
                admin_password = secrets.token_urlsafe(12)
                must_change = True
                logger.warning(
                    "⚠️ Generated admin password: %s — CHANGE IMMEDIATELY",
                    admin_password,
                )
            hashed = self._hash_password(admin_password)
            users.append(
                {
                    "username": "admin",
                    "display_name": "Admin",
                    "password_hash": hashed,
                    "role": "admin",
                    "is_active": True,
                    "must_change_password": must_change,
                    "created_at": datetime.now(UTC).isoformat(),
                }
            )
            self._write({"users": users})
            logger.warning(
                "Created default admin account (username: admin). Please change the password!"
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read(self) -> dict:
        """Read the JSON store from disk."""
        return self.repository.read()

    def _write(self, data: dict) -> None:
        """Write the JSON store to disk atomically."""
        self.repository.write(data)

    @staticmethod
    def _hash_password(password: str) -> str:
        import hashlib

        # Pre-hash with SHA-256 to handle passwords > 72 bytes (bcrypt limit)
        pw_sha = hashlib.sha256(password.encode("utf-8")).digest()
        return bcrypt.hashpw(pw_sha, bcrypt.gensalt()).decode("utf-8")

    @staticmethod
    def _verify_password(plain_password: str, hashed_password: str) -> bool:
        import hashlib

        pw_sha = hashlib.sha256(plain_password.encode("utf-8")).digest()
        return bcrypt.checkpw(
            pw_sha,
            hashed_password.encode("utf-8"),
        )

    @staticmethod
    def _safe_user(user: dict) -> dict:
        """Return a copy of the user dict without the password hash."""
        safe = {k: v for k, v in user.items() if k != "password_hash"}
        safe.setdefault("display_name", safe.get("username", ""))
        safe.setdefault("is_active", True)
        safe.setdefault("must_change_password", False)
        return safe

    # Dummy bcrypt hash for constant-time comparison when user not found
    _DUMMY_HASH = bcrypt.hashpw(b"dummy", bcrypt.gensalt()).decode("utf-8")

    @classmethod
    def _dummy_verify(cls, password: str) -> None:
        """Perform a dummy bcrypt check to prevent timing-based username enumeration."""
        import hashlib

        pw_sha = hashlib.sha256(password.encode("utf-8")).digest()
        bcrypt.checkpw(pw_sha, cls._DUMMY_HASH.encode("utf-8"))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def authenticate(self, username: str, password: str) -> dict | None:
        """Verify credentials and return the user dict (sans password), or ``None``."""
        data = self._read()
        for user in data["users"]:
            if user["username"] == username:
                if user.get("is_active", True) is False:
                    self._dummy_verify(password)
                    return None
                if self._verify_password(password, user["password_hash"]):
                    return self._safe_user(user)
                return None
        # Constant-time: run a dummy hash to prevent timing-based username enumeration
        self._dummy_verify(password)
        return None

    def get_user(self, username: str) -> dict | None:
        """Look up a user by username, returning ``None`` if not found."""
        data = self._read()
        for user in data["users"]:
            if user["username"] == username:
                return self._safe_user(user)
        return None

    def list_users(self) -> list[dict]:
        """Return all users without their password hashes."""
        data = self._read()
        return [self._safe_user(u) for u in data["users"]]

    def create_user(
        self,
        username: str,
        password: str,
        role: str = "user",
        display_name: str | None = None,
        is_active: bool = True,
    ) -> dict:
        """Create a new user.

        Raises:
            ValueError: If the username already exists.
        """
        data = self._read()
        for user in data["users"]:
            if user["username"] == username:
                raise ValueError(f"Username '{username}' already exists")

        new_user = {
            "username": username,
            "display_name": display_name or username,
            "password_hash": self._hash_password(password),
            "role": role,
            "is_active": bool(is_active),
            "created_at": datetime.now(UTC).isoformat(),
        }
        data["users"].append(new_user)
        self._write(data)
        return self._safe_user(new_user)

    def update_password(self, username: str, new_password: str) -> bool:
        """Update a user's password.  Returns ``True`` on success."""
        data = self._read()
        for user in data["users"]:
            if user["username"] == username:
                user["password_hash"] = self._hash_password(new_password)
                user["must_change_password"] = False
                self._write(data)
                return True
        return False

    def update_user(self, username: str, **fields) -> dict | None:
        """Update user fields (display_name, role, is_active, password).

        Returns the updated user dict (without hash) or ``None`` if not found.
        """
        data = self._read()
        for user in data["users"]:
            if user["username"] == username:
                if "display_name" in fields:
                    user["display_name"] = fields["display_name"]
                if "role" in fields and fields["role"] in ("admin", "user"):
                    user["role"] = fields["role"]
                if "is_active" in fields:
                    user["is_active"] = bool(fields["is_active"])
                if "password" in fields and fields["password"]:
                    user["password_hash"] = self._hash_password(fields["password"])
                    user["must_change_password"] = False
                self._write(data)
                return self._safe_user(user)
        return None

    def delete_user(self, username: str) -> bool:
        """Delete a user by username.  Returns ``True`` if deleted."""
        data = self._read()
        original_len = len(data["users"])
        data["users"] = [u for u in data["users"] if u["username"] != username]
        if len(data["users"]) < original_len:
            self._write(data)
            return True
        return False
