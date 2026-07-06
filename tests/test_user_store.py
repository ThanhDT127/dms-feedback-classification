"""Tests for UserStore CRUD operations."""
import pytest
from pathlib import Path
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "service" / "src"))


@pytest.fixture
def user_store():
    """Create a temporary UserStore for testing."""
    from dms.user_store import UserStore

    with tempfile.TemporaryDirectory() as tmpdir:
        store = UserStore(Path(tmpdir) / "test_users.json", default_admin_password="admin123")
        # Clear the default admin user so tests start clean
        data = store._read()
        data["users"] = []
        store._write(data)
        yield store


def test_create_user(user_store):
    user = user_store.create_user("testuser", "pass123", "user")
    assert user["username"] == "testuser"
    assert user["role"] == "user"
    assert "password_hash" not in user
    assert "created_at" in user


def test_create_duplicate_user(user_store):
    user_store.create_user("testuser", "pass123", "user")
    with pytest.raises(ValueError, match="already exists"):
        user_store.create_user("testuser", "pass456", "user")


def test_get_user(user_store):
    user_store.create_user("testuser", "pass123", "user")
    user = user_store.get_user("testuser")
    assert user is not None
    assert user["username"] == "testuser"
    assert "password_hash" not in user


def test_get_user_not_found(user_store):
    assert user_store.get_user("nonexistent") is None


def test_list_users(user_store):
    user_store.create_user("user1", "pass1", "user")
    user_store.create_user("user2", "pass2", "admin")
    users = user_store.list_users()
    assert len(users) == 2
    assert all("password_hash" not in u for u in users)


def test_authenticate_success(user_store):
    user_store.create_user("testuser", "correct_password", "user")
    user = user_store.authenticate("testuser", "correct_password")
    assert user is not None
    assert user["username"] == "testuser"
    assert "password_hash" not in user


def test_authenticate_wrong_password(user_store):
    user_store.create_user("testuser", "correct_password", "user")
    assert user_store.authenticate("testuser", "wrong_password") is None


def test_authenticate_nonexistent_user(user_store):
    assert user_store.authenticate("nonexistent", "password") is None


def test_update_password(user_store):
    user_store.create_user("testuser", "oldpass", "user")
    assert user_store.update_password("testuser", "newpass") is True
    # Old password should fail
    assert user_store.authenticate("testuser", "oldpass") is None
    # New password should work
    assert user_store.authenticate("testuser", "newpass") is not None


def test_update_password_nonexistent(user_store):
    assert user_store.update_password("nonexistent", "newpass") is False


def test_delete_user(user_store):
    user_store.create_user("testuser", "pass123", "user")
    assert user_store.delete_user("testuser") is True
    assert user_store.get_user("testuser") is None


def test_delete_nonexistent_user(user_store):
    assert user_store.delete_user("nonexistent") is False


def test_default_admin_created():
    """UserStore should create a default admin on init if store is empty."""
    with tempfile.TemporaryDirectory() as tmpdir:
        from dms.user_store import UserStore
        store = UserStore(Path(tmpdir) / "test_users.json", default_admin_password="admin123")
        users = store.list_users()
        assert len(users) == 1
        assert users[0]["username"] == "admin"
        assert users[0]["role"] == "admin"


def test_default_admin_idempotent():
    """Creating UserStore again shouldn't duplicate the admin user."""
    with tempfile.TemporaryDirectory() as tmpdir:
        from dms.user_store import UserStore
        db_path = Path(tmpdir) / "test_users.json"
        UserStore(db_path, default_admin_password="admin123")
        store2 = UserStore(db_path, default_admin_password="admin123")
        users = store2.list_users()
        assert len(users) == 1
