"""Unit tests for ScopePolicy and UserStore unit_ids extension."""

from __future__ import annotations

import pytest

from dms.chat.auth.scope_policy import ScopePolicy
from dms.chat.contract import UserScope
from dms.user_store import UserStore


class TestUserStoreUnitIds:
    """Kiểm tra mở rộng trường unit_ids trong UserStore."""

    def test_create_user_with_unit_ids(self, tmp_path):
        db_path = tmp_path / "users.json"
        store = UserStore(db_path=db_path, default_admin_password="test_password_123")

        user = store.create_user(
            username="saleman_mn",
            password="secure_password_1",
            role="user",
            display_name="Sale Miền Nam",
            unit_ids=["CN Miền Nam", "CN Cần Thơ"],
        )
        assert user["username"] == "saleman_mn"
        assert user["unit_ids"] == ["CN Miền Nam", "CN Cần Thơ"]

        # Fetch lại từ store
        fetched = store.get_user("saleman_mn")
        assert fetched is not None
        assert fetched["unit_ids"] == ["CN Miền Nam", "CN Cần Thơ"]

    def test_update_user_unit_ids(self, tmp_path):
        db_path = tmp_path / "users.json"
        store = UserStore(db_path=db_path, default_admin_password="test_password_123")

        store.create_user(
            username="saleman_mb",
            password="secure_password_2",
            role="user",
            unit_ids=["CN Miền Bắc"],
        )

        updated = store.update_user("saleman_mb", unit_ids=["CN Miền Bắc", "CN Hà Nội"])
        assert updated is not None
        assert updated["unit_ids"] == ["CN Miền Bắc", "CN Hà Nội"]

        fetched = store.get_user("saleman_mb")
        assert fetched["unit_ids"] == ["CN Miền Bắc", "CN Hà Nội"]

    def test_legacy_user_without_unit_ids_defaults_to_empty_list(self, tmp_path):
        """Tài khoản cũ chưa có unit_ids phải mặc định trả về list rỗng."""
        db_path = tmp_path / "users.json"
        store = UserStore(db_path=db_path, default_admin_password="test_password_123")

        # Admin mặc định
        admin = store.get_user("admin")
        assert admin is not None
        assert admin["unit_ids"] == []


class TestScopePolicy:
    """Kiểm tra logic phân quyền của ScopePolicy."""

    def test_extract_scope_admin(self):
        admin_dict = {
            "username": "super_admin",
            "role": "admin",
            "display_name": "Quản trị viên",
        }
        scope = ScopePolicy.extract_scope(admin_dict)
        assert scope.is_admin is True
        assert scope.unit_ids == []
        assert "toàn quyền" in scope.scope_description

    def test_extract_scope_user(self):
        user_dict = {
            "username": "sale_hanoi",
            "role": "user",
            "display_name": "Sale Hà Nội",
            "unit_ids": ["CN Hà Nội", "  CN Miền Bắc  "],
        }
        scope = ScopePolicy.extract_scope(user_dict)
        assert scope.is_admin is False
        assert scope.unit_ids == ["CN Hà Nội", "CN Miền Bắc"]

    def test_extract_scope_anonymous_or_none(self):
        scope = ScopePolicy.extract_scope(None)
        assert scope.is_admin is False
        assert scope.unit_ids == []

    def test_enforce_scope_on_params_admin(self):
        admin_scope = UserScope(username="admin", role="admin")
        params = {"date_from": "2026-08-01", "unit_name": "CN Đà Nẵng"}
        scoped = ScopePolicy.enforce_scope_on_params(params, admin_scope)
        # Admin giữ nguyên mọi tham số
        assert scoped["unit_name"] == "CN Đà Nẵng"

    def test_enforce_scope_on_params_user_single_unit_auto_fills(self):
        scope = UserScope(username="user1", role="user", unit_ids=["CN Miền Nam"])
        params = {"date_from": "2026-08-01"}
        scoped = ScopePolicy.enforce_scope_on_params(params, scope)
        assert scoped["unit_name"] == "CN Miền Nam"

    def test_enforce_scope_on_params_user_allowed_unit(self):
        scope = UserScope(username="user1", role="user", unit_ids=["CN Miền Nam", "CN Cần Thơ"])
        params = {"date_from": "2026-08-01", "unit_name": "CN Cần Thơ"}
        scoped = ScopePolicy.enforce_scope_on_params(params, scope)
        assert scoped["unit_name"] == "CN Cần Thơ"

    def test_enforce_scope_on_params_user_forbidden_unit_raises(self):
        scope = UserScope(username="user1", role="user", unit_ids=["CN Miền Nam"])
        params = {"date_from": "2026-08-01", "unit_name": "CN Miền Bắc"}
        with pytest.raises(PermissionError) as exc:
            ScopePolicy.enforce_scope_on_params(params, scope)
        assert "Bạn không có quyền xem dữ liệu của đơn vị 'CN Miền Bắc'" in str(exc.value)

    def test_enforce_scope_on_params_user_without_units_raises(self):
        scope = UserScope(username="unassigned_user", role="user", unit_ids=[])
        params = {"date_from": "2026-08-01"}
        with pytest.raises(PermissionError) as exc:
            ScopePolicy.enforce_scope_on_params(params, scope)
        assert "chưa được phân công đơn vị nào" in str(exc.value)

    def test_enforce_sql_where_admin(self):
        admin_scope = UserScope(username="admin", role="admin")
        sql = "SELECT * FROM v_issues_current WHERE is_active = 1"
        scoped_sql = ScopePolicy.enforce_sql_where(sql, admin_scope)
        assert scoped_sql == sql

    def test_enforce_sql_where_user(self):
        scope = UserScope(username="user1", role="user", unit_ids=["CN Miền Nam", "CN Cần Thơ"])
        sql = "SELECT product, count FROM v_issues_current"
        scoped_sql = ScopePolicy.enforce_sql_where(sql, scope)
        assert "WHERE unit_name IN ('CN Miền Nam', 'CN Cần Thơ')" in scoped_sql
        assert scoped_sql == "SELECT product, count FROM v_issues_current WHERE unit_name IN ('CN Miền Nam', 'CN Cần Thơ')"

    def test_enforce_sql_where_user_with_existing_where(self):
        scope = UserScope(username="user1", role="user", unit_ids=["CN Miền Nam"])
        sql = "SELECT product FROM v_issues_current WHERE sentiment = 'Tiêu cực'"
        scoped_sql = ScopePolicy.enforce_sql_where(sql, scope)
        assert "(unit_name IN ('CN Miền Nam')) AND (sentiment = 'Tiêu cực')" in scoped_sql

    def test_enforce_sql_where_empty_scope(self):
        scope = UserScope(username="user_no_unit", role="user", unit_ids=[])
        sql = "SELECT * FROM v_issues_current"
        scoped_sql = ScopePolicy.enforce_sql_where(sql, scope)
        assert "WHERE 1 = 0" in scoped_sql

    def test_filter_rows_by_scope(self):
        scope = UserScope(username="user_mn", role="user", unit_ids=["CN Miền Nam"])
        rows = [
            {"feedback_id": 1, "unit_name": "CN Miền Nam", "issue": "A"},
            {"feedback_id": 2, "unit_name": "CN Miền Bắc", "issue": "B"},
            {"feedback_id": 3, "unit_name": "CN Miền Nam", "issue": "C"},
        ]
        filtered = ScopePolicy.filter_rows_by_scope(rows, scope)
        assert len(filtered) == 2
        assert all(r["unit_name"] == "CN Miền Nam" for r in filtered)
