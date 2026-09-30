from __future__ import annotations

import pytest

from dms.chat.ai.prompt_loader import load_prompt, render_prompt_text


def test_missing_variable_raises():
    with pytest.raises(ValueError, match="Missing prompt variables: question"):
        render_prompt_text("{history}\n{question}", {"history": "x"})


def test_unused_variable_raises():
    with pytest.raises(ValueError, match="Unused prompt variables: extra"):
        render_prompt_text("{question}", {"question": "q", "extra": "e"})


def test_values_are_not_substituted_twice_and_json_is_kept():
    template = 'Trả JSON {"standalone_question": "..."}\n{history}\n{question}'
    text = render_prompt_text(template, {"history": "{question}", "question": "Q"})
    assert '{"standalone_question": "..."}' in text
    assert text.endswith("{question}\nQ")


def test_load_contextualize_prompt_has_provenance():
    prompt = load_prompt(
        "contextualize_v1",
        {"session_summary": "(chưa có)", "history": "Lượt 1", "question": "còn Hà Nội?"},
    )
    assert prompt.version == "contextualize_v1"
    assert len(prompt.sha256) == 64
    assert "còn Hà Nội?" in prompt.text
    assert "{history}" not in prompt.text and "{question}" not in prompt.text
    assert "{session_summary}" not in prompt.text and "<tom_tat_phien>" in prompt.text
