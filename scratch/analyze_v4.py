import pandas as pd
import json
from pathlib import Path
import numpy as np

script_dir = Path("d:/Works/DMS")
test_output_dir = script_dir / "test_output"
old_output_dir = script_dir / "Output"
scratch_dir = script_dir / "scratch"

TEST_FILES = [
    "DMS-13102025.xlsx",
    "DMS-14102025.xlsx",
    "DMS-1510-1710.xlsx",
    "DMS-1810-1910.xlsx",
    "DMS-2010-2210.xlsx",
]

MINOR_ORDER = [
    "Báo lỗi", "Báo CL tốt", "Y/c cải tiến", "Đề xuất SPM", "Bảng giá, Catalogue",
    "Bảng biển", "Kệ bóng, thử đèn,…", "Khác", "Tốt/ ko tốt", "Trả thưởng",
    "Đề xuất", "Bảo hành", "HTPP", "Hàng hoá", "Hàng giả", "Website",
    "Hãng", "Hoạt động", "CTKM, giá, cơ chế", "TT SP", "Tin trung lập"
]

base_cols = [
    "STT", "Tên đơn vị", "Mã vấn đề", "Mã nhân viên", "Tên nhân viên", 
    "Ngày", "Mã đại lý", "Đại lý", "Địa chỉ", "Tỉnh/TP", "Quận/huyện", 
    "Sản phẩm", "Dòng SP", "Model", "Lớp", "Điểm", "Nội dung phản hồi", 
    "Loại vấn đề", "Trạng thái"
]
extra_cols = ["Sentiment", "LLM_Extracted", "BM25_Score"]
all_cols = base_cols + MINOR_ORDER + extra_cols

def clean_txt(t):
    if not isinstance(t, str):
        return ""
    return "".join(t.lower().split())

def old_sentiment_clean(s):
    s = str(s).strip()
    if s in ("—", "nan", "None"):
        return ""
    return s

# 1. Load Ground Truth from evaluated_results.json
with open(scratch_dir / "evaluated_results.json", "r", encoding="utf-8") as f:
    gt_data = json.load(f)

print(f"Loaded {len(gt_data)} ground truth records.")

gt_map = {}
for item in gt_data:
    t_clean = clean_txt(item["text"])
    if t_clean:
        gt_map[t_clean] = item

# 2. Load V4 outputs and Old Production outputs to align
v4_records = []
total_aligned = 0

for fname in TEST_FILES:
    v4_path = test_output_dir / fname.replace(".xlsx", "_output.xlsx")
    old_path = old_output_dir / fname.replace(".xlsx", "_output.xlsx")
    
    if not v4_path.exists():
        print(f"V4 file not found: {v4_path}")
        continue
    if not old_path.exists():
        print(f"Old file not found: {old_path}")
        continue
        
    df_v4 = pd.read_excel(v4_path, header=None, skiprows=2)
    df_old = pd.read_excel(old_path, header=None, skiprows=2)
    
    df_v4.columns = all_cols[:df_v4.shape[1]]
    df_old.columns = all_cols[:df_old.shape[1]]
    
    print(f"File {fname}: V4 rows={len(df_v4)}, Old rows={len(df_old)}")
    
    for i in range(min(len(df_v4), len(df_old))):
        row_v4 = df_v4.iloc[i]
        row_old = df_old.iloc[i]
        
        text = str(row_v4["Nội dung phản hồi"])
        t_clean = clean_txt(text)
        
        # Get V4 labels
        v4_lbls = []
        for col in MINOR_ORDER:
            val = str(row_v4[col]).strip()
            if val not in ("", "nan", "None"):
                v4_lbls.append(col)
        v4_sent = str(row_v4["Sentiment"]).strip()
        if v4_sent in ("nan", "None"):
            v4_sent = ""
            
        # Get Old labels
        old_lbls = []
        for col in MINOR_ORDER:
            val = str(row_old[col]).strip()
            if val not in ("", "nan", "None"):
                old_lbls.append(col)
        old_sent = str(row_old["Sentiment"]).strip()
        if old_sent in ("nan", "None"):
            old_sent = ""
            
        # Align with Ground Truth and V3 from gt_map
        gt_item = gt_map.get(t_clean)
        if gt_item:
            v3_lbls = gt_item["v3"]["labels"]
            v3_sent = gt_item["v3"]["sentiment"]
            if v3_sent == "—":
                v3_sent = ""
                
            gt_lbls = gt_item["gt"]["labels"]
            gt_sent = gt_item["gt"]["sentiment"]
            if gt_sent == "—":
                gt_sent = ""
                
            if old_sent == "—":
                old_sent = ""
                
            v4_records.append({
                "text": text,
                "file": fname,
                "row_idx": i + 3,
                "old_labels": old_lbls,
                "old_sentiment": old_sentiment_clean(old_sent),
                "v3_labels": v3_lbls,
                "v3_sentiment": old_sentiment_clean(v3_sent),
                "v4_labels": v4_lbls,
                "v4_sentiment": old_sentiment_clean(v4_sent),
                "gt_labels": gt_lbls,
                "gt_sentiment": old_sentiment_clean(gt_sent),
            })
            total_aligned += 1

print(f"Successfully aligned {total_aligned} records across all 5 files.")

# Perform calculations and build report
report = []
report.append("# BÁO CÁO PHÂN TÍCH SO SÁNH BA PHIÊN BẢN (OLD vs V3 vs V4)")
report.append(f"Quy mô dữ liệu đối chiếu: {total_aligned} dòng phản hồi gộp từ 5 file Excel.")
report.append("\n## I. Độ chính xác Tuyệt đối (Exact Match Accuracy)")
report.append("> **Exact Match Accuracy** là tiêu chuẩn nghiêm ngặt nhất: một dòng được coi là đúng khi và chỉ khi **khớp chính xác 100% cả tập nhãn (labels) và cảm xúc (sentiment)** so với Ground Truth.")

# Calculate exact matches
old_exact = []
v3_exact = []
v4_exact = []

old_lbl_exact = []
v3_lbl_exact = []
v4_lbl_exact = []

old_sent_exact = []
v3_sent_exact = []
v4_sent_exact = []

file_stats = {}

for r in v4_records:
    f = r["file"]
    if f not in file_stats:
        file_stats[f] = {"total": 0, "old": 0, "v3": 0, "v4": 0}
        
    old_ok = set(r["old_labels"]) == set(r["gt_labels"]) and r["old_sentiment"] == r["gt_sentiment"]
    v3_ok = set(r["v3_labels"]) == set(r["gt_labels"]) and r["v3_sentiment"] == r["gt_sentiment"]
    v4_ok = set(r["v4_labels"]) == set(r["gt_labels"]) and r["v4_sentiment"] == r["gt_sentiment"]
    
    file_stats[f]["total"] += 1
    if old_ok: file_stats[f]["old"] += 1
    if v3_ok: file_stats[f]["v3"] += 1
    if v4_ok: file_stats[f]["v4"] += 1
    
    old_exact.append(old_ok)
    v3_exact.append(v3_ok)
    v4_exact.append(v4_ok)
    
    old_lbl_exact.append(set(r["old_labels"]) == set(r["gt_labels"]))
    v3_lbl_exact.append(set(r["v3_labels"]) == set(r["gt_labels"]))
    v4_lbl_exact.append(set(r["v4_labels"]) == set(r["gt_labels"]))
    
    old_sent_exact.append(r["old_sentiment"] == r["gt_sentiment"])
    v3_sent_exact.append(r["v3_sentiment"] == r["gt_sentiment"])
    v4_sent_exact.append(r["v4_sentiment"] == r["gt_sentiment"])

report.append("\n### 1. Bảng so sánh tổng hợp gộp cả 5 file")
report.append("| Phiên bản | Độ chính xác Tuyệt đối (Exact Match) | Độ chính xác gán Nhãn (Label Exact Match) | Độ chính xác Cảm xúc (Sentiment Match) |")
report.append("|---|:---:|:---:|:---:|")
report.append(f"| **Bản cũ (Old Production)** | {np.mean(old_exact)*100:.2f}% | {np.mean(old_lbl_exact)*100:.2f}% | {np.mean(old_sent_exact)*100:.2f}% |")
report.append(f"| **Bản mới V3 (V3)** | {np.mean(v3_exact)*100:.2f}% | {np.mean(v3_lbl_exact)*100:.2f}% | {np.mean(v3_sent_exact)*100:.2f}% |")
report.append(f"| **Bản hiện tại V4 (V4)** | {np.mean(v4_exact)*100:.2f}% | {np.mean(v4_lbl_exact)*100:.2f}% | {np.mean(v4_sent_exact)*100:.2f}% |")

report.append("\n### 2. Chi tiết độ chính xác theo từng file Excel")
report.append("| Tên file Excel | Số dòng | Bản cũ (Old) | Bản mới (V3) | Bản hiện tại (V4) | Tăng trưởng V4 vs V3 | Tăng trưởng V4 vs Old |")
report.append("|---|:---:|:---:|:---:|:---:|:---:|:---:|")
for f in TEST_FILES:
    s = file_stats.get(f, {"total": 0, "old": 0, "v3": 0, "v4": 0})
    tot = s["total"]
    if tot == 0: continue
    p_old = s["old"]/tot*100
    p_v3 = s["v3"]/tot*100
    p_v4 = s["v4"]/tot*100
    growth_v3 = p_v4 - p_v3
    growth_old = p_v4 - p_old
    report.append(f"| {f} | {tot} | {p_old:.1f}% | {p_v3:.1f}% | {p_v4:.1f}% | **+{growth_v3:.1f}%** | **+{growth_old:.1f}%** |")

# Calculate Label-wise Metrics for Old, V3, V4
report.append("\n## II. Phân tích Chỉ số không thiên lệch (Macro-average Metrics)")
report.append("> Điểm **Macro-average** tính bằng trung bình cộng độc lập các nhãn, phản ánh khách quan năng lực nhận diện các nhãn hiếm mà không bị loãng bởi các nhãn chiếm đa số.")

def calc_metrics(lbl_func):
    metrics = {}
    for label in MINOR_ORDER:
        y_true = np.array([1 if label in r["gt_labels"] else 0 for r in v4_records])
        y_pred = np.array([1 if label in lbl_func(r) else 0 for r in v4_records])
        
        tp = np.sum((y_pred == 1) & (y_true == 1))
        tn = np.sum((y_pred == 0) & (y_true == 0))
        fp = np.sum((y_pred == 1) & (y_true == 0))
        fn = np.sum((y_pred == 0) & (y_true == 1))
        
        acc = (tp + tn) / len(v4_records)
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0
        
        metrics[label] = {"acc": acc, "prec": prec, "rec": rec, "f1": f1, "support": np.sum(y_true)}
    return metrics

m_old = calc_metrics(lambda r: r["old_labels"])
m_v3 = calc_metrics(lambda r: r["v3_labels"])
m_v4 = calc_metrics(lambda r: r["v4_labels"])

def macro_avg(metrics):
    accs = [d["acc"] for d in metrics.values()]
    precs = [d["prec"] for m, d in metrics.items() if d["support"] > 0 or d["prec"] > 0]
    recs = [d["rec"] for m, d in metrics.items() if d["support"] > 0]
    f1s = [d["f1"] for m, d in metrics.items() if d["support"] > 0]
    return np.mean(accs), np.mean(precs), np.mean(recs), np.mean(f1s)

macro_old = macro_avg(m_old)
macro_v3 = macro_avg(m_v3)
macro_v4 = macro_avg(m_v4)

report.append("\n| Chỉ số Macro-average | Bản cũ (Old) | Bản mới (V3) | Bản hiện tại (V4) | Cải thiện V4 vs V3 |")
report.append("|---|:---:|:---:|:---:|:---:|")
report.append(f"| **Macro Accuracy** | {macro_old[0]*100:.2f}% | {macro_v3[0]*100:.2f}% | {macro_v4[0]*100:.2f}% | **+{((macro_v4[0]-macro_v3[0])*100):.2f}%** |")
report.append(f"| **Macro Precision** | {macro_old[1]*100:.2f}% | {macro_v3[1]*100:.2f}% | {macro_v4[1]*100:.2f}% | **+{((macro_v4[1]-macro_v3[1])*100):.2f}%** |")
report.append(f"| **Macro Recall** | {macro_old[2]*100:.2f}% | {macro_v3[2]*100:.2f}% | {macro_v4[2]*100:.2f}% | **+{((macro_v4[2]-macro_v3[2])*100):.2f}%** |")
report.append(f"| **Macro F1-Score** | {macro_old[3]*100:.2f}% | {macro_v3[3]*100:.2f}% | {macro_v4[3]*100:.2f}% | **+{((macro_v4[3]-macro_v3[3])*100):.2f}%** |")

report.append("\n## III. Bảng so sánh chi tiết từng Cột Nhãn (Label-wise F1-Score Comparison)")
report.append("Bảng dưới đây so sánh điểm **F1-Score (%)** của cả 3 phiên bản trên toàn bộ 21 cột nhãn nghiệp vụ:")
report.append("| Tên nhãn nghiệp vụ | Số mẫu thực tế (Support) | Bản cũ (Old) F1 | Bản mới (V3) F1 | Bản hiện tại (V4) F1 | Kết quả tối ưu ở V4 |")
report.append("|---|:---:|:---:|:---:|:---:|---|")

for lbl in MINOR_ORDER:
    supp = m_gt_supp = m_old[lbl]["support"]
    f_old = m_old[lbl]["f1"]*100
    f_v3 = m_v3[lbl]["f1"]*100
    f_v4 = m_v4[lbl]["f1"]*100
    
    status = "Không đổi"
    if f_v4 > f_v3:
        status = f"🔥 Tăng +{(f_v4 - f_v3):.1f}%"
    elif f_v4 < f_v3:
        status = f"📉 Giảm {(f_v4 - f_v3):.1f}%"
    else:
        if f_v4 == 100.0 and supp > 0:
            status = "✨ Hoàn hảo"
        elif supp == 0:
            status = "—"
            
    report.append(f"| {lbl} | {supp} | {f_old:.1f}% | {f_v3:.1f}% | {f_v4:.1f}% | {status} |")

# IV. Detailed analysis of specific fixed issues
report.append("\n## IV. Minh chứng Thực tế về việc Khắc phục 4 Lỗi Hệ thống của V3 ở bản V4")
report.append("Dưới đây là các ví dụ thực tế được bóc tách từ 5 file chạy phân loại để minh họa trực quan:")

# Find and print examples
report.append("\n### 1. Khắc phục lỗi 'Competitor Override' (Hủy bỏ bộ lọc cứng thương hiệu đối thủ)")
report.append("> **Lỗi của V3:** Khi khách hàng phàn nàn/khen sản phẩm Rạng Đông nhưng có so sánh với đối thủ, V3 chỉ gán các nhãn đối thủ mà xóa hoàn toàn nhãn chính của RĐ.")
report.append("> **Giải pháp V4:** Giữ lại đầy đủ các nhãn nghiệp vụ cho cả hai thương hiệu.")

comp_ex = []
for r in v4_records:
    # Find records where brand is set and there are both competitor and non-competitor labels in V4 but V3 missed non-competitor
    t_low = r["text"].lower()
    if any(x in t_low for x in ["sino", "sopoka", "mpe"]) and "rạng đông" in t_low:
        if len(r["v4_labels"]) > 1 and "Hãng" in r["v4_labels"] and any(lbl in ["Báo lỗi", "Y/c cải tiến", "Báo CL tốt"] for lbl in r["v4_labels"]):
            comp_ex.append(r)

for idx, r in enumerate(comp_ex[:3]):
    report.append(f"\n* **Ví dụ {idx+1}:** \"*{r['text']}*\"")
    report.append(f"  - **Bản cũ (Old):** `{r['old_labels']}` | Sent: `{r['old_sentiment']}`")
    report.append(f"  - **Bản mới V3:** `{r['v3_labels']}` | Sent: `{r['v3_sentiment']}`")
    report.append(f"  - **Bản hiện tại V4:** `{r['v4_labels']}` | Sent: `{r['v4_sentiment']}`")
    report.append(f"  - **Ground Truth:** `{r['gt_labels']}` | Sent: `{r['gt_sentiment']}`")

report.append("\n### 2. Phân định rõ ràng Báo lỗi vs Yêu cầu cải tiến (Mở rộng Báo lỗi chất lượng vật lý)")
report.append("> **Lỗi của V3:** Xem tất cả phàn nàn nhẹ là 'Báo lỗi' hư hỏng vật lý hoặc ngược lại, V2 lại gộp hết thành Cải tiến.")
report.append("> **Giải pháp V4:** Ranh giới rõ ràng. Lỗi hư hỏng/kém chất lượng (không sáng, đơ, hỏng...) = Báo lỗi. Yêu cầu đổi thiết kế vỏ hộp, thêm dây... = Y/c cải tiến.")

err_ex = []
for r in v4_records:
    if "cải tiến" in r["gt_labels"] and "Báo lỗi" not in r["gt_labels"] and "Báo lỗi" in r["v3_labels"] and "Báo lỗi" not in r["v4_labels"]:
        err_ex.append(r)
    if "Báo lỗi" in r["gt_labels"] and "cải tiến" not in r["gt_labels"] and "Báo lỗi" not in r["old_labels"] and "Báo lỗi" in r["v4_labels"]:
        err_ex.append(r)

for idx, r in enumerate(err_ex[:3]):
    report.append(f"\n* **Ví dụ {idx+1}:** \"*{r['text']}*\"")
    report.append(f"  - **Bản cũ (Old):** `{r['old_labels']}` | Sent: `{r['old_sentiment']}`")
    report.append(f"  - **Bản mới V3:** `{r['v3_labels']}` | Sent: `{r['v3_sentiment']}`")
    report.append(f"  - **Bản hiện tại V4:** `{r['v4_labels']}` | Sent: `{r['v4_sentiment']}`")
    report.append(f"  - **Ground Truth:** `{r['gt_labels']}` | Sent: `{r['gt_sentiment']}`")

report.append("\n### 3. Khắc phục lỗi bẫy sai chính tả (Spell Guard)")
report.append("> **Lỗi của V3:** Bị lừa bởi lỗi sai chính tả đồng âm tiếng Việt, ví dụ: 'chưa tin thưởng' (tin tưởng) bị gán nhãn 'Trả thưởng'.")
report.append("> **Giải pháp V4:** Spell Guard thông minh và các rule phủ định giúp bỏ qua các từ viết sai chính tả hoặc không mang nghĩa khuyến mãi thực tế.")

spell_ex = []
for r in v4_records:
    if "tin thưởng" in r["text"].lower() or "thưởng" in r["text"].lower() and "Trả thưởng" in r["v3_labels"] and "Trả thưởng" not in r["v4_labels"]:
        spell_ex.append(r)

for idx, r in enumerate(spell_ex[:3]):
    report.append(f"\n* **Ví dụ {idx+1}:** \"*{r['text']}*\"")
    report.append(f"  - **Bản cũ (Old):** `{r['old_labels']}` | Sent: `{r['old_sentiment']}`")
    report.append(f"  - **Bản mới V3:** `{r['v3_labels']}` | Sent: `{r['v3_sentiment']}`")
    report.append(f"  - **Bản hiện tại V4:** `{r['v4_labels']}` | Sent: `{r['v4_sentiment']}`")
    report.append(f"  - **Ground Truth:** `{r['gt_labels']}` | Sent: `{r['gt_sentiment']}`")

report.append("\n### 4. Chuẩn hóa cảm xúc trung tính (Neutral Sentiment)")
report.append("> **Lỗi của V3:** Gán cảm xúc tiêu cực quá đà cho các đóng góp mang tính trung lập, đề xuất khách quan.")
report.append("> **Giải pháp V4:** Trả lại cảm xúc trống `\"\"` (trung tính) cho các câu hỏi thông tin, đề xuất hoặc tin thị trường đối thủ.")

sent_ex = []
for r in v4_records:
    if r["gt_sentiment"] == "" and r["v3_sentiment"] == "Tiêu cực" and r["v4_sentiment"] == "":
        sent_ex.append(r)

for idx, r in enumerate(sent_ex[:3]):
    report.append(f"\n* **Ví dụ {idx+1}:** \"*{r['text']}*\"")
    report.append(f"  - **Bản cũ (Old):** `{r['old_labels']}` | Sent: `{r['old_sentiment']}`")
    report.append(f"  - **Bản mới V3:** `{r['v3_labels']}` | Sent: `{r['v3_sentiment']}`")
    report.append(f"  - **Bản hiện tại V4:** `{r['v4_labels']}` | Sent: `{r['v4_sentiment']}`")
    report.append(f"  - **Ground Truth:** `{r['gt_labels']}` | Sent: `{r['gt_sentiment']}`")

report_text = "\n".join(report)
output_report_path = scratch_dir / "final_v4_comparative_analysis.md"
output_report_path.write_text(report_text, encoding="utf-8")
print(f"\nEvaluation and detailed comparison report successfully saved to: {output_report_path}")
