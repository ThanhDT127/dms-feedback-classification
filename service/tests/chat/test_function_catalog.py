from __future__ import annotations

from dms.chat.ai.function_catalog import (
    FUNCTION_CATALOG,
    PARAM_LABELS_VI,
    REGISTRY_READABLE_PARAMS,
    ResultKind,
    render_function_line,
)
from dms.chat.contract import FUNCTION_REGISTRY_SPEC


def test_catalog_keys_match_contract():
    assert set(FUNCTION_CATALOG) == set(FUNCTION_REGISTRY_SPEC)
    assert set(REGISTRY_READABLE_PARAMS) == set(FUNCTION_REGISTRY_SPEC)


def test_supported_params_are_read_by_registry():
    for name, info in FUNCTION_CATALOG.items():
        extra = info.supported_params - REGISTRY_READABLE_PARAMS[name]
        assert not extra, (name, extra)


def test_contract_params_are_known_to_registry_table():
    for name, spec in FUNCTION_REGISTRY_SPEC.items():
        unknown = set(spec["params"]) - REGISTRY_READABLE_PARAMS[name]
        assert not unknown, (name, unknown)


def test_every_param_has_vietnamese_label():
    params = set().union(*(info.supported_params for info in FUNCTION_CATALOG.values()))
    assert params <= set(PARAM_LABELS_VI)


def test_result_kinds_for_block_rendering():
    assert FUNCTION_CATALOG["get_overview"].result_kind is ResultKind.KPI
    assert FUNCTION_CATALOG["get_daily_trend"].result_kind is ResultKind.TIMESERIES
    assert FUNCTION_CATALOG["get_products"].result_kind is ResultKind.DISTRIBUTION
    assert FUNCTION_CATALOG["get_issues"].result_kind is ResultKind.RECORDS
    assert FUNCTION_CATALOG["get_unit_issue_type_matrix"].result_kind is ResultKind.MATRIX


def test_only_overview_accepts_compare_range():
    with_compare = {
        name for name, info in FUNCTION_CATALOG.items() if "compare_from" in info.supported_params
    }
    assert with_compare == {"get_overview"}


def test_render_function_line():
    line = render_function_line(FUNCTION_CATALOG["get_comparison"])
    assert line.startswith("- get_comparison(")
    assert "period" in line and "Trả về:" in line
