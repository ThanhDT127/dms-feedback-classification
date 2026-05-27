import sys
import os
import pandas as pd
from pathlib import Path

script_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(script_dir / "service" / "src"))

from dms.pipeline.issue_classifier import MINOR_ORDER

old_path = script_dir / "Output" / "DMS-13102025_output.xlsx"
new_path = script_dir / "test_output" / "DMS-13102025_output.xlsx"

if not old_path.exists() or not new_path.exists():
    print("Missing files for comparison.")
    sys.exit(1)

# Read excel files without header, skip the first 2 title rows
old_df = pd.read_excel(old_path, header=None, skiprows=2)
new_df = pd.read_excel(new_path, header=None, skiprows=2)

# Build correct column list: 19 base columns + 21 minor labels + 3 extra columns
base_cols = [
    "STT", "Tên đơn vị", "Mã vấn đề", "Mã nhân viên", "Tên nhân viên", 
    "Ngày", "Mã đại lý", "Đại lý", "Địa chỉ", "Tỉnh/TP", "Quận/huyện", 
    "Sản phẩm", "Dòng SP", "Model", "Lớp", "Điểm", "Nội dung phản hồi", 
    "Loại vấn đề", "Trạng thái"
]
extra_cols = ["Sentiment", "LLM_Extracted", "BM25_Score"]
all_cols = base_cols + MINOR_ORDER + extra_cols

old_df.columns = all_cols[:old_df.shape[1]]
new_df.columns = all_cols[:new_df.shape[1]]

text_col = "Nội dung phản hồi"

n_rows = min(len(old_df), len(new_df))
diff_rows = []

for i in range(n_rows):
    text = str(old_df.iloc[i].get(text_col, "")).strip()
    
    old_active = []
    new_active = []
    
    for label in MINOR_ORDER:
        old_val = str(old_df.iloc[i].get(label, "")).strip()
        new_val = str(new_df.iloc[i].get(label, "")).strip()
        
        # Check active ('x' or 'X' or any non-empty string except nan/None)
        if old_val not in ("", "nan", "None"):
            old_active.append(label)
        if new_val not in ("", "nan", "None"):
            new_active.append(label)
            
    old_sent = str(old_df.iloc[i].get("Sentiment", "")).strip()
    new_sent = str(new_df.iloc[i].get("Sentiment", "")).strip()
    if old_sent in ("nan", "None"): old_sent = ""
    if new_sent in ("nan", "None"): new_sent = ""
    
    # If there is any difference in labels or sentiment
    if set(old_active) != set(new_active) or old_sent != new_sent:
        diff_rows.append({
            "row_index": i + 3,  # Excel 1-based, header is 2 rows
            "text": text,
            "old_labels": old_active,
            "new_labels": new_active,
            "old_sentiment": old_sent or "—",
            "new_sentiment": new_sent or "—"
        })

output_lines = []
output_lines.append(f"Total differences found in DMS-13102025: {len(diff_rows)} / {n_rows} rows.")
output_lines.append("\n--- DETAILED DIFFERENCES ---")
for idx, d in enumerate(diff_rows):
    output_lines.append(f"\n[{idx+1}] Dòng Excel: {d['row_index']}")
    output_lines.append(f"Nội dung: \"{d['text']}\"")
    output_lines.append(f"  - OLD: Labels={d['old_labels'] or ['Tin trung lập']} | Sentiment={d['old_sentiment']}")
    output_lines.append(f"  - NEW (V3): Labels={d['new_labels'] or ['Tin trung lập']} | Sentiment={d['new_sentiment']}")

output_text = "\n".join(output_lines)
output_file = script_dir / "scratch" / "diff_results_1.txt"
output_file.write_text(output_text, encoding="utf-8")
print(f"Analysis complete. Saved to: {output_file}")
print(f"Total differences: {len(diff_rows)}")
