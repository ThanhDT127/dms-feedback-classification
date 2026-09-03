"""Pipeline runner for a single Excel file."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl.utils import get_column_letter

from ..analytics.input_reader import TEXT_ALIASES as TEXT_ALIASES  # noqa: F401
from ..analytics.input_reader import _canon_lower as _canon_lower  # noqa: F401
from ..analytics.input_reader import (
    detect_header_and_textcol as detect_header_and_textcol,  # noqa: F401
)
from ..analytics.input_reader import read_feedback_workbook
from ..exceptions import PipelineCancelled, PipelineError
from ..gemini_client import GeminiClient
from ..metrics import MetricsCollector
from ..settings import Settings
from ..time_utils import utc_now_iso
from ..usage_tracker import UsageTracker, calculate_cost
from .excel_formatter import write_formatted_header
from .issue_classifier import MINOR_ORDER, IssueClassifier
from .rag_product import RAGProductMatcher

logger = logging.getLogger("dms-watcher")


class PipelineRunner:
    """Run the full classification pipeline for one Excel workbook."""

    def __init__(
        self,
        gemini: GeminiClient,
        rag: RAGProductMatcher,
        metrics: MetricsCollector,
        settings: Settings,
        issue_classifier: IssueClassifier | None = None,
        usage_tracker: UsageTracker | None = None,
    ) -> None:
        self.gemini = gemini
        self.rag = rag
        self.metrics = metrics
        self.settings = settings
        self.issue_classifier = issue_classifier or IssueClassifier(
            gemini=gemini, settings=settings
        )
        self.usage_tracker = usage_tracker
        self._pricing_config = self._load_pricing_config()

    def _load_pricing_config(self) -> dict:
        """Parse the JSON pricing config from settings."""
        import json as _json

        try:
            return _json.loads(self.settings.gemini_model_pricing)
        except Exception:
            return {}

    def _run_rag_with_retry(
        self,
        batch_texts: list[str],
        cancellation_check: Callable[[], bool] | None = None,
    ) -> list[dict]:
        if cancellation_check is not None and cancellation_check():
            raise PipelineCancelled("Classification job was cancelled.")
        try:
            return self.rag.retrieve_batch(batch_texts)
        except Exception as exc:
            if cancellation_check is not None and cancellation_check():
                raise PipelineCancelled("Classification job was cancelled.") from exc
            logger.warning("RAG error after provider retries: %s", exc)
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
            for _ in batch_texts
        ]

    def run_pipeline(
        self,
        input_path: str | Path,
        output_path: str | Path,
        ckpt_path: str | Path,
        progress_callback: Callable[..., Any] | None = None,
        cancellation_check: Callable[[], bool] | None = None,
        job_id: str | None = None,
    ) -> dict:
        try:
            return self._run_pipeline(
                input_path,
                output_path,
                ckpt_path,
                progress_callback,
                cancellation_check,
                job_id=job_id,
            )
        except PipelineCancelled:
            raise
        except Exception as exc:
            raise PipelineError(str(exc)) from exc

    def _run_pipeline(
        self,
        input_path: str | Path,
        output_path: str | Path,
        ckpt_path: str | Path,
        progress_callback: Callable[..., Any] | None = None,
        cancellation_check: Callable[[], bool] | None = None,
        job_id: str | None = None,
    ) -> dict:
        self._current_job_id = job_id
        input_path = Path(input_path)
        output_path = Path(output_path)
        ckpt_path = Path(ckpt_path)
        t_start = time.time()

        logger.info("Reading input file: %s", input_path)
        parsed_input = read_feedback_workbook(input_path)
        df_all, text_col = parsed_input.dataframe, parsed_input.text_column

        insert_pos = list(df_all.columns).index(text_col)
        for idx, col in enumerate(["Sản phẩm", "Dòng SP", "Model", "Lớp", "Điểm"]):
            if col not in df_all.columns:
                df_all.insert(insert_pos + idx, col, "")

        base_cols = list(df_all.columns)
        extra_cols = ["Sentiment", "LLM_Extracted", "BM25_Score"]
        all_cols = base_cols + MINOR_ORDER + extra_cols

        start_idx = 0
        rows_out = []
        if ckpt_path.exists():
            try:
                meta = json.loads(ckpt_path.read_text(encoding="utf-8"))
                start_idx = int(meta.get("last_index", 0))
                logger.info("Resuming from checkpoint index %d", start_idx)
            except Exception as exc:
                logger.warning("Cannot read checkpoint: %s", exc)

        if start_idx > 0 and output_path.exists():
            try:
                df_resume = pd.read_excel(output_path, header=None, skiprows=2)
                df_resume.columns = all_cols[: df_resume.shape[1]]
                rows_out = df_resume.to_dict("records")
                logger.info("Loaded %d previously processed rows", len(rows_out))
            except Exception as exc:
                logger.warning("Cannot read previous output: %s", exc)

        label_counts = {m: 0 for m in MINOR_ORDER}
        for row in rows_out:
            for minor in MINOR_ORDER:
                val = row.get(minor, "")
                if val and str(val).strip() != "":
                    label_counts[minor] += 1

        texts = df_all[text_col].fillna("").astype(str).tolist()
        n_total = len(texts)
        if progress_callback is not None:
            progress_callback(start_idx, n_total, [])
        batch_index = start_idx // self.settings.llm_batch_size

        for i in range(start_idx, n_total, self.settings.llm_batch_size):
            if cancellation_check is not None and cancellation_check():
                raise PipelineCancelled("Classification job was cancelled.")
            batch = texts[i : i + self.settings.llm_batch_size]
            batch_index += 1
            logger.info("Batch %d: rows %d-%d", batch_index, i, i + len(batch) - 1)
            t0 = time.time()

            t_rag_s = time.time()
            if progress_callback is not None:
                try:
                    progress_callback(step=1, step_status="running")
                except Exception:
                    pass
            if cancellation_check is not None and cancellation_check():
                raise PipelineCancelled("Classification job was cancelled.")
            rag_batch = self._run_rag_with_retry(batch, cancellation_check)

            # Track RAG usage
            rag_usage = self.rag._last_usage
            if rag_usage:
                rag_cost = calculate_cost(
                    self.settings.gemini_model,
                    rag_usage.get("prompt_tokens", 0),
                    rag_usage.get("completion_tokens", 0),
                    self._pricing_config,
                )
                self.metrics.record_gemini_call(
                    prompt_tokens=rag_usage.get("prompt_tokens", 0),
                    completion_tokens=rag_usage.get("completion_tokens", 0),
                    cost_usd=rag_cost,
                )
                if self.usage_tracker:
                    self.usage_tracker.record(
                        model=self.settings.gemini_model,
                        call_type="rag_extract",
                        prompt_tokens=rag_usage.get("prompt_tokens", 0),
                        completion_tokens=rag_usage.get("completion_tokens", 0),
                        total_tokens=rag_usage.get("total_tokens", 0),
                        estimated_cost_usd=rag_cost,
                        job_id=self._current_job_id,
                    )

            if progress_callback is not None:
                try:
                    progress_callback(step=2, step_status="running")
                except Exception:
                    pass
            rag_batch = self.rag.enrich_with_keyword_fallbacks(rag_batch, batch)
            logger.info("  RAG time: %.2fs", time.time() - t_rag_s)

            time.sleep(self.settings.rate_gap_sec)

            t_issue_s = time.time()
            if progress_callback is not None:
                try:
                    progress_callback(step=3, step_status="running")
                except Exception:
                    pass
            if cancellation_check is not None and cancellation_check():
                raise PipelineCancelled("Classification job was cancelled.")
            try:
                # Call pure-LLM classify_batch directly using RAG products as hints
                issue_list = self.issue_classifier.classify_batch(
                    batch,
                    matched_products=rag_batch,
                    cancellation_check=cancellation_check,
                )
                usage = self.issue_classifier._last_usage
                cost = calculate_cost(
                    self.settings.gemini_model,
                    usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0),
                    self._pricing_config,
                )
                self.metrics.record_gemini_call(
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                    cost_usd=cost,
                )
                if self.usage_tracker:
                    self.usage_tracker.record(
                        model=self.settings.gemini_model,
                        call_type="classify_batch",
                        prompt_tokens=usage.get("prompt_tokens", 0),
                        completion_tokens=usage.get("completion_tokens", 0),
                        total_tokens=usage.get("total_tokens", 0),
                        estimated_cost_usd=cost,
                        job_id=self._current_job_id,
                    )
            except PipelineCancelled:
                raise
            except Exception as exc:
                logger.error(
                    "Issue classifier batch error: %s -> retrying %d rows individually",
                    exc,
                    len(batch),
                )
                # Sequential per-row retry with individual fallback
                issue_list = []
                for row_idx, (text, rag_item) in enumerate(zip(batch, rag_batch, strict=False)):
                    if cancellation_check is not None and cancellation_check():
                        raise PipelineCancelled("Classification job was cancelled.") from exc
                    try:
                        single_result = self.issue_classifier.classify_batch(
                            [text],
                            matched_products=[rag_item],
                            cancellation_check=cancellation_check,
                        )
                        issue_list.append(single_result[0] if single_result else {})
                    except PipelineCancelled:
                        raise
                    except Exception as row_exc:
                        logger.warning(
                            "Single-row retry at batch offset %d failed: %s", row_idx, row_exc
                        )
                        issue_list.append(
                            {
                                "final_minors": ["Tin trung lập"],
                                "sentiment": "",
                                "brand": "",
                                "decision_log": [
                                    {
                                        "minor": "__ALL__",
                                        "action": "KEEP",
                                        "why": f"FALLBACK_INDIVIDUAL_ERROR: {row_exc}",
                                    }
                                ],
                            }
                        )
            logger.info("  Issue classify time: %.2fs", time.time() - t_issue_s)

            for j, (_, rag, issue) in enumerate(zip(batch, rag_batch, issue_list, strict=False)):
                base_row = df_all.iloc[i + j].to_dict()
                labels_minor = {m: False for m in MINOR_ORDER}
                for minor in issue.get("final_minors", []):
                    if minor in labels_minor:
                        labels_minor[minor] = True

                sentiment = issue.get("sentiment", "") or ""
                brand_disp = issue.get("brand", "") or ""
                for minor in MINOR_ORDER:
                    if minor == "Hãng" and labels_minor.get(minor, False):
                        base_row[minor] = brand_disp if brand_disp else "x"
                    else:
                        base_row[minor] = "x" if labels_minor.get(minor, False) else ""
                    if labels_minor.get(minor, False):
                        label_counts[minor] += 1
                base_row["Sentiment"] = sentiment

                llm_val = (rag.get("LLM_Extracted", "") or "").strip()
                if llm_val.upper() == "NONE":
                    llm_val = ""

                best_model = (rag.get("Model", "") or "").strip()
                best_line = (rag.get("Dòng SP", "") or "").strip()
                best_cat = (rag.get("Sản phẩm", "") or "").strip()
                best_score = rag.get("Score", 0.0) or 0.0
                best_src = (rag.get("Src", "") or "").strip()

                if not best_cat and not best_line:
                    base_row["Model"] = ""
                    base_row["BM25_Score"] = ""
                    base_row["Điểm"] = ""
                else:
                    base_row["Sản phẩm"] = best_cat or base_row.get("Sản phẩm", "")
                    base_row["Dòng SP"] = best_line or base_row.get("Dòng SP", "")
                    base_row["Model"] = best_model or base_row.get("Model", "")
                    base_row["BM25_Score"] = (
                        float(best_score) if best_src == "RAG" and best_score > 0 else ""
                    )
                    base_row["Điểm"] = ""

                base_row["LLM_Extracted"] = llm_val
                for col in all_cols:
                    if col not in base_row:
                        base_row[col] = ""
                rows_out.append({col: base_row[col] for col in all_cols})

            done = i + len(batch)
            logger.info("  Batch total time: %.2fs", time.time() - t0)
            if (done % self.settings.ckpt_every == 0) or (done >= n_total):
                self._save_output(rows_out, all_cols, output_path)
                self._save_checkpoint(ckpt_path, done)
                logger.info("Checkpoint %d/%d -> %s", done, n_total, ckpt_path)

            if cancellation_check is not None and cancellation_check():
                raise PipelineCancelled("Classification job was cancelled.")

            if progress_callback is not None:
                new_results_batch = []
                for idx_in_batch in range(len(batch)):
                    base_row = df_all.iloc[i + idx_in_batch].to_dict()
                    rag = rag_batch[idx_in_batch]
                    issue = issue_list[idx_in_batch]

                    labels_minor = {m: False for m in MINOR_ORDER}
                    for minor in issue.get("final_minors", []):
                        if minor in labels_minor:
                            labels_minor[minor] = True

                    sentiment = issue.get("sentiment", "") or ""
                    best_cat = (rag.get("Sản phẩm", "") or "").strip()
                    best_line = (rag.get("Dòng SP", "") or "").strip()
                    best_model = (rag.get("Model", "") or "").strip()
                    best_score = rag.get("Score", 0.0) or 0.0
                    brand = (issue.get("brand", "") or "").strip()

                    new_results_batch.append(
                        {
                            "source_row_number": parsed_input.source_row_numbers[i + idx_in_batch],
                            "text": batch[idx_in_batch],
                            "product": best_cat,
                            "product_line": best_line,
                            "model": best_model,
                            "bm25_score": round(float(best_score), 1) if best_score else 0,
                            "sentiment": sentiment,
                            "labels": [m for m in MINOR_ORDER if labels_minor[m]],
                            "brand": brand,
                        }
                    )
                try:
                    progress_callback(done, n_total, new_results_batch, step=3, step_status="done")
                except TypeError:
                    progress_callback(done, n_total, new_results_batch)

        duration = time.time() - t_start
        self._current_job_id = None  # Clear job_id after pipeline completes
        logger.info("Pipeline complete: %d rows in %.1fs -> %s", n_total, duration, output_path)
        return {
            "total_rows": n_total,
            "processed_rows": len(rows_out),
            "output_path": str(output_path),
            "duration_seconds": round(duration, 1),
            "label_distribution": label_counts,
        }

    @staticmethod
    def _save_output(rows_out: list[dict], all_cols: list[str], output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        df_cur = pd.DataFrame(rows_out)[all_cols]
        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            df_cur.to_excel(writer, index=False, startrow=2, header=False)
            ws = writer.sheets["Sheet1"]
            write_formatted_header(ws, df_cur)
            for col_idx in range(1, len(df_cur.columns) + 1):
                letter = get_column_letter(col_idx)
                max_len = max((len(str(v)) for v in df_cur.iloc[:, col_idx - 1]), default=10)
                ws.column_dimensions[letter].width = min(70, max(12, int(max_len * 1.1)))

    @staticmethod
    def _save_checkpoint(ckpt_path: Path, last_index: int) -> None:
        ckpt_path.parent.mkdir(parents=True, exist_ok=True)
        ckpt_path.write_text(
            json.dumps(
                {"last_index": last_index, "timestamp": utc_now_iso()},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
