"""Tests for config reverse-sync endpoints and SharePoint client dependency."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from conftest import apply_auth_overrides
from fastapi.testclient import TestClient

from dms.settings import Settings, get_settings
from dms.web import deps
from dms.web.app import create_app

# ─── Helpers ───


def _make_settings(tmp_path: Path, kw_dir: Path | None = None) -> Settings:
    """Build a Settings object pointing at tmp_path for isolation."""
    if kw_dir is None:
        kw_dir = tmp_path / "Keyword"
        kw_dir.mkdir(exist_ok=True)
    return Settings(
        azure_tenant_id="tenant",
        azure_client_id="client",
        azure_client_secret="secret",
        sharepoint_drive_id="drive",
        sharepoint_root_folder_id="root",
        gemini_backend="vertex",
        gcp_project_id="project",
        keyword_dir_override=kw_dir,
        data_dir=tmp_path,
        work_dir=tmp_path / "work",
        log_dir=tmp_path / "logs",
    )


def _fake_sp_upload_response(
    item_id="sp-item-123", etag='"etag-abc"', modified="2026-05-29T08:00:00Z", size=1024
):
    """Return a dict mimicking a SharePoint Graph API upload response."""
    return {
        "id": item_id,
        "eTag": etag,
        "lastModifiedDateTime": modified,
        "size": size,
        "name": "kw_map.json",
    }


# ─── Fixtures ───


@pytest.fixture
def mock_sp_client():
    """A MagicMock SharePointClient with configurable upload_file return."""
    client = MagicMock()
    client.upload_file.return_value = _fake_sp_upload_response()
    return client


@pytest.fixture
def client(tmp_path, monkeypatch, mock_sp_client):
    """TestClient that fully isolates Settings, deps, and SharePointClient."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_BACKEND", raising=False)
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
    monkeypatch.delenv("AZURE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("SHAREPOINT_DRIVE_ID", raising=False)
    monkeypatch.delenv("SHAREPOINT_ROOT_FOLDER_ID", raising=False)

    kw_dir = tmp_path / "Keyword"
    kw_dir.mkdir(exist_ok=True)
    settings = _make_settings(tmp_path, kw_dir)

    monkeypatch.setattr("dms.settings.SERVICE_DIR", tmp_path)
    monkeypatch.setattr("dms.web.api.pipeline_api.deps.get_settings", lambda: settings)
    monkeypatch.setattr(
        "dms.web.api.pipeline_api.deps.get_sharepoint_client", lambda: mock_sp_client
    )
    monkeypatch.setattr("dms.web.deps.get_settings", lambda: settings)

    if hasattr(get_settings, "cache_clear"):
        get_settings.cache_clear()
    deps.reset()

    app = create_app()
    apply_auth_overrides(app)
    return TestClient(app)


@pytest.fixture
def kw_map_path(tmp_path) -> Path:
    """Create a kw_map.json file in the test keyword dir."""
    kw_dir = tmp_path / "Keyword"
    kw_dir.mkdir(exist_ok=True)
    path = kw_dir / "kw_map.json"
    path.write_text(json.dumps({"Báo lỗi": ["hỏng", "cháy"]}, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def products_path(tmp_path) -> Path:
    """Create a minimal product Excel file in the test keyword dir."""
    import pandas as pd

    kw_dir = tmp_path / "Keyword"
    kw_dir.mkdir(exist_ok=True)
    path = kw_dir / "Phân Chia Nhóm Sản Phẩm V2.xlsx"
    df = pd.DataFrame([{"Sản phẩm": "Đèn LED", "Dòng SP": "Bulb"}])
    df.to_excel(path, index=False)
    return path


# ═══════════════════════════════════════════════════════════════
#  6.1 — Tests for get_sharepoint_client()
# ═══════════════════════════════════════════════════════════════


class TestGetSharePointClient:
    """Test deps.get_sharepoint_client() lazy singleton."""

    def test_returns_none_when_settings_unavailable(self, monkeypatch):
        deps.reset()
        monkeypatch.setattr("dms.web.deps.get_settings", lambda: None)
        assert deps.get_sharepoint_client() is None

    def test_returns_none_on_auth_error(self, tmp_path, monkeypatch):
        deps.reset()
        settings = _make_settings(tmp_path)
        monkeypatch.setattr("dms.web.deps.get_settings", lambda: settings)
        # Force AuthProvider to fail
        monkeypatch.setattr(
            "dms.web.deps._get_or_create",
            lambda key, factory: factory() if key == "sharepoint_client" else None,
        )
        with patch("dms.auth.AuthProvider.__init__", side_effect=ValueError("bad creds")):
            result = deps.get_sharepoint_client()
        assert result is None

    def test_singleton_returns_same_instance(self, tmp_path, monkeypatch):
        deps.reset()
        sentinel = MagicMock()
        monkeypatch.setattr("dms.web.deps.get_settings", lambda: _make_settings(tmp_path))
        monkeypatch.setattr(
            "dms.web.deps._get_or_create",
            lambda key, factory: sentinel if key == "sharepoint_client" else factory(),
        )
        a = deps.get_sharepoint_client()
        b = deps.get_sharepoint_client()
        assert a is b


# ═══════════════════════════════════════════════════════════════
#  6.2 — Tests for sync-keywords-to-sp endpoint
# ═══════════════════════════════════════════════════════════════


class TestSyncKeywordsToSP:
    """POST /api/pipeline/sync-keywords-to-sp"""

    def test_success_uploads_and_updates_state(self, client, kw_map_path, mock_sp_client, tmp_path):
        response = client.post("/api/pipeline/sync-keywords-to-sp")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "SharePoint" in data["message"]
        assert data["sharepoint_item_id"] == "sp-item-123"

        # SharePoint client was called with the right file
        mock_sp_client.upload_file.assert_called_once()
        call_args = mock_sp_client.upload_file.call_args
        assert str(call_args[0][0]).endswith("kw_map.json")

        # config_assets_state.json was updated
        state_path = tmp_path / "work" / "config_assets_state.json"
        assert state_path.is_file()
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert "keyword/kw_map.json" in state["assets"]
        version = state["assets"]["keyword/kw_map.json"]
        assert version["item_id"] == "sp-item-123"
        assert version["e_tag"] == '"etag-abc"'

    def test_404_when_kw_map_missing(self, client, tmp_path):
        # Don't create kw_map.json
        response = client.post("/api/pipeline/sync-keywords-to-sp")
        assert response.status_code == 404
        assert "kw_map.json" in response.json()["detail"]

    def test_503_when_sp_client_unavailable(self, client, kw_map_path, monkeypatch):
        monkeypatch.setattr("dms.web.api.pipeline_api.deps.get_sharepoint_client", lambda: None)
        response = client.post("/api/pipeline/sync-keywords-to-sp")
        assert response.status_code == 503
        assert "SharePoint" in response.json()["detail"]

    def test_502_when_upload_fails(self, client, kw_map_path, mock_sp_client):
        mock_sp_client.upload_file.side_effect = Exception("Network timeout")
        response = client.post("/api/pipeline/sync-keywords-to-sp")
        assert response.status_code == 502
        assert "Network timeout" in response.json()["detail"]

    def test_502_does_not_update_state(self, client, kw_map_path, mock_sp_client, tmp_path):
        """Edge case: upload fails → state must NOT be updated."""
        mock_sp_client.upload_file.side_effect = Exception("Auth expired")
        client.post("/api/pipeline/sync-keywords-to-sp")
        state_path = tmp_path / "work" / "config_assets_state.json"
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            assert "keyword/kw_map.json" not in state.get("assets", {})

    def test_400_when_settings_missing(self, client, monkeypatch):
        monkeypatch.setattr("dms.web.api.pipeline_api.deps.get_settings", lambda: None)
        response = client.post("/api/pipeline/sync-keywords-to-sp")
        assert response.status_code == 400


# ═══════════════════════════════════════════════════════════════
#  6.3 — Tests for sync-products-to-sp endpoint
# ═══════════════════════════════════════════════════════════════


class TestSyncProductsToSP:
    """POST /api/pipeline/sync-products-to-sp"""

    def test_success_uploads_excel(self, client, products_path, mock_sp_client, tmp_path):
        mock_sp_client.upload_file.return_value = _fake_sp_upload_response(
            item_id="sp-prod-456", etag='"etag-prod"', size=2048
        )
        response = client.post("/api/pipeline/sync-products-to-sp")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["sharepoint_item_id"] == "sp-prod-456"

        # State updated for the product file
        state_path = tmp_path / "work" / "config_assets_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        product_key = "keyword/Phân Chia Nhóm Sản Phẩm V2.xlsx"
        assert product_key in state["assets"]
        assert state["assets"][product_key]["item_id"] == "sp-prod-456"

    def test_404_when_excel_missing(self, client, tmp_path):
        response = client.post("/api/pipeline/sync-products-to-sp")
        assert response.status_code == 404

    def test_503_when_sp_client_unavailable(self, client, products_path, monkeypatch):
        monkeypatch.setattr("dms.web.api.pipeline_api.deps.get_sharepoint_client", lambda: None)
        response = client.post("/api/pipeline/sync-products-to-sp")
        assert response.status_code == 503

    def test_502_when_upload_fails(self, client, products_path, mock_sp_client):
        mock_sp_client.upload_file.side_effect = ConnectionError("SharePoint unreachable")
        response = client.post("/api/pipeline/sync-products-to-sp")
        assert response.status_code == 502
        assert "SharePoint unreachable" in response.json()["detail"]


# ═══════════════════════════════════════════════════════════════
#  Edge Case Tests — state integrity, sync-loop prevention,
#  concurrent access, corrupt state, etc.
# ═══════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge cases to prevent the system from getting confused."""

    def test_state_preserves_other_assets(self, client, kw_map_path, mock_sp_client, tmp_path):
        """Uploading kw_map should NOT wipe state entries for other assets."""
        # Pre-seed state with an existing model asset entry
        state_path = tmp_path / "work" / "config_assets_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        existing_state = {
            "assets": {
                "model/tfidf_word.pkl": {
                    "item_id": "model-123",
                    "e_tag": '"model-etag"',
                    "last_modified": "2026-05-01T00:00:00Z",
                    "size": "5000",
                }
            },
            "last_success_at": "2026-05-01T00:00:00",
        }
        state_path.write_text(json.dumps(existing_state), encoding="utf-8")

        # Now sync keywords
        client.post("/api/pipeline/sync-keywords-to-sp")

        # Both entries must exist
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert "model/tfidf_word.pkl" in state["assets"], "Existing model asset was lost!"
        assert "keyword/kw_map.json" in state["assets"], "New keyword asset not added!"
        assert state["assets"]["model/tfidf_word.pkl"]["item_id"] == "model-123"

    def test_state_with_corrupt_json_recovers(self, client, kw_map_path, mock_sp_client, tmp_path):
        """If config_assets_state.json is corrupt, sync should still work."""
        state_path = tmp_path / "work" / "config_assets_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text("NOT-VALID-JSON!!{{{", encoding="utf-8")

        response = client.post("/api/pipeline/sync-keywords-to-sp")
        assert response.status_code == 200

        # State should be overwritten cleanly
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert "keyword/kw_map.json" in state["assets"]

    def test_version_format_matches_config_asset_sync(
        self, client, kw_map_path, mock_sp_client, tmp_path
    ):
        """The state entry format MUST exactly match ConfigAssetSyncService._item_version().
        If this breaks, the sync loop prevention will fail silently.
        """
        client.post("/api/pipeline/sync-keywords-to-sp")

        state_path = tmp_path / "work" / "config_assets_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        version = state["assets"]["keyword/kw_map.json"]

        # Must have exactly these 4 keys, all as strings
        expected_keys = {"item_id", "e_tag", "last_modified", "size"}
        assert set(version.keys()) == expected_keys, (
            f"Keys mismatch: {set(version.keys())} vs {expected_keys}"
        )
        for key in expected_keys:
            assert isinstance(version[key], str), f"Key {key} must be str, got {type(version[key])}"

    def test_sync_loop_prevention_via_is_changed(
        self, client, kw_map_path, mock_sp_client, tmp_path
    ):
        """After uploading, ConfigAssetSyncService._is_changed() should return False."""
        from dms.config_assets import ConfigAssetSyncService

        # Upload and get the state written
        client.post("/api/pipeline/sync-keywords-to-sp")

        state_path = tmp_path / "work" / "config_assets_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        prior = state["assets"]["keyword/kw_map.json"]

        # Simulate the same file being on SharePoint (same response as upload)
        remote_item = _fake_sp_upload_response()

        # _is_changed() should say "no change" → no re-download
        assert ConfigAssetSyncService._is_changed(remote_item, prior, kw_map_path) is False

    def test_is_changed_detects_real_remote_update(
        self, client, kw_map_path, mock_sp_client, tmp_path
    ):
        """If someone else modifies the file on SharePoint after our upload, _is_changed must return True."""
        from dms.config_assets import ConfigAssetSyncService

        client.post("/api/pipeline/sync-keywords-to-sp")

        state_path = tmp_path / "work" / "config_assets_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        prior = state["assets"]["keyword/kw_map.json"]

        # Simulate someone ELSE uploading a newer version with different eTag
        remote_item_changed = _fake_sp_upload_response(
            item_id="sp-item-123",
            etag='"etag-NEW-VERSION"',
            modified="2026-05-30T12:00:00Z",
            size=2048,
        )
        assert ConfigAssetSyncService._is_changed(remote_item_changed, prior, kw_map_path) is True

    def test_double_sync_is_idempotent(self, client, kw_map_path, mock_sp_client, tmp_path):
        """Syncing twice should produce valid state both times, not corrupt it."""
        r1 = client.post("/api/pipeline/sync-keywords-to-sp")
        assert r1.status_code == 200

        # Change the mock response slightly (new eTag after re-upload)
        mock_sp_client.upload_file.return_value = _fake_sp_upload_response(
            etag='"etag-second-upload"'
        )
        r2 = client.post("/api/pipeline/sync-keywords-to-sp")
        assert r2.status_code == 200

        state_path = tmp_path / "work" / "config_assets_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["assets"]["keyword/kw_map.json"]["e_tag"] == '"etag-second-upload"'

    def test_empty_sp_response_fields_stored_as_empty_strings(
        self, client, kw_map_path, mock_sp_client, tmp_path
    ):
        """If SharePoint response is missing fields, they should be stored as '' not None."""
        mock_sp_client.upload_file.return_value = {}  # Empty response
        response = client.post("/api/pipeline/sync-keywords-to-sp")
        assert response.status_code == 200

        state_path = tmp_path / "work" / "config_assets_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        version = state["assets"]["keyword/kw_map.json"]
        for key, val in version.items():
            assert isinstance(val, str), f"{key} should be str, got {type(val)}"
            assert val == "", f"{key} should be '' for missing fields, got {val!r}"

    def test_keyword_and_product_sync_independent_state(
        self, client, kw_map_path, products_path, mock_sp_client, tmp_path
    ):
        """Syncing keywords and products should write to different state keys."""
        mock_sp_client.upload_file.side_effect = [
            _fake_sp_upload_response(item_id="kw-111"),
            _fake_sp_upload_response(item_id="prod-222"),
        ]

        r1 = client.post("/api/pipeline/sync-keywords-to-sp")
        r2 = client.post("/api/pipeline/sync-products-to-sp")
        assert r1.status_code == 200
        assert r2.status_code == 200

        state_path = tmp_path / "work" / "config_assets_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["assets"]["keyword/kw_map.json"]["item_id"] == "kw-111"
        product_key = "keyword/Phân Chia Nhóm Sản Phẩm V2.xlsx"
        assert state["assets"][product_key]["item_id"] == "prod-222"

    def test_state_dir_auto_created(self, client, kw_map_path, mock_sp_client, tmp_path):
        """State file parent dir (work/) should be created automatically."""
        work_dir = tmp_path / "work"
        if work_dir.exists():
            import shutil

            shutil.rmtree(work_dir)

        response = client.post("/api/pipeline/sync-keywords-to-sp")
        assert response.status_code == 200
        assert (work_dir / "config_assets_state.json").is_file()
