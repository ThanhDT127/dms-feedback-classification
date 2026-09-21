from __future__ import annotations

import pytest

from dms.chat.ai.intents import (
    INTENT_SPECS,
    Intent,
    PlannerState,
    functions_for_milestone,
    is_supported,
    supported_functions,
)
from dms.chat.contract import FUNCTION_REGISTRY_SPEC

M1_MAPPING = {
    Intent.OVERVIEW: {"get_overview"},
    Intent.COMPARISON: {"get_overview", "get_comparison"},
    Intent.TREND: {"get_daily_trend"},
    Intent.DRILL_PRODUCT: {"get_products"},
    Intent.DRILL_UNIT: {"get_units", "get_unit_issue_type_matrix"},
    Intent.DRILL_GEOGRAPHY: {"get_geography"},
    Intent.DRILL_ISSUE: {
        "get_issue_types",
        "get_groups",
        "get_status_backlog",
        "get_priority_issues",
    },
    Intent.LOOKUP_FEEDBACK: {"get_issues"},
}


def test_exactly_fifteen_intents():
    assert [intent.value for intent in Intent] == [
        "OVERVIEW",
        "TREND",
        "COMPARISON",
        "DRILL_PRODUCT",
        "DRILL_UNIT",
        "DRILL_GEOGRAPHY",
        "DRILL_ISSUE",
        "LOOKUP_FEEDBACK",
        "LOOKUP_FILE",
        "LOOKUP_SIMILAR",
        "REPORT_DAILY",
        "REPORT_WEEKLY",
        "REPORT_CUSTOM",
        "REPORT_EXPORT",
        "HELP",
    ]
    assert set(INTENT_SPECS) == set(Intent)


def test_planner_states_exclude_unauthorized_scope():
    assert {state.value for state in PlannerState} == {"CLARIFY", "OUT_OF_DOMAIN"}


def test_every_mapped_function_exists_in_contract():
    for spec in INTENT_SPECS.values():
        for name in spec.allowed_functions:
            assert name in FUNCTION_REGISTRY_SPEC, (spec.intent, name)


def test_m1_mapping_matches_spec():
    for intent in Intent:
        assert set(supported_functions(intent, "M1")) == M1_MAPPING.get(intent, set()), intent


def test_support_flags_at_m1():
    assert is_supported(Intent.OVERVIEW, "M1")
    assert is_supported(Intent.HELP, "M1")
    for intent in (
        Intent.LOOKUP_FILE,
        Intent.LOOKUP_SIMILAR,
        Intent.REPORT_DAILY,
        Intent.REPORT_WEEKLY,
        Intent.REPORT_CUSTOM,
        Intent.REPORT_EXPORT,
    ):
        assert not is_supported(intent, "M1"), intent


def test_later_milestone_keeps_m1_functions():
    assert set(functions_for_milestone("M1")) <= set(functions_for_milestone("M5"))


def test_unknown_milestone_raises():
    with pytest.raises(ValueError):
        supported_functions(Intent.OVERVIEW, "M9")
