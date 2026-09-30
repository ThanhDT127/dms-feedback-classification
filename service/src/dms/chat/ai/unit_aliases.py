"""Alias cách gọi tắt → tên thật trong dữ liệu (design b03 D3).

Áp **trước** khớp mờ để "tv1" hay "hcm" không phải nhờ rapidfuzz đoán. Nguồn chuẩn tạm thời
nằm phía Dev B; khi có MetadataCache của Dev A (review C09) thì đổi loader ở đây.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from .text_match import normalize_match_text

ALIASES_PATH = Path(__file__).resolve().parent / "data" / "unit_aliases.json"


@dataclass(frozen=True)
class AliasTable:
    """Tra theo chiều dữ liệu; khoá đã chuẩn hoá bỏ dấu, viết thường."""

    by_dimension: Mapping[str, Mapping[str, str]]

    def resolve(self, dimension: str, mention: str) -> str | None:
        """Trả tên thật nếu ``mention`` là một cách gọi tắt đã khai báo."""
        return self.by_dimension.get(dimension, {}).get(normalize_match_text(mention))

    def targets(self, dimension: str) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.by_dimension.get(dimension, {}).values()))

    def dimensions(self) -> Iterator[str]:
        return iter(self.by_dimension)


def load_aliases(path: Path = ALIASES_PATH) -> AliasTable:
    raw = json.loads(path.read_text(encoding="utf-8"))
    table: dict[str, dict[str, str]] = {}
    for dimension, mapping in raw.items():
        if dimension.startswith("_") or not isinstance(mapping, dict):
            continue
        table[str(dimension)] = {
            normalize_match_text(alias): str(value) for alias, value in mapping.items()
        }
    return AliasTable(by_dimension=table)


DEFAULT_ALIASES = load_aliases()
