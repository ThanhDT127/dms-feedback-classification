import sys
import os
import pandas as pd
import numpy as np
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

# Lists to hold all data rows
all_old_rows = []
all_new_rows = []

for fname in TEST_FILES:
    base = fname.replace(".xlsx", "_output.xlsx")
    op = old_dir / base
    np_file = new_dir / base
    
    if not op.exists() or not np_file.exists():
        continue
        
    df_old = pd.read_excel(op, header=None, skiprows=2)
    df_new = pd.read_excel(np_file, header=None, skiprows=2)
    
    df_old.columns = all_cols[:df_old.shape[1]]
    df_new.columns = all_cols[:df_new.shape[1]]
    
    all_old_rows.append(df_old)
    all_new_rows.append(df_new)

# Concatenate all files data
df_all_old = pd.concat(all_old_rows, ignore_index=True)
df_all_new = pd.concat(all_new_rows, ignore_index=True)

n_total = min(len(df_all_old), len(df_all_new))
df_all_old = df_all_old.iloc[:n_total]
df_all_new = df_all_new.iloc[:n_total]

print(f"Total merged rows for evaluation: {n_total}")

# Label-wise statistics
label_results = {}

for label in MINOR_ORDER:
    # Convert active/inactive to binary 1/0
    # Active is any non-empty string except nan/None
    y_old = df_all_old[label].fillna("").astype(str).str.strip().apply(lambda x: 1 if x not in ("", "nan", "None") else 0).values
    y_new = df_all_new[label].fillna("").astype(str).str.strip().apply(lambda x: 1 if x not in ("", "nan", "None") else 0).values
    
    # We treat V3 (y_new) as the Ground Truth for calculating metrics of y_old
    tp = int(np.sum((y_old == 1) & (y_new == 1)))
    tn = int(np.sum((y_old == 0) & (y_new == 0)))
    fp = int(np.sum((y_old == 1) & (y_new == 0)))
    fn = int(np.sum((y_old == 0) & (y_new == 1)))
    
    accuracy = (tp + tn) / n_total if n_total > 0 else 0
    
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
    
    # Support (number of active samples in Ground Truth V3)
    support = int(np.sum(y_new == 1))
    
    label_results[label] = {
        "Accuracy": accuracy,
        "Precision": precision,
        "Recall": recall,
        "F1": f1,
        "Support": support,
        "TP": tp, "TN": tn, "FP": fp, "FN": fn
    }

# Calculate Macro-averages (excluding labels with 0 support in GT to avoid divide-by-zero or meaningless metrics)
valid_labels = [lbl for lbl, res in label_results.items() if res["Support"] > 0 or (res["TP"] + res["FP"] > 0)]
n_valid = len(valid_labels)

macro_accuracy = np.mean([label_results[lbl]["Accuracy"] for lbl in MINOR_ORDER])
macro_precision = np.mean([label_results[lbl]["Precision"] for lbl in valid_labels])
macro_recall = np.mean([label_results[lbl]["Recall"] for lbl in valid_labels])
macro_f1 = np.mean([label_results[lbl]["F1"] for lbl in valid_labels])

# Generate detailed markdown report to save
report_lines = []
report_lines.append("# BÁO CÁO ĐÁNH GIÁ ĐA NHÃN CHI TIẾT (LABEL-WISE EVALUATION)")
report_lines.append(f"Quy mô dữ liệu gộp: {n_total} dòng phản hồi từ 5 file.")
report_lines.append("\n## I. Chỉ số Trung bình Không thiên lệch (Unbiased Macro-averages)")
report_lines.append("> [!IMPORTANT]")
report_lines.append("> Điểm **Macro-average** tính toán bằng cách lấy trung bình cộng độc lập của từng nhãn, giúp loại bỏ hoàn toàn bias (thiên lệch) của các nhãn chiếm đa số và đánh giá chính xác chất lượng phân loại trên các nhãn hiếm/mất cân bằng dữ liệu.")
report_lines.append(f"- **Macro-average Accuracy (Độ chính xác trung bình từng nhãn của Bản cũ):** {macro_accuracy*100:.2f}%")
report_lines.append(f"- **Macro-average F1-Score (Điểm F1 trung bình của Bản cũ):** {macro_f1*100:.2f}%")
report_lines.append(f"- **Macro-average Precision (Độ xác thực trung bình của Bản cũ):** {macro_precision*100:.2f}%")
report_lines.append(f"- **Macro-average Recall (Độ phủ trung bình của Bản cũ):** {macro_recall*100:.2f}%")

report_lines.append("\n## II. Bảng chỉ số chi tiết cho từng cột nhãn (Label-wise Metrics)")
report_lines.append("Bảng dưới đây sắp xếp các nhãn theo thứ tự xuất hiện, thể hiện rõ nhãn nào bản cũ phân loại tốt và nhãn nào bị sai lệch nhiều:")
report_lines.append("| Nhãn | Độ chính xác (Accuracy) | Precision | Recall (Độ phủ) | F1-Score | Số mẫu thực tế (Support) | Đánh giá chất lượng của Bản cũ |")
report_lines.append("|---|---|---|---|---|---|---|")

for label in MINOR_ORDER:
    res = label_results[label]
    acc = res["Accuracy"] * 100
    prec = res["Precision"] * 100
    rec = res["Recall"] * 100
    f1 = res["F1"] * 100
    supp = res["Support"]
    
    # Assess quality
    if supp == 0 and res["FP"] == 0:
        quality = "Hoàn hảo (Không có mẫu)"
    elif f1 >= 90:
        quality = "Rất tốt ✅"
    elif f1 >= 70:
        quality = "Khá ⚠️"
    elif f1 > 0:
        quality = "Kém (Sai lệch nhiều) ❌"
    else:
        quality = "Sai hoàn toàn / Bỏ sót ❌❌" if supp > 0 else "Hoàn hảo (Không có mẫu)"
        
    report_lines.append(f"| {label} | {acc:.1f}% | {prec:.1f}% | {rec:.1f}% | {f1:.1f}% | {supp} | {quality} |")

report_text = "\n".join(report_lines)
report_file = script_dir / "scratch" / "macro_evaluation_results.md"
report_file.write_text(report_text, encoding="utf-8")

print(f"Evaluation complete. Saved to: {report_file}")
print(f"Macro-Accuracy: {macro_accuracy*100:.2f}%")
print(f"Macro-F1: {macro_f1*100:.2f}%")
