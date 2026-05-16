from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from support import write_baseline_artifacts

from dms.config_assets import ConfigAssetSyncService
from dms.exceptions import ConfigAssetSyncError, ModelArtifactError
from dms.pipeline.baseline_classifier import BaselineIssueClassifier
from dms.pipeline.rag_product import RAGProductMatcher


class FakeSharePointConfigClient:
    def __init__(self, folder_items: dict[str, list[dict]], file_bytes: dict[str, bytes]):
        self.folder_items = folder_items
        self.file_bytes = file_bytes
        self.downloads: list[tuple[str, Path]] = []

    def list_folder_items(self, folder_name: str) -> list[dict]:
        return list(self.folder_items.get(folder_name, []))

    def download_file(self, file_id: str, local_path: str | Path) -> Path:
        path = Path(local_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.file_bytes[file_id])
        self.downloads.append((file_id, path))
        return path


def _remote_item(item_id: str, name: str, etag: str, modified: str = "2026-05-16T10:00:00Z") -> dict:
    return {
        "id": item_id,
        "name": name,
        "eTag": etag,
        "lastModifiedDateTime": modified,
        "size": 10,
        "file": {},
    }


class DummyGemini:
    def generate(self, prompt, temperature=None):
        return "1. NONE"


def _validator(settings, keyword_dir: Path, model_dir: Path) -> None:
    snapshot_settings = settings.model_copy(
        update={"keyword_dir_override": keyword_dir, "model_dir_override": model_dir}
    )
    BaselineIssueClassifier(settings=snapshot_settings)
    RAGProductMatcher(settings=snapshot_settings, gemini=DummyGemini())


def prepare_local_assets(settings, tmp_path: Path) -> None:
    settings.data_dir = tmp_path / "data"
    settings.keyword_dir.mkdir(parents=True, exist_ok=True)
    settings.model_dir.mkdir(parents=True, exist_ok=True)
    write_baseline_artifacts(settings.model_dir, settings.keyword_dir, include_keyword_minors=True)
    with pd.ExcelWriter(settings.keyword_dir / "Phân Chia Nhóm Sản Phẩm V2.xlsx") as writer:
        pd.DataFrame(
            [{"Model": "AT10 8W", "Dòng SP": "Bulb", "Sản phẩm": "Den LED"}]
        ).to_excel(writer, index=False, sheet_name="Products")
        pd.DataFrame(
            [{"Keyword": "bóng đèn", "Dòng SP": "Bulb", "Sản phẩm": "Den LED", "Priority": 1}]
        ).to_excel(writer, index=False, sheet_name="Loc lan 2")
        pd.DataFrame(
            [{"Keyword": "phích cắm", "Sản phẩm": "Phich", "Priority": 1}]
        ).to_excel(writer, index=False, sheet_name="Loc lan 3")
    (settings.keyword_dir / "Hệ từ khóa Lọc 3 lần.xlsx").write_bytes(b"keyword-v1")


def seed_state(service: ConfigAssetSyncService, items: list[tuple[str, dict]]) -> None:
    service.save_state(
        {
            "assets": {
                asset_key: {
                    "item_id": str(item["id"]),
                    "e_tag": str(item["eTag"]),
                    "last_modified": str(item["lastModifiedDateTime"]),
                    "size": str(item["size"]),
                }
                for asset_key, item in items
            },
            "last_success_at": "2026-05-16T10:00:00",
        }
    )


def test_config_asset_sync_skips_unchanged_assets(settings, tmp_path: Path):
    prepare_local_assets(settings, tmp_path)
    kw_map_item = _remote_item("kw1", "kw_map.json", "etag-1")
    cat_item = _remote_item("cat1", "Phân Chia Nhóm Sản Phẩm V2.xlsx", "etag-2")
    tfidf_word_item = _remote_item("m1", "tfidf_word.pkl", "etag-a")
    tfidf_char_item = _remote_item("m2", "tfidf_char.pkl", "etag-b")
    ovr_item = _remote_item("m3", "ovr_logreg.pkl", "etag-c")
    thresholds_item = _remote_item("m4", "best_thresholds.json", "etag-d")
    labels_item = _remote_item("m5", "label_cols.json", "etag-e")
    service = ConfigAssetSyncService(
        settings=settings,
        sharepoint_client=FakeSharePointConfigClient(
            {
                settings.sp_keyword_folder: [
                    kw_map_item,
                    cat_item,
                ],
                settings.sp_model_folder: [
                    tfidf_word_item,
                    tfidf_char_item,
                    ovr_item,
                    thresholds_item,
                    labels_item,
                ],
            },
            {},
        ),
        snapshot_validator=lambda keyword_dir, model_dir: _validator(settings, keyword_dir, model_dir),
    )
    seed_state(
        service,
        [
            ("keyword/kw_map.json", kw_map_item),
            ("keyword/Phân Chia Nhóm Sản Phẩm V2.xlsx", cat_item),
            ("model/tfidf_word.pkl", tfidf_word_item),
            ("model/tfidf_char.pkl", tfidf_char_item),
            ("model/ovr_logreg.pkl", ovr_item),
            ("model/best_thresholds.json", thresholds_item),
            ("model/label_cols.json", labels_item),
        ],
    )

    result = service.sync()
    assert not result.reload_required
    assert result.downloaded_assets == []


def test_config_asset_sync_downloads_changed_asset_atomically(settings, tmp_path: Path):
    prepare_local_assets(settings, tmp_path)
    kw_map_old = _remote_item("kw1", "kw_map.json", "etag-old")
    cat_item = _remote_item("cat1", "Phân Chia Nhóm Sản Phẩm V2.xlsx", "etag-cat")
    tfidf_word_item = _remote_item("m1", "tfidf_word.pkl", "etag-a")
    tfidf_char_item = _remote_item("m2", "tfidf_char.pkl", "etag-b")
    ovr_item = _remote_item("m3", "ovr_logreg.pkl", "etag-c")
    thresholds_item = _remote_item("m4", "best_thresholds.json", "etag-d")
    labels_item = _remote_item("m5", "label_cols.json", "etag-e")
    client = FakeSharePointConfigClient(
        {
            settings.sp_keyword_folder: [
                _remote_item("kw1", "kw_map.json", "etag-new"),
                cat_item,
            ],
            settings.sp_model_folder: [
                tfidf_word_item,
                tfidf_char_item,
                ovr_item,
                thresholds_item,
                labels_item,
            ],
        },
        {"kw1": json.dumps({"Website": ["portal"]}, ensure_ascii=False).encode("utf-8")},
    )
    service = ConfigAssetSyncService(
        settings=settings,
        sharepoint_client=client,
        snapshot_validator=lambda keyword_dir, model_dir: _validator(settings, keyword_dir, model_dir),
    )
    seed_state(
        service,
        [
            ("keyword/kw_map.json", kw_map_old),
            ("keyword/Phân Chia Nhóm Sản Phẩm V2.xlsx", cat_item),
            ("model/tfidf_word.pkl", tfidf_word_item),
            ("model/tfidf_char.pkl", tfidf_char_item),
            ("model/ovr_logreg.pkl", ovr_item),
            ("model/best_thresholds.json", thresholds_item),
            ("model/label_cols.json", labels_item),
        ],
    )
    result = service.sync()

    assert result.reload_required
    assert "keyword/kw_map.json" in result.downloaded_assets
    runtime_settings = service.get_runtime_settings()
    payload = json.loads(runtime_settings.kw_map_path.read_text(encoding="utf-8"))
    assert payload["Website"] == ["portal"]
    assert settings.kw_map_path != runtime_settings.kw_map_path


def test_config_asset_sync_rejects_invalid_model_bundle_and_keeps_local_snapshot(settings, tmp_path: Path):
    prepare_local_assets(settings, tmp_path)
    original = settings.tfidf_word_path.read_bytes()
    kw_map_item = _remote_item("kw1", "kw_map.json", "etag-1")
    cat_item = _remote_item("cat1", "Phân Chia Nhóm Sản Phẩm V2.xlsx", "etag-cat")
    tfidf_word_old = _remote_item("m1", "tfidf_word.pkl", "etag-old")
    tfidf_char_item = _remote_item("m2", "tfidf_char.pkl", "etag-b")
    ovr_item = _remote_item("m3", "ovr_logreg.pkl", "etag-c")
    thresholds_item = _remote_item("m4", "best_thresholds.json", "etag-d")
    labels_item = _remote_item("m5", "label_cols.json", "etag-e")
    client = FakeSharePointConfigClient(
        {
            settings.sp_keyword_folder: [
                kw_map_item,
                cat_item,
            ],
            settings.sp_model_folder: [
                _remote_item("m1", "tfidf_word.pkl", "etag-bad"),
                tfidf_char_item,
                ovr_item,
                thresholds_item,
                labels_item,
            ],
        },
        {"m1": b"not-a-pickle"},
    )
    service = ConfigAssetSyncService(
        settings=settings,
        sharepoint_client=client,
        snapshot_validator=lambda keyword_dir, model_dir: _validator(settings, keyword_dir, model_dir),
    )
    seed_state(
        service,
        [
            ("keyword/kw_map.json", kw_map_item),
            ("keyword/Phân Chia Nhóm Sản Phẩm V2.xlsx", cat_item),
            ("model/tfidf_word.pkl", tfidf_word_old),
            ("model/tfidf_char.pkl", tfidf_char_item),
            ("model/ovr_logreg.pkl", ovr_item),
            ("model/best_thresholds.json", thresholds_item),
            ("model/label_cols.json", labels_item),
        ],
    )
    with pytest.raises(ModelArtifactError):
        service.sync()
    assert settings.tfidf_word_path.read_bytes() == original
    assert not service.active_model_dir.exists()


def test_config_asset_sync_raises_when_required_asset_missing_without_local_snapshot(settings, tmp_path: Path):
    settings.data_dir = tmp_path / "data"
    settings.ensure_runtime_dirs()
    client = FakeSharePointConfigClient(
        {
            settings.sp_keyword_folder: [],
            settings.sp_model_folder: [],
        },
        {},
    )
    service = ConfigAssetSyncService(
        settings=settings,
        sharepoint_client=client,
        snapshot_validator=lambda keyword_dir, model_dir: _validator(settings, keyword_dir, model_dir),
    )
    with pytest.raises(ConfigAssetSyncError):
        service.sync()


def test_config_asset_sync_uses_source_dirs_as_fallback_before_first_snapshot(settings, tmp_path: Path):
    prepare_local_assets(settings, tmp_path)
    service = ConfigAssetSyncService(
        settings=settings,
        sharepoint_client=FakeSharePointConfigClient(
            {settings.sp_keyword_folder: [], settings.sp_model_folder: []},
            {},
        ),
        snapshot_validator=lambda keyword_dir, model_dir: _validator(settings, keyword_dir, model_dir),
    )
    result = service.sync()
    assert not result.reload_required
    runtime_settings = service.get_runtime_settings()
    assert runtime_settings.keyword_dir == settings.keyword_dir
