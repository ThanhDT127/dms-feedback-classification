from __future__ import annotations

import json

from dms.pipeline.issue_classifier import IssueClassifier, get_label_config_snapshot
from dms.prompt_renderer import DEFAULT_ISSUE_PROMPT_PATH, render_issue_classifier_prompt


class _Gemini:
    def __init__(self, response: str) -> None:
        self.calls: list[str] = []
        self.response = response

    def generate_json(self, prompt: str, temperature: float = 0.0):
        self.calls.append(prompt)
        return type("Response", (), {"text": self.response, "usage": {}})()


def test_issue_prompt_renderer_uses_default_template(settings):
    rendered = render_issue_classifier_prompt(
        settings,
        {
            "minor_order_json": '["Tin trung lap"]',
            "label_defs": "{}",
            "hints_json": "{}",
            "brand_json": "{}",
            "input_json": '[{"row_index": 0, "text": "hello"}]',
        },
    )

    assert rendered.source_path == DEFAULT_ISSUE_PROMPT_PATH
    assert rendered.version == "issue_classifier_v1"
    assert len(rendered.sha256) == 64
    assert "{input_json}" not in rendered.text
    assert "hello" in rendered.text


def test_issue_prompt_renderer_prefers_legacy_keyword_override(settings):
    settings.keyword_dir.mkdir(parents=True, exist_ok=True)
    override = settings.keyword_dir / "system_prompt.txt"
    override.write_text("Prompt {input_json} {minor_order_json} {label_defs} {hints_json} {brand_json}", encoding="utf-8")

    rendered = render_issue_classifier_prompt(
        settings,
        {
            "minor_order_json": "labels",
            "label_defs": "defs",
            "hints_json": "hints",
            "brand_json": "brands",
            "input_json": "rows",
        },
    )

    assert rendered.source_path == override
    assert rendered.text == "Prompt rows labels defs hints brands"


def test_issue_classifier_records_prompt_metadata(settings):
    labels = get_label_config_snapshot()["minor_order"]
    raw = json.dumps([{"row_index": 0, "labels": {labels[0]: True}, "decision_log": []}])
    gemini = _Gemini(raw)
    classifier = IssueClassifier(gemini, settings)

    classifier.classify_batch(["AT10 website bi loi"])

    assert gemini.calls
    assert "AT10 website bi loi" in gemini.calls[0]
    assert classifier._last_prompt["version"] == "issue_classifier_v1"
    assert len(classifier._last_prompt["sha256"]) == 64
