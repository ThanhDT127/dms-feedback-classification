"""Sync tracked keyword/model assets from SharePoint into local runtime paths."""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory

from .exceptions import ConfigAssetSyncError
from .settings import Settings
from .sharepoint import SharePointClient
from .time_utils import utc_now_iso

logger = logging.getLogger("dms-watcher")

KEYWORD_ASSET_NAMES = (
    "Phân Chia Nhóm Sản Phẩm V2.xlsx",
    "Hệ từ khóa Lọc 3 lần.xlsx",
    "kw_map.json",
)

MODEL_ASSET_NAMES = (
    "tfidf_word.pkl",
    "tfidf_char.pkl",
    "ovr_logreg.pkl",
    "best_thresholds.json",
    "label_cols.json",
    "keyword_minors.json",
)


@dataclass(frozen=True)
class TrackedConfigAsset:
    """Manifest entry for one tracked config asset."""

    asset_key: str
    category: str
    file_name: str
    remote_folder: str
    local_dir: Path
    required: bool

    @property
    def local_path(self) -> Path:
        return self.local_dir / self.file_name


@dataclass
class ConfigSyncResult:
    """Outcome of one sync attempt."""

    checked_at: str
    reload_required: bool = False
    changed_assets: list[str] = field(default_factory=list)
    downloaded_assets: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_health_dict(self) -> dict:
        return {
            "checked_at": self.checked_at,
            "reload_required": self.reload_required,
            "changed_assets": list(self.changed_assets),
            "downloaded_assets": list(self.downloaded_assets),
            "errors": list(self.errors),
        }


class ConfigAssetSyncService:
    """Synchronize tracked Keyword/ and Model/ assets from SharePoint."""

    def __init__(
        self,
        settings: Settings,
        sharepoint_client: SharePointClient,
        snapshot_validator: Callable[[Path, Path], None] | None = None,
    ) -> None:
        self.settings = settings
        self.sharepoint_client = sharepoint_client
        self.snapshot_validator = snapshot_validator
        self.last_result: ConfigSyncResult | None = None

    @property
    def active_keyword_dir(self) -> Path:
        return self.settings.active_keyword_dir

    @property
    def active_model_dir(self) -> Path:
        return self.settings.active_model_dir

    @property
    def source_keyword_dir(self) -> Path:
        return self.settings.keyword_dir

    @property
    def source_model_dir(self) -> Path:
        return self.settings.model_dir

    def has_active_snapshot(self) -> bool:
        return self.active_keyword_dir.exists() or self.active_model_dir.exists()

    def get_runtime_settings(self) -> Settings:
        if not self.settings.enable_sharepoint_config_sync:
            return self.settings
        if not self.has_active_snapshot():
            return self.settings
        updates: dict[str, Path] = {}
        if self.active_keyword_dir.exists():
            updates["keyword_dir_override"] = self.active_keyword_dir
        if self.active_model_dir.exists():
            updates["model_dir_override"] = self.active_model_dir
        return self.settings.model_copy(update=updates)

    def _source_path_for(self, asset: TrackedConfigAsset) -> Path:
        base_dir = self.source_keyword_dir if asset.category == "keyword" else self.source_model_dir
        return base_dir / asset.file_name

    def _current_local_path_for(self, asset: TrackedConfigAsset) -> Path:
        if asset.local_path.exists():
            return asset.local_path
        return self._source_path_for(asset)

    def build_manifest(self) -> list[TrackedConfigAsset]:
        manifest: list[TrackedConfigAsset] = []
        for file_name in KEYWORD_ASSET_NAMES:
            manifest.append(
                TrackedConfigAsset(
                    asset_key=f"keyword/{file_name}",
                    category="keyword",
                    file_name=file_name,
                    remote_folder=self.settings.sp_keyword_folder,
                    local_dir=self.active_keyword_dir,
                    required=file_name in {"Phân Chia Nhóm Sản Phẩm V2.xlsx", "kw_map.json"},
                )
            )
        for file_name in MODEL_ASSET_NAMES:
            manifest.append(
                TrackedConfigAsset(
                    asset_key=f"model/{file_name}",
                    category="model",
                    file_name=file_name,
                    remote_folder=self.settings.sp_model_folder,
                    local_dir=self.active_model_dir,
                    required=file_name != "keyword_minors.json",
                )
            )
        return manifest

    def load_state(self) -> dict:
        path = self.settings.config_assets_state_path
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Cannot read config asset state: %s", exc)
        return {"assets": {}, "last_success_at": None}

    def save_state(self, state: dict) -> None:
        from .utils import atomic_write_json

        atomic_write_json(self.settings.config_assets_state_path, state)

    @staticmethod
    def _item_version(item: dict) -> dict[str, str]:
        return {
            "item_id": str(item.get("id", "")),
            "e_tag": str(item.get("eTag", "")),
            "last_modified": str(item.get("lastModifiedDateTime", "")),
            "size": str(item.get("size", "")),
        }

    @staticmethod
    def _is_changed(item: dict, prior: dict | None, local_path: Path) -> bool:
        if prior is None or not local_path.exists():
            return True
        version = ConfigAssetSyncService._item_version(item)
        return any(version[key] != str(prior.get(key, "")) for key in version)

    def _copy_dir_contents(self, src_dir: Path, dst_dir: Path) -> None:
        dst_dir.mkdir(parents=True, exist_ok=True)
        if not src_dir.exists():
            return
        for item in src_dir.iterdir():
            dest = dst_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)

    def _download_into_stage(self, asset: TrackedConfigAsset, item: dict, stage_dir: Path) -> None:
        self.sharepoint_client.download_file(str(item["id"]), stage_dir / asset.file_name)

    def _publish_stage(self, src_dir: Path, dst_dir: Path) -> None:
        dst_dir.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = dst_dir.parent / f"{dst_dir.name}.tmp"
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        if dst_dir.exists():
            shutil.copytree(dst_dir, temp_dir)
        else:
            temp_dir.mkdir(parents=True, exist_ok=True)
        self._copy_dir_contents(src_dir, temp_dir)
        backup_dir = dst_dir.parent / f"{dst_dir.name}.bak"
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        if dst_dir.exists():
            dst_dir.replace(backup_dir)
        temp_dir.replace(dst_dir)
        if backup_dir.exists():
            shutil.rmtree(backup_dir)

    def _seed_stage(self, active_dir: Path, source_dir: Path, stage_dir: Path) -> None:
        if active_dir.exists():
            self._copy_dir_contents(active_dir, stage_dir)
            return
        self._copy_dir_contents(source_dir, stage_dir)

    def _validate_required_local_assets(self, assets: list[TrackedConfigAsset]) -> None:
        missing = []
        for asset in assets:
            if not asset.required:
                continue
            if asset.local_path.exists():
                continue
            source_path = (
                self.source_keyword_dir / asset.file_name
                if asset.category == "keyword"
                else self.source_model_dir / asset.file_name
            )
            if not source_path.exists():
                missing.append(asset.file_name)
        if missing:
            raise ConfigAssetSyncError(
                "No valid local snapshot exists and required config assets are missing: "
                + ", ".join(sorted(missing))
            )

    def sync(self) -> ConfigSyncResult:
        checked_at = utc_now_iso()
        result = ConfigSyncResult(
            checked_at=checked_at,
        )
        self.last_result = result

        if not self.settings.enable_sharepoint_config_sync:
            return result

        manifest = self.build_manifest()
        state = self.load_state()
        prior_assets = state.get("assets", {})

        grouped_remote: dict[str, dict[str, dict]] = {}
        try:
            for folder_name in {asset.remote_folder for asset in manifest}:
                grouped_remote[folder_name] = {
                    item.get("name", ""): item
                    for item in self.sharepoint_client.list_folder_items(folder_name)
                    if "file" in item
                }
        except Exception as exc:
            self._validate_required_local_assets(manifest)
            message = f"Config asset sync skipped due to SharePoint error: {exc}"
            logger.warning(message)
            result.errors.append(message)
            return result

        changed_keyword: list[tuple[TrackedConfigAsset, dict]] = []
        changed_model: list[tuple[TrackedConfigAsset, dict]] = []
        next_asset_state = dict(prior_assets)

        for asset in manifest:
            remote_item = grouped_remote.get(asset.remote_folder, {}).get(asset.file_name)
            if remote_item is None:
                if asset.required and not self._current_local_path_for(asset).exists():
                    raise ConfigAssetSyncError(
                        f"Required config asset not found in SharePoint or local snapshot: {asset.file_name}"
                    )
                continue

            prior = prior_assets.get(asset.asset_key)
            if self._is_changed(remote_item, prior, self._current_local_path_for(asset)):
                bucket = changed_keyword if asset.category == "keyword" else changed_model
                bucket.append((asset, remote_item))
                result.changed_assets.append(asset.asset_key)
            else:
                next_asset_state[asset.asset_key] = prior

        if not changed_keyword and not changed_model:
            return result

        cache_root = self.settings.config_assets_cache_dir
        cache_root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=cache_root, prefix="cfgsync-") as temp_root_str:
            temp_root = Path(temp_root_str)
            stage_keyword = temp_root / "Keyword"
            stage_model = temp_root / "Model"
            self._seed_stage(self.active_keyword_dir, self.source_keyword_dir, stage_keyword)
            self._seed_stage(self.active_model_dir, self.source_model_dir, stage_model)

            for asset, item in changed_keyword:
                self._download_into_stage(asset, item, stage_keyword)
            for asset, item in changed_model:
                self._download_into_stage(asset, item, stage_model)

            if self.snapshot_validator is not None:
                self.snapshot_validator(stage_keyword, stage_model)

            if changed_keyword:
                self._publish_stage(stage_keyword, self.active_keyword_dir)
            if changed_model:
                self._publish_stage(stage_model, self.active_model_dir)

            for asset, item in changed_keyword:
                next_asset_state[asset.asset_key] = self._item_version(item)
                result.downloaded_assets.append(asset.asset_key)
            for asset, item in changed_model:
                next_asset_state[asset.asset_key] = self._item_version(item)
                result.downloaded_assets.append(asset.asset_key)

        state["assets"] = next_asset_state
        state["last_success_at"] = checked_at
        self.save_state(state)
        result.reload_required = bool(result.downloaded_assets)
        return result
