"""Run pipeline on test files and compare output to old production results."""

import sys
import os
import time
from pathlib import Path

script_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(script_dir.parent / "src"))

import pandas as pd
from dms.settings import get_settings
from dms.gemini_client import GeminiClient
from dms.pipeline.rag_product import RAGProductMatcher
from dms.pipeline.runner import PipelineRunner
from dms.pipeline.issue_classifier import MINOR_ORDER
from dms.metrics import MetricsCollector

# Files to test
TEST_FILES = [
    "DMS-13102025.xlsx",
    "DMS-14102025.xlsx",
    "DMS-1510-1710.xlsx",
    "DMS-1810-1910.xlsx",
    "DMS-2010-2210.xlsx",
]

OLD_OUTPUT_DIR = script_dir / "Output"
NEW_OUTPUT_DIR = script_dir / "test_output"
CKPT_DIR = script_dir / "test_checkpoint"

def run_all():
    """Run pipeline on all test files."""
    NEW_OUTPUT_DIR.mkdir(exist_ok=True)
    CKPT_DIR.mkdir(exist_ok=True)

    settings = get_settings()
    active_keyword = settings.active_keyword_dir
    active_model = settings.active_model_dir
    runtime_settings = settings.model_copy(
        update={
            "keyword_dir_override": active_keyword if active_keyword.exists() else settings.keyword_dir,
            "model_dir_override": active_model if active_model.exists() else settings.model_dir,
            "llm_batch_size": 20,
            "rate_gap_sec": 4.0,
        }
    )
    
    gemini = GeminiClient(runtime_settings)
    rag = RAGProductMatcher(runtime_settings, gemini)
    metrics = MetricsCollector(runtime_settings.metrics_path)
    
    runner = PipelineRunner(
        gemini=gemini,
        rag=rag,
        metrics=metrics,
        settings=runtime_settings
    )
    
    for fname in TEST_FILES:
        input_path = script_dir / "Input" / fname
        output_path = NEW_OUTPUT_DIR / fname.replace(".xlsx", "_output.xlsx")
        ckpt_path = CKPT_DIR / fname.replace(".xlsx", ".json")
        
        # Skip if already processed
        if output_path.exists():
            print(f"[SKIP] {fname} already has output at {output_path}")
            continue
            
        print(f"\n{'='*60}")
        print(f"Processing: {fname}")
        print(f"{'='*60}")
        
        t0 = time.time()
        try:
            results = runner.run_pipeline(
                input_path=input_path,
                output_path=output_path,
                ckpt_path=ckpt_path
            )
            elapsed = time.time() - t0
            print(f"[OK] {fname}: {results['total_rows']} rows in {elapsed:.1f}s")
            if ckpt_path.exists():
                ckpt_path.unlink()
        except Exception as e:
            print(f"[ERR] {fname}: {e}")


def compare_all():
    """Compare new outputs to old production outputs."""
    report_lines = [
        "# Prompt V2 vs Old Production — Side-by-Side Comparison\n",
        f"Generated: {time.strftime('%Y-%m-%d %H:%M')}\n",
    ]
    
    for fname in TEST_FILES:
        base = fname.replace(".xlsx", "_output.xlsx")
        old_path = OLD_OUTPUT_DIR / base
        new_path = NEW_OUTPUT_DIR / base
        
        if not old_path.exists():
            report_lines.append(f"\n## {fname}\n**OLD output not found**: {old_path}\n")
            continue
        if not new_path.exists():
            report_lines.append(f"\n## {fname}\n**NEW output not found**: {new_path}\n")
            continue
        
        old_df = pd.read_excel(old_path, header=1)
        new_df = pd.read_excel(new_path, header=1)
        
        # Find text column
        text_col = None
        for c in old_df.columns:
            if str(c).strip().lower() in ("nội dung", "noi dung", "nội dung vấn đề"):
                text_col = c
                break
        if text_col is None:
            text_col = old_df.columns[0]
        
        report_lines.append(f"\n## {fname}")
        report_lines.append(f"Old rows: {len(old_df)}, New rows: {len(new_df)}\n")
        
        n_rows = min(len(old_df), len(new_df))
        
        # Count mismatches per label
        label_stats = {label: {"match": 0, "mismatch": 0} for label in MINOR_ORDER}
        sentiment_match = 0
        sentiment_mismatch = 0
        total_label_mismatches = 0
        
        detail_rows = []
        
        for i in range(n_rows):
            text = str(old_df.iloc[i].get(text_col, ""))[:80]
            
            old_labels = []
            new_labels = []
            row_mismatches = []
            
            for label in MINOR_ORDER:
                old_val = str(old_df.iloc[i].get(label, "")).strip()
                new_val = str(new_df.iloc[i].get(label, "")).strip()
                
                old_active = old_val not in ("", "nan", "None")
                new_active = new_val not in ("", "nan", "None")
                
                if old_active:
                    old_labels.append(label)
                if new_active:
                    new_labels.append(label)
                    
                if old_active == new_active:
                    label_stats[label]["match"] += 1
                else:
                    label_stats[label]["mismatch"] += 1
                    row_mismatches.append(f"{'+'if new_active else '-'}{label}")
            
            old_sent = str(old_df.iloc[i].get("Sentiment", "")).strip()
            new_sent = str(new_df.iloc[i].get("Sentiment", "")).strip()
            if old_sent in ("nan", "None"):
                old_sent = ""
            if new_sent in ("nan", "None"):
                new_sent = ""
            
            if old_sent == new_sent:
                sentiment_match += 1
            else:
                sentiment_mismatch += 1
            
            if row_mismatches or old_sent != new_sent:
                total_label_mismatches += 1
                detail_rows.append({
                    "row": i + 1,
                    "text": text,
                    "old_labels": ", ".join(old_labels) or "Tin trung lập",
                    "new_labels": ", ".join(new_labels) or "Tin trung lập",
                    "old_sent": old_sent or "—",
                    "new_sent": new_sent or "—",
                    "diff": "; ".join(row_mismatches),
                })
        
        # Summary table
        report_lines.append(f"### Summary")
        report_lines.append(f"- **Total rows**: {n_rows}")
        report_lines.append(f"- **Rows with label differences**: {total_label_mismatches} ({total_label_mismatches/n_rows*100:.0f}%)")
        report_lines.append(f"- **Sentiment match**: {sentiment_match}/{n_rows} ({sentiment_match/n_rows*100:.0f}%)\n")
        
        # Per-label stats
        report_lines.append("### Per-Label Agreement")
        report_lines.append("| Label | Match | Mismatch | Agreement |")
        report_lines.append("|---|---|---|---|")
        for label in MINOR_ORDER:
            m = label_stats[label]["match"]
            mm = label_stats[label]["mismatch"]
            pct = m / n_rows * 100 if n_rows > 0 else 0
            flag = " ⚠️" if mm > 3 else ""
            report_lines.append(f"| {label} | {m} | {mm} | {pct:.0f}%{flag} |")
        
        # Detail table (only show diffs)
        if detail_rows:
            report_lines.append(f"\n### Detailed Differences ({len(detail_rows)} rows)")
            report_lines.append("| # | Text | Old Labels | New Labels | Old Sent | New Sent | Diff |")
            report_lines.append("|---|---|---|---|---|---|---|")
            for d in detail_rows[:40]:  # Cap at 40 rows
                report_lines.append(
                    f"| {d['row']} | {d['text'][:60]} | {d['old_labels']} | {d['new_labels']} | {d['old_sent']} | {d['new_sent']} | {d['diff']} |"
                )
        
    report_path = script_dir / "prompt_v2_comparison.md"
    report_path.write_text("\n".join(report_lines), encoding="utf-8")
    print(f"\n{'='*60}")
    print(f"Comparison report saved to: {report_path}")
    print(f"{'='*60}")


if __name__ == "__main__":
    if "--compare-only" in sys.argv:
        compare_all()
    else:
        run_all()
        compare_all()
