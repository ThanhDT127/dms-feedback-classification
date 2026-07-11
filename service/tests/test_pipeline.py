from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from support import write_baseline_artifacts

from dms.metrics import MetricsCollector
from dms.pipeline.issue_classifier import (
    IssueClassifier,
    get_label_config_snapshot,
    normalize_issue_output,
)
from dms.pipeline.rag_product import RAGProductMatcher
from dms.pipeline.runner import PipelineRunner, detect_header_and_textcol


class FakeGemini:
    def __init__(self):
        self.calls = []

    def generate(self, prompt, temperature=None):
        self.calls.append(("generate", prompt))
        labels = get_label_config_snapshot()["minor_order"]
        return FakeGeminiResponse(
            json.dumps(
                {
                    "final_minors": [labels[0]],
                    "sentiment": "Tiêu cực",
                    "brand": "",
                    "decision_log": [],
                },
                ensure_ascii=False,
            )
        )

    def generate_json(self, prompt, temperature=0.0):
        self.calls.append(("generate_json", prompt))
        labels = get_label_config_snapshot()["minor_order"]
        return FakeGeminiResponse(
            json.dumps(
                {
                    "final_minors": [labels[0], labels[15]],
                    "sentiment": "Tiêu cực",
                    "brand": "",
                    "decision_log": [],
                },
                ensure_ascii=False,
            )
        )


class FakeGeminiResponse:
    def __init__(self, text: str):
        self.text = text
        self.usage = {
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
        }


class SequenceGemini:
    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls = []

    def generate_json(self, prompt, temperature=0.0):
        self.calls.append(("generate_json", prompt))
        if self.responses:
            return FakeGeminiResponse(self.responses.pop(0))
        return FakeGeminiResponse("not json")

    def generate(self, prompt, temperature=0.0):
        self.calls.append(("generate", prompt))
        return FakeGeminiResponse("not json")


class ResumeRAG:
    def __init__(self):
        self._last_usage = {}

    def retrieve_batch(self, texts):
        return [
            {
                "LLM_Extracted": "",
                "Model": "",
                "Dòng SP": "",
                "Sản phẩm": "",
                "Score": 0.0,
                "Evidence": "",
                "Src": "NONE",
            }
            for _ in texts
        ]

    def enrich_with_keyword_fallbacks(self, rag_batch, texts):
        return rag_batch


class ResumeIssueClassifier:
    def __init__(self):
        self.seen_texts: list[str] = []
        self._last_usage = {}

    def classify_batch(self, texts, matched_products=None, debug=False, cancellation_check=None):
        self.seen_texts.extend(texts)
        labels = get_label_config_snapshot()["minor_order"]
        return [
            {
                "final_minors": [labels[-1]],
                "sentiment": "",
                "brand": "",
                "decision_log": [],
            }
            for _ in texts
        ]


def build_catalog(path: Path) -> None:
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame(
            [
                {"Model": "AT10 8W", "Dòng SP": "Bulb", "Sản phẩm": "Den LED"},
            ]
        ).to_excel(writer, index=False, sheet_name="Products")
        pd.DataFrame(
            [{"Keyword": "bóng đèn", "Dòng SP": "Bulb", "Sản phẩm": "Den LED", "Priority": 1}]
        ).to_excel(writer, index=False, sheet_name="Loc lan 2")
        pd.DataFrame([{"Keyword": "phich cam", "Sản phẩm": "Phich", "Priority": 1}]).to_excel(
            writer,
            index=False,
            sheet_name="Loc lan 3",
        )


def test_issue_normalization_filters_labels():
    labels = get_label_config_snapshot()["minor_order"]
    result = normalize_issue_output(
        {
            "final_minors": [labels[16], labels[15], labels[0]],
            "sentiment": "Positive",
            "brand": "",
            "decision_log": [{"minor": labels[0], "action": "keep", "why": "x"}],
        }
    )
    assert set(result["final_minors"]) == {labels[15], labels[0]}
    assert result["sentiment"] == ""


def test_detect_header_and_text_column():
    raw = pd.DataFrame([["meta", "x"], ["Nội dung phản hồi", "Khác"], ["đèn lỗi", "1"]])
    fixed, text_col = detect_header_and_textcol(raw)
    assert text_col == "Nội dung phản hồi"
    assert fixed.iloc[0][text_col] == "đèn lỗi"


def test_rag_bm25_and_keyword_fallback(settings, tmp_path: Path):
    settings.data_dir = tmp_path / "data"
    settings.bm25_min_score = 0.0
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.keyword_dir.mkdir(parents=True, exist_ok=True)
    build_catalog(settings.df_products_path)

    gemini = FakeGemini()
    rag = RAGProductMatcher(settings=settings, gemini=gemini)
    result = rag.bm25_search_dual("AT10 8W")
    assert result[0]["Model"] == "AT10 8W"

    fallback = rag.enrich_with_keyword_fallbacks(
        [
            {
                "LLM_Extracted": "",
                "Model": "",
                "Dòng SP": "",
                "Sản phẩm": "",
                "Score": 0.0,
                "Evidence": "",
                "Src": "NONE",
            }
        ],
        ["Tôi muốn bóng đèn mới"],
    )
    assert fallback[0]["Sản phẩm"] == "Den LED"


def test_pipeline_runner_processes_file_with_prelim_handoff(settings, tmp_path: Path):
    settings.data_dir = tmp_path / "data"
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.keyword_dir.mkdir(parents=True, exist_ok=True)
    settings.model_dir_override = tmp_path / "Model"
    build_catalog(settings.df_products_path)
    write_baseline_artifacts(settings.model_dir, settings.keyword_dir, include_keyword_minors=True)

    metrics = MetricsCollector(tmp_path / "metrics.json")
    gemini = FakeGemini()
    rag = RAGProductMatcher(settings=settings, gemini=gemini)
    runner = PipelineRunner(
        gemini=gemini,
        rag=rag,
        metrics=metrics,
        settings=settings,
    )

    input_path = tmp_path / "input.xlsx"
    pd.DataFrame({"Nội dung phản hồi": ["AT10 8W website bị lỗi"]}).to_excel(
        input_path, index=False
    )

    result = runner.run_pipeline(input_path, tmp_path / "out.xlsx", tmp_path / "ckpt.json")
    assert result["total_rows"] == 1
    assert (tmp_path / "out.xlsx").exists()
    assert any(
        "matched_product" in prompt for kind, prompt in gemini.calls if kind == "generate_json"
    )
    assert metrics.gemini_calls == 2
    assert metrics.total_prompt_tokens == 2
    assert metrics.total_completion_tokens == 2


def test_issue_classifier_prompt_inverted_cot():
    # Verify that normalize_issue_output works with the inverted JSON output structure
    labels = get_label_config_snapshot()["minor_order"]
    raw_parsed = {
        "row_index": 0,
        "brand": "Asia",
        "is_competitor": True,
        "sentiment": "Tiêu cực",
        "decision_log": [
            {"minor": labels[0], "action": "ADD", "why": "x"},
            {"minor": labels[16], "action": "ADD", "why": "Asia"},
        ],
        "labels": {labels[0]: True, labels[16]: True, labels[15]: False},
    }

    normalized = normalize_issue_output(raw_parsed)
    assert normalized["brand"] == "Asia"
    assert normalized["sentiment"] == "Tiêu cực"
    assert labels[0] in normalized["final_minors"]
    assert labels[16] in normalized["final_minors"]
    assert labels[15] not in normalized["final_minors"]


def test_pipeline_resume_checkpoint_does_not_duplicate_rows(settings, tmp_path):
    settings.llm_batch_size = 1
    settings.ckpt_every = 1
    metrics = MetricsCollector(tmp_path / "metrics.json")
    issue_classifier = ResumeIssueClassifier()
    runner = PipelineRunner(
        rag=ResumeRAG(),
        gemini=FakeGemini(),
        metrics=metrics,
        settings=settings,
        issue_classifier=issue_classifier,
    )
    input_path = tmp_path / "input.xlsx"
    output_path = tmp_path / "out.xlsx"
    ckpt_path = tmp_path / "ckpt.json"
    pd.DataFrame({"Nội dung phản hồi": ["first", "second", "third"]}).to_excel(
        input_path, index=False
    )

    raw = pd.read_excel(input_path, header=None, dtype=str)
    df_all, text_col = detect_header_and_textcol(raw, scan_rows=10)
    insert_pos = list(df_all.columns).index(text_col)
    for idx, col in enumerate(["Sản phẩm", "Dòng SP", "Model", "Lớp", "Điểm"]):
        df_all.insert(insert_pos + idx, col, "")
    base_cols = list(df_all.columns)
    all_cols = base_cols + get_label_config_snapshot()["minor_order"] + [
        "Sentiment",
        "LLM_Extracted",
        "BM25_Score",
    ]
    first_row = {col: "" for col in all_cols}
    first_row[text_col] = "first"
    PipelineRunner._save_output([first_row], all_cols, output_path)
    ckpt_path.write_text(json.dumps({"last_index": 1}), encoding="utf-8")

    result = runner.run_pipeline(input_path, output_path, ckpt_path)

    resumed = pd.read_excel(output_path, header=None, skiprows=2)
    assert result["processed_rows"] == 3
    assert len(resumed) == 3
    assert issue_classifier.seen_texts == ["second", "third"]


def test_issue_classifier_typo_guard_and_brand_fallback():
    # Typo guard scenario should not trigger reward-related labels.
    labels = get_label_config_snapshot()["minor_order"]
    raw_parsed = {
        "row_index": 2,
        "brand": "",
        "is_competitor": False,
        "sentiment": "Tiêu cực",
        "decision_log": [
            {
                "minor": labels[-1],
                "action": "ADD",
                "why": "typo guard",
            }
        ],
        "labels": {labels[-1]: True, labels[9]: False},
    }

    normalized = normalize_issue_output(raw_parsed)
    assert normalized["brand"] == ""
    assert normalized["sentiment"] == "Tiêu cực"
    assert labels[-1] in normalized["final_minors"]
    assert labels[9] not in normalized["final_minors"]


def test_issue_normalization_accepts_malformed_shapes():
    labels = get_label_config_snapshot()["minor_order"]
    result = normalize_issue_output(
        {
            "labels": [labels[0]],
            "decision_log": "not-a-list",
        }
    )
    assert labels[0] in result["final_minors"]
    assert result["decision_log"] == []

    fallback = normalize_issue_output(["not", "a", "dict"])
    assert fallback["final_minors"] == [labels[-1]]


def test_issue_classifier_fail_soft_for_malformed_json(settings):
    classifier = IssueClassifier(SequenceGemini(["this is not json"]), settings)

    result = classifier.classify_batch(["bad row"])

    assert len(result) == 1
    assert result[0]["final_minors"] == [get_label_config_snapshot()["minor_order"][-1]]


def test_issue_classifier_preserves_valid_rows_with_malformed_item(settings):
    labels = get_label_config_snapshot()["minor_order"]
    raw = json.dumps(
        [
            {
                "row_index": 0,
                "labels": {labels[0]: True},
                "sentiment": "",
                "brand": "",
                "decision_log": [],
            },
            "not an object",
        ],
        ensure_ascii=False,
    )
    classifier = IssueClassifier(SequenceGemini([raw, "not json"]), settings)

    result = classifier.classify_batch(["valid row", "bad row"])

    assert labels[0] in result[0]["final_minors"]
    assert result[1]["final_minors"] == [labels[-1]]


def test_issue_classifier_maps_scrambled_row_index(settings):
    labels = get_label_config_snapshot()["minor_order"]
    raw = json.dumps(
        [
            {
                "row_index": 1,
                "labels": {labels[15]: True},
                "sentiment": "",
                "brand": "",
                "decision_log": [],
            },
            {
                "row_index": 0,
                "labels": {labels[0]: True},
                "sentiment": "",
                "brand": "",
                "decision_log": [],
            },
        ],
        ensure_ascii=False,
    )
    classifier = IssueClassifier(SequenceGemini([raw]), settings)

    result = classifier.classify_batch(["first", "second"])

    assert labels[0] in result[0]["final_minors"]
    assert labels[15] in result[1]["final_minors"]

