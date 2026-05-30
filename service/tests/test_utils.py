"""Unit tests for dms.utils atomic write functions."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dms.utils import atomic_write_json, atomic_write_text


class TestAtomicWriteText:
    def test_creates_file_with_content(self, tmp_path: Path) -> None:
        target = tmp_path / "output.txt"
        atomic_write_text(target, "hello world")
        assert target.read_text(encoding="utf-8") == "hello world"

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "deep" / "file.txt"
        atomic_write_text(target, "content")
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "content"

    def test_overwrites_existing_file(self, tmp_path: Path) -> None:
        target = tmp_path / "file.txt"
        target.write_text("old content", encoding="utf-8")
        atomic_write_text(target, "new content")
        assert target.read_text(encoding="utf-8") == "new content"

    def test_no_temp_files_left_after_success(self, tmp_path: Path) -> None:
        target = tmp_path / "file.txt"
        atomic_write_text(target, "data")
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].name == "file.txt"

    def test_unicode_content(self, tmp_path: Path) -> None:
        target = tmp_path / "unicode.txt"
        content = "Tiếng Việt: Ngày tốt lành 🎉"
        atomic_write_text(target, content)
        assert target.read_text(encoding="utf-8") == content


class TestAtomicWriteJson:
    def test_creates_valid_json_file(self, tmp_path: Path) -> None:
        target = tmp_path / "data.json"
        data = {"key": "value", "number": 42, "list": [1, 2, 3]}
        atomic_write_json(target, data)
        loaded = json.loads(target.read_text(encoding="utf-8"))
        assert loaded == data

    def test_nested_structure(self, tmp_path: Path) -> None:
        target = tmp_path / "nested.json"
        data = {"seen": {"file_id_1": {"name": "test.xlsx", "status": "done"}}}
        atomic_write_json(target, data)
        loaded = json.loads(target.read_text(encoding="utf-8"))
        assert loaded["seen"]["file_id_1"]["status"] == "done"

    def test_empty_dict(self, tmp_path: Path) -> None:
        target = tmp_path / "empty.json"
        atomic_write_json(target, {})
        loaded = json.loads(target.read_text(encoding="utf-8"))
        assert loaded == {}

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b" / "c.json"
        atomic_write_json(target, {"x": 1})
        assert target.exists()

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        target = tmp_path / "file.json"
        atomic_write_json(target, {"v": 1})
        atomic_write_json(target, {"v": 2})
        loaded = json.loads(target.read_text(encoding="utf-8"))
        assert loaded["v"] == 2

    def test_non_serializable_uses_default_str(self, tmp_path: Path) -> None:
        from datetime import datetime
        target = tmp_path / "dt.json"
        now = datetime.now()
        atomic_write_json(target, {"ts": now})
        loaded = json.loads(target.read_text(encoding="utf-8"))
        # datetime is serialized to string via default=str
        assert isinstance(loaded["ts"], str)
