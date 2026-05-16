from __future__ import annotations

from pathlib import Path

import pandas as pd
from support import write_baseline_artifacts

from dms.metrics import MetricsCollector
from dms.pipeline.baseline_classifier import BaselineIssueClassifier
from dms.pipeline.issue_classifier import normalize_issue_output
from dms.pipeline.rag_product import RAGProductMatcher
from dms.pipeline.runner import PipelineRunner, detect_header_and_textcol


class FakeGemini:
    def __init__(self):
        self.calls = []

    def generate(self, prompt, temperature=None):
        self.calls.append(("generate", prompt))
        return '{"final_minors":["Báo lỗi"],"sentiment":"Tiêu cực","brand":"","decision_log":[]}'

    def generate_json(self, prompt, temperature=0.0):
        self.calls.append(("generate_json", prompt))
        return '{"final_minors":["Báo lỗi","Website"],"sentiment":"Tiêu cực","brand":"","decision_log":[]}'


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
    result = normalize_issue_output(
        {
            "final_minors": ["Hãng", "Website", "Báo lỗi"],
            "sentiment": "Positive",
            "brand": "Rạng Đông",
            "decision_log": [{"minor": "Báo lỗi", "action": "keep", "why": "x"}],
        }
    )
    assert result["final_minors"] == ["Website", "Báo lỗi"]
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
        [{"LLM_Extracted": "", "Model": "", "Dòng SP": "", "Sản phẩm": "", "Score": 0.0, "Evidence": "", "Src": "NONE"}],
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
    baseline = BaselineIssueClassifier(settings=settings)
    runner = PipelineRunner(
        gemini=gemini,
        rag=rag,
        metrics=metrics,
        settings=settings,
        baseline_classifier=baseline,
    )

    input_path = tmp_path / "input.xlsx"
    pd.DataFrame({"Nội dung phản hồi": ["AT10 8W website bị lỗi"]}).to_excel(input_path, index=False)

    result = runner.run_pipeline(input_path, tmp_path / "out.xlsx", tmp_path / "ckpt.json")
    assert result["total_rows"] == 1
    assert (tmp_path / "out.xlsx").exists()
    assert any("prelim_minors" in prompt for kind, prompt in gemini.calls if kind == "generate_json")
