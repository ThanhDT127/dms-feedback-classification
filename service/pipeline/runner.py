"""
Pipeline runner — orchestrates the full classification pipeline for a single file.

Ported from notebook Cell 11 (Main pipeline).
Flow: load Excel → detect text column → RAG product match → Issue classify → Excel output
"""
import os
import re
import json
import time
import numpy as np
import pandas as pd
from datetime import datetime
from unidecode import unidecode
from openpyxl.utils import get_column_letter

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    LLM_BATCH_SIZE,
    CKPT_EVERY,
    MAX_RETRY,
    BASE_WAIT,
    RATE_GAP_SEC,
    WORK_DIR,
    logger,
)
from pipeline.rag_product import RAGProductMatcher
from pipeline.issue_classifier import MINOR_ORDER, MINOR_TO_MAJOR, llm_issue_classify_batch
from pipeline.excel_formatter import write_formatted_header

# Lazy import to avoid circular dependency
def _get_metrics():
    try:
        from watcher import metrics
        return metrics
    except ImportError:
        return None


# ── Text column detection helpers ────────────────────────────────────────────
TEXT_ALIASES = [
    "nội dung", "noi dung", "nội dung vấn đề", "noi dung van de",
    "nội dung phản hồi", "noi dung phan hoi"
]


def _canon_lower(s: str) -> str:
    return re.sub(r"\s+", " ", unidecode(str(s or "")).lower().strip())


def _is_numeric_like(x) -> bool:
    if x is None:
        return True
    s = str(x).strip()
    if not s:
        return True
    return bool(re.fullmatch(r"[\d\-\./:, ]+", s))


def _score_textiness(vals) -> float:
    vals = [str(v) for v in vals if str(v).strip()]
    if not vals:
        return -1.0
    lens = [len(v) for v in vals]
    mean_len = np.mean(lens) if lens else 0
    has_space = np.mean([1.0 if (" " in v) else 0.0 for v in vals])
    non_num = np.mean([0.0 if _is_numeric_like(v) else 1.0 for v in vals])
    return float(mean_len * 0.7 + has_space * 20 + non_num * 30)


def detect_header_and_textcol(raw_df: pd.DataFrame, scan_rows: int = 10):
    """
    Auto-detect the header row and text content column.

    Returns:
        (df_fixed, text_col_name): DataFrame with proper headers and the detected text column name.
    """
    n_scan = min(scan_rows, len(raw_df))
    best_row_idx, best_col_idx = None, None

    for r in range(n_scan):
        row_vals = raw_df.iloc[r, :].tolist()
        for c, v in enumerate(row_vals):
            cval = _canon_lower(v)
            for alias in TEXT_ALIASES:
                if alias in cval and len(cval) <= len(alias) + 20:
                    best_row_idx, best_col_idx = r, c
                    break
            if best_row_idx is not None:
                break
        if best_row_idx is not None:
            break

    if best_row_idx is not None:
        header_vals = [
            str(x).strip() if str(x).strip() else f"col_{i}"
            for i, x in enumerate(raw_df.iloc[best_row_idx, :].tolist())
        ]
        df_fixed = raw_df.iloc[best_row_idx + 1:, :].copy()
        df_fixed.columns = header_vals
        text_col_name = None
        for col in df_fixed.columns:
            if any(al in _canon_lower(col) for al in TEXT_ALIASES):
                text_col_name = col
                break
        if text_col_name is None and best_col_idx is not None and best_col_idx < len(df_fixed.columns):
            text_col_name = df_fixed.columns[best_col_idx]
        return df_fixed.reset_index(drop=True), text_col_name

    # Fallback: pick column with highest "textiness" score
    tmp = raw_df.copy().reset_index(drop=True)
    tmp.columns = [f"col_{i}" for i in range(tmp.shape[1])]
    scores = {}
    for j in range(tmp.shape[1]):
        col_vals = tmp.iloc[:n_scan, j].tolist()
        if all((str(v).strip() == "" or pd.isna(v)) for v in col_vals):
            scores[j] = -1.0
        else:
            scores[j] = _score_textiness(col_vals)
    best_j = max(scores, key=scores.get) if scores else 0
    text_col_name = tmp.columns[best_j]
    return tmp.copy(), text_col_name


# ── Singleton RAG matcher ───────────────────────────────────────────────────
_rag_matcher: RAGProductMatcher | None = None


def _get_rag_matcher() -> RAGProductMatcher:
    global _rag_matcher
    if _rag_matcher is None:
        _rag_matcher = RAGProductMatcher()
    return _rag_matcher


def _run_rag_with_retry(matcher: RAGProductMatcher, batch_texts: list[str]) -> list[dict]:
    """Run RAG with retry logic."""
    m = _get_metrics()
    retries = 0
    for attempt in range(1, MAX_RETRY + 1):
        try:
            result = matcher.retrieve_batch(batch_texts)
            if m:
                m.record_gemini_call(retries=retries)
            return result
        except Exception as e:
            retries += 1
            wait = BASE_WAIT * attempt
            logger.warning("RAG error (%d/%d): %s → sleep %.1fs", attempt, MAX_RETRY, e, wait)
            time.sleep(wait)
    if m:
        m.record_gemini_call(retries=retries)
    return [
        {"LLM_Extracted": "", "Model": "", "Dòng SP": "", "Sản phẩm": "",
         "Score": 0.0, "Evidence": "", "Src": "NONE"}
        for _ in batch_texts
    ]


def run_pipeline(input_path: str, output_path: str, ckpt_path: str) -> dict:
    """
    Run the full classification pipeline on a single Excel file.

    Args:
        input_path: Path to input .xlsx file.
        output_path: Path to write output .xlsx file.
        ckpt_path: Path for checkpoint JSON file.

    Returns:
        Dict with keys: total_rows, processed_rows, output_path, duration_seconds
    """
    t_start = time.time()
    matcher = _get_rag_matcher()

    # ── Read input ──
    logger.info("Reading input file: %s", input_path)
    _raw = pd.read_excel(input_path, header=None, dtype=str)
    df_all, TEXT_COL = detect_header_and_textcol(_raw, scan_rows=10)

    if not TEXT_COL:
        df_all = pd.read_excel(input_path)
        TEXT_COL = next((c for c in df_all.columns if "nội dung" in _canon_lower(c)), None)

    assert TEXT_COL, f"Cannot find text column in {input_path}. Check file format."
    logger.info("Text column: '%s' | shape=%s", TEXT_COL, df_all.shape)

    # ── Insert product columns ──
    insert_pos = list(df_all.columns).index(TEXT_COL)
    for k, col in enumerate(["Sản phẩm", "Dòng SP", "Model", "Lớp", "Điểm"]):
        if col not in df_all.columns:
            df_all.insert(insert_pos + k, col, "")

    BASE_COLS = list(df_all.columns)
    EXTRA_COLS = ["Sentiment", "LLM_Extracted", "BM25_Score"]
    ALL_COLS = BASE_COLS + MINOR_ORDER + EXTRA_COLS

    # ── Resume from checkpoint ──
    start_idx = 0
    rows_out = []

    if os.path.exists(ckpt_path):
        try:
            with open(ckpt_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
                start_idx = int(meta.get("last_index", 0))
                logger.info("Resuming from checkpoint index %d", start_idx)
        except Exception as e:
            logger.warning("Cannot read checkpoint: %s", e)

    if start_idx > 0 and os.path.exists(output_path):
        try:
            df_resume = pd.read_excel(output_path, header=None, skiprows=2)
            df_resume.columns = ALL_COLS[:df_resume.shape[1]]
            rows_out = df_resume.to_dict("records")
            logger.info("Loaded %d previously processed rows", len(rows_out))
        except Exception as e:
            logger.warning("Cannot read previous output: %s", e)

    # ── Process batches ──
    texts = df_all[TEXT_COL].fillna("").astype(str).tolist()
    n_total = len(texts)

    batch_index = start_idx // LLM_BATCH_SIZE

    for i in range(start_idx, n_total, LLM_BATCH_SIZE):
        batch = texts[i:i + LLM_BATCH_SIZE]
        batch_index += 1
        logger.info("Batch %d: rows %d–%d", batch_index, i, i + len(batch) - 1)
        t0 = time.time()

        # ── RAG product matching ──
        t_rag_s = time.time()
        rag_batch = _run_rag_with_retry(matcher, batch)
        rag_batch = matcher.enrich_with_keyword_fallbacks(rag_batch, batch)
        t_rag_e = time.time()
        logger.info("  RAG time: %.2fs", t_rag_e - t_rag_s)

        # ── Rate limit gap ──
        logger.debug("  Sleeping %.1fs before issue classifier", RATE_GAP_SEC)
        time.sleep(RATE_GAP_SEC)

        # ── Issue classification ──
        t_issue_s = time.time()
        try:
            issue_list = llm_issue_classify_batch(batch)
            m = _get_metrics()
            if m:
                m.record_gemini_call()
        except Exception as e:
            logger.error("Issue classifier error: %s → using empty output", e)
            issue_list = [
                {"final_minors": [], "sentiment": "", "brand": "", "decision_log": []}
                for _ in range(len(batch))
            ]
        t_issue_e = time.time()
        logger.info("  Issue classify time: %.2fs", t_issue_e - t_issue_s)

        # ── Build output rows ──
        for j, (text, rag, issue) in enumerate(zip(batch, rag_batch, issue_list)):
            base_row = df_all.iloc[i + j].to_dict()

            # Issue labels
            labels_minor = {m: False for m in MINOR_ORDER}
            for m in issue.get("final_minors", []):
                if m in labels_minor:
                    labels_minor[m] = True

            sentiment = issue.get("sentiment", "") or ""
            brand_disp = issue.get("brand", "") or ""

            for mn in MINOR_ORDER:
                if mn == "Hãng" and labels_minor.get(mn, False):
                    base_row[mn] = brand_disp if brand_disp else "x"
                else:
                    base_row[mn] = "x" if labels_minor.get(mn, False) else ""
            base_row["Sentiment"] = sentiment

            # RAG product
            llm_val = (rag.get("LLM_Extracted", "") or "").strip()
            if llm_val.upper() == "NONE":
                llm_val = ""

            best_model = (rag.get("Model", "") or "").strip()
            best_line = (rag.get("Dòng SP", "") or "").strip()
            best_cat = (rag.get("Sản phẩm", "") or "").strip()
            best_score = rag.get("Score", 0.0) or 0.0
            best_src = (rag.get("Src", "") or "").strip()

            if (best_cat == "") and (best_line == ""):
                base_row["Sản phẩm"] = base_row.get("Sản phẩm", "")
                base_row["Dòng SP"] = base_row.get("Dòng SP", "")
                base_row["Model"] = ""
                base_row["BM25_Score"] = ""
                base_row["Điểm"] = ""
            else:
                base_row["Sản phẩm"] = best_cat or base_row.get("Sản phẩm", "")
                base_row["Dòng SP"] = best_line or base_row.get("Dòng SP", "")
                base_row["Model"] = best_model or base_row.get("Model", "")
                base_row["BM25_Score"] = float(best_score) if best_src == "RAG" and best_score > 0 else ""
                base_row["Điểm"] = ""

            base_row["LLM_Extracted"] = llm_val

            # Ensure all columns present
            for c in ALL_COLS:
                if c not in base_row:
                    base_row[c] = ""

            rows_out.append({c: base_row[c] for c in ALL_COLS})

        done = i + len(batch)
        t1 = time.time()
        logger.info("  Batch total time: %.2fs", t1 - t0)

        # ── Checkpoint ──
        if (done % CKPT_EVERY == 0) or (done >= n_total):
            _save_output(rows_out, ALL_COLS, output_path)
            _save_checkpoint(ckpt_path, done)
            logger.info("Checkpoint %d/%d → %s", done, n_total, ckpt_path)

    duration = time.time() - t_start
    logger.info("Pipeline complete: %d rows in %.1fs → %s", n_total, duration, output_path)

    return {
        "total_rows": n_total,
        "processed_rows": len(rows_out),
        "output_path": output_path,
        "duration_seconds": round(duration, 1),
    }


def _save_output(rows_out: list[dict], all_cols: list[str], output_path: str):
    """Save current results to formatted Excel file."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    df_cur = pd.DataFrame(rows_out)[all_cols]
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        df_cur.to_excel(writer, index=False, startrow=2, header=False)
        ws = writer.sheets["Sheet1"]
        write_formatted_header(ws, df_cur)
        for col_idx in range(1, len(df_cur.columns) + 1):
            letter = get_column_letter(col_idx)
            max_len = max((len(str(v)) for v in df_cur.iloc[:, col_idx - 1]), default=10)
            ws.column_dimensions[letter].width = min(70, max(12, int(max_len * 1.1)))


def _save_checkpoint(ckpt_path: str, last_index: int):
    """Save checkpoint JSON."""
    os.makedirs(os.path.dirname(ckpt_path) or ".", exist_ok=True)
    with open(ckpt_path, "w", encoding="utf-8") as f:
        json.dump(
            {"last_index": last_index, "timestamp": datetime.now().isoformat()},
            f, ensure_ascii=False, indent=2,
        )
