from __future__ import annotations

from dms.chat.ai.intents import Intent, is_supported
from dms.chat.ai.suggestions import (
    MAX_SUGGESTIONS,
    SUGGESTION_TEMPLATES,
    build_suggestions,
    suggestions_for_milestone,
)
from dms.chat.ai.text_match import normalize_match_text


def test_suggestions_are_capped_and_never_repeat_the_question():
    asked = "Tổng quan tháng 8"

    items = build_suggestions(
        Intent.OVERVIEW, milestone="M1", range_text="tháng 8", asked_question=asked
    )

    assert 1 <= len(items) <= MAX_SUGGESTIONS
    assert all(normalize_match_text(item) != normalize_match_text(asked) for item in items)
    assert len(set(items)) == len(items)


def test_suggestions_have_no_leftover_placeholder():
    items = build_suggestions(Intent.OVERVIEW, milestone="M1", range_text="")

    assert items
    for item in items:
        assert "{" not in item and "}" not in item
        assert "  " not in item


def test_unit_templates_are_skipped_without_a_unit():
    with_unit = build_suggestions(Intent.DRILL_UNIT, milestone="M1", unit="Nha Trang")
    without_unit = build_suggestions(Intent.DRILL_UNIT, milestone="M1", unit=None)

    assert any("Nha Trang" in item for item in with_unit)
    assert all("Nha Trang" not in item for item in without_unit)


def test_only_intents_supported_at_the_milestone_are_suggested():
    items = build_suggestions(Intent.OVERVIEW, milestone="M1", range_text="tháng 8")

    # Ở M1 chưa có báo cáo/xuất file, nên không được gợi ý các việc đó.
    assert all("báo cáo" not in item.casefold() for item in items)
    assert all("xuất" not in item.casefold() for item in items)
    assert items


def test_every_template_intent_is_supported_somewhere():
    for intent in SUGGESTION_TEMPLATES:
        assert isinstance(intent, Intent)
    assert set(suggestions_for_milestone("M1")) <= set(SUGGESTION_TEMPLATES)
    assert all(is_supported(intent, "M1") for intent in suggestions_for_milestone("M1"))


def test_limit_can_be_lowered():
    items = build_suggestions(Intent.OVERVIEW, milestone="M1", range_text="tháng 8", limit=1)
    assert len(items) == 1


def test_unknown_intent_falls_back_to_overview_templates():
    items = build_suggestions(None, milestone="M1", range_text="tháng 8")
    assert items
