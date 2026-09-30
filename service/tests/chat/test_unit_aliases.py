from __future__ import annotations

import pytest

from dms.chat.ai.unit_aliases import DEFAULT_ALIASES, load_aliases

from .ai_fakes import SAMPLE_METADATA

DIMENSION_TO_METADATA_KEY = {"unit": "units", "province": "provinces"}


@pytest.mark.parametrize("dimension", sorted(DIMENSION_TO_METADATA_KEY))
def test_every_alias_points_to_a_real_value(dimension):
    valid = set(SAMPLE_METADATA[DIMENSION_TO_METADATA_KEY[dimension]])
    targets = DEFAULT_ALIASES.targets(dimension)
    assert targets, f"chiều {dimension} chưa có alias nào"
    for target in targets:
        assert target in valid, f"alias trỏ tới '{target}' không có trong dữ liệu mẫu"


@pytest.mark.parametrize(
    ("mention", "expected"),
    [
        ("tv1", "Truyền thống Vùng 1"),
        ("TV1", "Truyền thống Vùng 1"),
        ("Vùng 2", "Truyền thống Vùng 2"),
        ("Sài Gòn", "Hồ Chí Minh"),
        ("  hcm  ", "Hồ Chí Minh"),
    ],
)
def test_resolve_ignores_case_accents_and_spacing(mention, expected):
    assert DEFAULT_ALIASES.resolve("unit", mention) == expected


def test_unknown_alias_returns_none():
    assert DEFAULT_ALIASES.resolve("unit", "Chi nhánh Sao Hoả") is None
    assert DEFAULT_ALIASES.resolve("khong_co_chieu_nay", "tv1") is None


def test_comment_keys_are_skipped():
    table = load_aliases()
    assert "_comment" not in list(table.dimensions())
