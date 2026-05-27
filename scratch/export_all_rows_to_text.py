import sys
import os
import pandas as pd
from pathlib import Path

script_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(script_dir / "service" / "src"))

from dms.pipeline.issue_classifier import MINOR_ORDER

TEST_FILES = [
    "DMS-13102025.xlsx",
    "DMS-14102025.xlsx",
    "DMS-1510-1710.xlsx",
    "DMS-1810-1910.xlsx",
    "DMS-2010-2210.xlsx",
]

old_dir = script_dir / "Output"
new_dir = script_dir / "test_output"

# Base columns in the output files
base_cols = [
    "STT", "Tên đơn vị", "Mã vấn đề", "Mã nhân viên", "Tên nhân viên", 
    "Ngày", "Mã đại lý", "Đại lý", "Địa chỉ", "Tỉnh/TP", "Quận/huyện", 
    "Sản phẩm", "Dòng SP", "Model", "Lớp", "Điểm", "Nội dung phản hồi", 
    "Loại vấn đề", "Trạng thái"
]
extra_cols = ["Sentiment", "LLM_Extracted", "BM25_Score"]
all_cols = base_cols + MINOR_ORDER + extra_cols

for fname in TEST_FILES:
    base = fname.replace(".xlsx", "_output.xlsx")
    op = old_dir / base
    np_file = new_dir / base
    
    if not op.exists() or not np_file.exists():
        print(f"Skipping {fname} - missing file")
        continue
        
    df_old = pd.read_excel(op, header=None, skiprows=2)
    df_new = pd.read_excel(np_file, header=None, skiprows=2)
    
    df_old.columns = all_cols[:df_old.shape[1]]
    df_new.columns = all_cols[:df_new.shape[1]]
    
    n_rows = min(len(df_old), len(df_new))
    all_rows = []
    
    for i in range(n_rows):
        text = str(df_old.iloc[i].get("Nội dung phản hồi", "")).strip()
        
        old_active = []
        new_active = []
        
        for label in MINOR_ORDER:
            old_val = str(df_old.iloc[i].get(label, "")).strip()
            new_val = str(df_new.iloc[i].get(label, "")).strip()
            
            if old_val not in ("", "nan", "None"):
                old_active.append(label)
            if new_val not in ("", "nan", "None"):
                new_active.append(label)
                
        old_sent = str(df_old.iloc[i].get("Sentiment", "")).strip()
        new_sent = str(df_new.iloc[i].get("Sentiment", "")).strip()
        if old_sent in ("nan", "None"): old_sent = ""
        if new_sent in ("nan", "None"): new_sent = ""
        
        all_rows.append({
            "row_index": i + 3,
            "text": text,
            "old_labels": old_active or ["Tin trung lập"],
            "new_labels": new_active or ["Tin trung lập"],
            "old_sentiment": old_sent or "—",
            "new_sentiment": new_sent or "—"
        })
            
    # Write to text file
    output_lines = []
    output_lines.append(f"File: {fname}")
    output_lines.append(f"Total Rows: {n_rows}\n")
    for idx, d in enumerate(all_rows):
        output_lines.append(f"Row: {d['row_index']}")
        output_lines.append(f"Text: {d['text']}")
        output_lines.append(f"  Old: {d['old_labels']} | Sentiment: {d['old_sentiment']}")
        output_lines.append(f"  V3:  {d['new_labels']} | Sentiment: {d['new_sentiment']}")
        output_lines.append("-" * 50)
        
    out_file = script_dir / "scratch" / f"all_{fname.replace('.xlsx', '.txt')}"
    out_file.write_text("\n".join(output_lines), encoding="utf-8")
    print(f"Exported ALL {n_rows} rows of {fname} to {out_file}")
