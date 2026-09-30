"""Lưu, tìm và dọn file xuất của chat (spec ``chat-report-export``, design b10 D9).

``export_id`` là 128 bit ngẫu nhiên và mỗi lần tải đều kiểm chủ sở hữu, nên không đoán được
file của người khác. File hết hạn được dọn cùng housekeeping.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("dms-chat-export")

EXPORT_DIR_NAME = "chat_exports"
EXPORT_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_UNSAFE_USERNAME = re.compile(r"[^a-z0-9_-]+")
USERNAME_HASH_CHARS = 8
DEFAULT_TTL_HOURS = 24
DEFAULT_MAX_ACTIVE = 20


def new_export_id() -> str:
    return uuid.uuid4().hex


def safe_username(username: str) -> str:
    """Thư mục theo user chỉ gồm ``[a-z0-9_-]``; thêm hash ngắn để không trùng nhau."""
    raw = str(username or "")
    folded = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii").lower()
    cleaned = _UNSAFE_USERNAME.sub("_", folded).strip("_") or "user"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:USERNAME_HASH_CHARS]
    return f"{cleaned[:40]}-{digest}"


@dataclass(frozen=True)
class ExportRecord:
    export_id: str
    owner: str
    filename: str
    size_bytes: int
    created_at: str
    expires_at: str
    rows_exported: int = 0
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "export_id": self.export_id,
            "owner": self.owner,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "rows_exported": self.rows_exported,
            "truncated": self.truncated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExportRecord:
        return cls(
            export_id=str(data["export_id"]),
            owner=str(data["owner"]),
            filename=str(data.get("filename") or ""),
            size_bytes=int(data.get("size_bytes") or 0),
            created_at=str(data.get("created_at") or ""),
            expires_at=str(data.get("expires_at") or ""),
            rows_exported=int(data.get("rows_exported") or 0),
            truncated=bool(data.get("truncated")),
        )

    def is_expired(self, now: datetime) -> bool:
        return _parse(self.expires_at) <= now


class ExportStore:
    def __init__(
        self,
        root: Path,
        *,
        ttl_hours: int = DEFAULT_TTL_HOURS,
        max_active_per_user: int = DEFAULT_MAX_ACTIVE,
    ) -> None:
        self.root = Path(root)
        self.ttl_hours = int(ttl_hours)
        self.max_active_per_user = int(max_active_per_user)

    # ── Đường dẫn ──

    def user_dir(self, username: str) -> Path:
        return self.root / safe_username(username)

    def paths_for(self, username: str, export_id: str) -> tuple[Path, Path]:
        folder = self.user_dir(username)
        return folder / f"{export_id}.xlsx", folder / f"{export_id}.json"

    def reserve(self, username: str) -> tuple[str, Path]:
        export_id = new_export_id()
        path, _ = self.paths_for(username, export_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        return export_id, path

    # ── Ghi và đọc ──

    def save_metadata(
        self,
        username: str,
        export_id: str,
        *,
        filename: str,
        size_bytes: int,
        rows_exported: int = 0,
        truncated: bool = False,
        now: datetime | None = None,
    ) -> ExportRecord:
        moment = now or datetime.now(UTC)
        record = ExportRecord(
            export_id=export_id,
            owner=username,
            filename=filename,
            size_bytes=size_bytes,
            created_at=moment.isoformat(),
            expires_at=(moment + timedelta(hours=self.ttl_hours)).isoformat(),
            rows_exported=rows_exported,
            truncated=truncated,
        )
        _, meta_path = self.paths_for(username, export_id)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(record.to_dict(), ensure_ascii=False), encoding="utf-8")
        self.enforce_limit(username, now=moment)
        return record

    def get(
        self, username: str, export_id: str, *, now: datetime | None = None
    ) -> ExportRecord | None:
        """Bản ghi còn hạn của chính user; mọi trường hợp khác trả ``None`` (endpoint trả 404)."""
        if not EXPORT_ID_PATTERN.match(str(export_id or "")):
            return None
        path, meta_path = self.paths_for(username, export_id)
        record = _read(meta_path)
        if record is None or record.owner != username or not path.is_file():
            return None
        if record.is_expired(now or datetime.now(UTC)):
            return None
        return record

    def file_path(self, username: str, export_id: str) -> Path:
        return self.paths_for(username, export_id)[0]

    def records_for(self, username: str) -> list[ExportRecord]:
        folder = self.user_dir(username)
        if not folder.is_dir():
            return []
        found = [_read(path) for path in sorted(folder.glob("*.json"))]
        return [record for record in found if record is not None]

    # ── Dọn dẹp ──

    def enforce_limit(self, username: str, *, now: datetime | None = None) -> int:
        """Giữ tối đa ``max_active_per_user`` file chưa hết hạn; xoá bản cũ nhất trước."""
        moment = now or datetime.now(UTC)
        active = sorted(
            (record for record in self.records_for(username) if not record.is_expired(moment)),
            key=lambda record: record.created_at,
        )
        removed = 0
        while len(active) > self.max_active_per_user:
            oldest = active.pop(0)
            self.delete(oldest.owner, oldest.export_id)
            removed += 1
        if removed:
            logger.info("chat_export_limit_enforced", extra={"removed": removed, "owner": username})
        return removed

    def delete(self, username: str, export_id: str) -> None:
        for path in self.paths_for(username, export_id):
            path.unlink(missing_ok=True)

    def cleanup(self, now: datetime | None = None) -> int:
        """Xoá cả cặp ``.xlsx`` + ``.json`` của mọi file đã quá hạn."""
        moment = now or datetime.now(UTC)
        removed = 0
        for meta_path in self._all_metadata():
            record = _read(meta_path)
            if record is None:
                meta_path.unlink(missing_ok=True)
                meta_path.with_suffix(".xlsx").unlink(missing_ok=True)
                removed += 1
                continue
            if record.is_expired(moment):
                meta_path.unlink(missing_ok=True)
                meta_path.with_suffix(".xlsx").unlink(missing_ok=True)
                removed += 1
        if removed:
            logger.info("chat_exports_cleaned", extra={"removed": removed})
        return removed

    def _all_metadata(self) -> Iterator[Path]:
        if not self.root.is_dir():
            return iter(())
        return (
            path
            for folder in self.root.iterdir()
            if folder.is_dir()
            for path in folder.glob("*.json")
        )


def cleanup_chat_exports(root: Path, now: datetime | None = None) -> int:
    """Điểm gọi cho ``RuntimeCleanup.cleanup_housekeeping`` (b10 task 4.5)."""
    return ExportStore(root).cleanup(now)


def _read(path: Path) -> ExportRecord | None:
    try:
        return ExportRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _parse(value: str) -> datetime:
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


__all__ = [
    "EXPORT_DIR_NAME",
    "EXPORT_ID_PATTERN",
    "ExportRecord",
    "ExportStore",
    "cleanup_chat_exports",
    "new_export_id",
    "safe_username",
]
