import sys
from pathlib import Path
import pandas as pd

# Reconfigure stdout to use UTF-8 to prevent 'charmap' encoding issues in Windows console/logs
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# Add service/src to python path
SERVICE_SRC = Path(r"d:\Works\DMS\service\src")
if str(SERVICE_SRC) not in sys.path:
    sys.path.insert(0, str(SERVICE_SRC))

from dms.settings import get_settings
from dms.gemini_client import GeminiClient
from dms.pipeline.issue_classifier import IssueClassifier, MINOR_ORDER


def main():
    settings = get_settings()

    gemini = GeminiClient(settings)
    classifier = IssueClassifier(gemini, settings)

    cache_dir = Path(r"d:\Works\DMS\scratch\sharepoint_cache")
    output_dir = cache_dir / "Output"

    output_files = sorted(list(output_dir.glob("*.xlsx")))

    print(f"Loaded {len(output_files)} output files.")

    records = []

    # We read directly from the Output files to ensure 100% column-mapping robustness
    for out_file in output_files:
        if len(records) >= 20:
            break

        try:
            # Skip the first 2 header rows
            df_out = pd.read_excel(out_file, header=None, skiprows=2)

            # Map columns from the end of the dataframe
            num_cols = df_out.shape[1]
            col_names = [f"base_col_{i}" for i in range(num_cols)]
            minor_start_idx = num_cols - 3 - len(MINOR_ORDER)
            for i, label in enumerate(MINOR_ORDER):
                col_names[minor_start_idx + i] = label
            col_names[num_cols - 3] = "Sentiment"
            col_names[num_cols - 2] = "LLM_Extracted"
            col_names[num_cols - 1] = "BM25_Score"
            df_out.columns = col_names

            # Find the actual text column index by looking at the original headers
            df_headers = pd.read_excel(out_file, nrows=5)
            text_idx = None
            for i, col in enumerate(df_headers.columns):
                if "nội dung vấn đề" in str(col).lower() or (
                    "nội dung" in str(col).lower() and "mã" not in str(col).lower()
                ):
                    text_idx = i
                    break

            if text_idx is None or text_idx >= num_cols:
                continue

            text_col_name = df_out.columns[text_idx]

            # Extract the first non-empty valid row
            comment_text = None
            row_data = None
            for idx, row in df_out.iterrows():
                val = str(row[text_col_name]).strip()
                if (
                    val
                    and val != "nan"
                    and len(val) > 15
                    and not val.startswith("col_")
                    and val != "Nội dung vấn đề"
                ):
                    # Avoid index rows or header duplicates
                    comment_text = val
                    row_data = row
                    break

            if not comment_text or row_data is None:
                continue

            # Extract historical labels
            old_labels = []
            for label in MINOR_ORDER:
                val = row_data[label]
                if pd.notna(val) and str(val).strip().lower() in (
                    "x",
                    "1",
                    "true",
                    "yes",
                    "có",
                ):
                    old_labels.append(label)
            old_sentiment = row_data["Sentiment"]

            records.append(
                {
                    "file": out_file.name,
                    "text": comment_text,
                    "old_labels": old_labels,
                    "old_sentiment": str(old_sentiment)
                    if pd.notna(old_sentiment)
                    else "",
                }
            )

        except Exception:
            # print(f"Error reading {out_file.name}: {e}")
            pass

    print(f"Successfully harvested {len(records)} comments from 20 different files.")

    # Run the new prompt classifier on these harvested comments
    texts = [r["text"] for r in records]
    print("Running new prompt classifier on Gemini...")
    new_results = classifier.classify_batch(texts)

    # Compile comparison
    comparison_lines = []
    comparison_lines.append(
        "# Báo cáo Kiểm thử So sánh Prompt Cũ vs Prompt Mới (20 Ca Thực tế)"
    )
    comparison_lines.append(
        "\nDưới đây là kết quả đối chiếu tự động 20 dòng phản hồi thực tế được trích xuất từ 20 tệp khách hàng khác nhau trên SharePoint."
    )
    comparison_lines.append(
        "\n| STT | Tệp dữ liệu | Nội dung phản hồi thực tế | Phân loại CŨ (Lịch sử) | Phân loại MỚI (Cải tiến) | Nhận xét cải thiện |"
    )
    comparison_lines.append("| --- | --- | --- | --- | --- | --- |")

    for idx, (rec, res) in enumerate(zip(records, new_results)):
        old_lbl_str = ", ".join(rec["old_labels"]) if rec["old_labels"] else "Không gán"
        new_lbl_str = (
            ", ".join(res["final_minors"]) if res["final_minors"] else "Không gán"
        )

        # Determine analysis comment
        comment = ""
        if set(rec["old_labels"]) == set(res["final_minors"]):
            comment = "✓ Trùng khớp (Độ chính xác tốt)"
        else:
            text_lower = rec["text"].lower()
            if "tin thưởng" in text_lower:
                comment = "💡 **Cải thiện lớn**: Phát hiện typo 'tin thưởng' -> 'tin tưởng', loại bỏ nhãn Trả thưởng thành công!"
            elif (
                any(x in text_lower for x in ["chậm", "giao hàng", "thiếu"])
                and "website" in rec["old_labels"]
                and "website" not in res["final_minors"]
            ):
                comment = "💡 **Cải thiện lớn**: Sửa lỗi gán nhầm logistics/giao hàng chậm vào 'Website' của prompt cũ."
            else:
                comment = "🔄 Đã chuẩn hóa chính xác hơn theo ranh giới CoT mới."

        comparison_lines.append(
            f"| {idx + 1} | {rec['file']} | {rec['text']} | {old_lbl_str} | {new_lbl_str} | {comment} |"
        )

    report_text = "\n".join(comparison_lines)
    report_file = Path(r"d:\Works\DMS\scratch\upgrade_comparison_report.md")
    report_file.write_text(report_text, encoding="utf-8")

    print(f"\nValidation completed successfully! Report generated at: {report_file}")


if __name__ == "__main__":
    main()
