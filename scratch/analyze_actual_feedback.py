import sys
import pandas as pd
from pathlib import Path

# Reconfigure stdout to use UTF-8 to prevent 'charmap' encoding issues in Windows console/logs
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")


def analyze_data():
    cache_dir = Path(r"d:\Works\DMS\scratch\sharepoint_cache")
    input_dir = cache_dir / "Input"
    output_dir = cache_dir / "Output"

    # Read some files from Input and Output to see what's inside
    input_files = list(input_dir.glob("*.xlsx"))
    output_files = list(output_dir.glob("*.xlsx"))

    print(f"Total Input Excel files found: {len(input_files)}")
    print(f"Total Output Excel files found: {len(output_files)}")

    feedback_corpus = []

    label_cols = [
        "Báo lỗi",
        "Báo CL tốt",
        "Y/c cải tiến",
        "Đề xuất SPM",
        "Bảng giá, Catalogue",
        "Bảng biển",
        "Kệ bóng, thử đèn,…",
        "Khác",
        "Tốt/ ko tốt",
        "Trả thưởng",
        "Đề xuất",
        "Bảo hành",
        "HTPP",
        "Hàng hoá",
        "Hàng giả",
        "Website",
        "Hãng",
        "Hoạt động",
        "CTKM, giá, cơ chế",
        "TT SP",
        "Tin trung lập",
    ]

    # We will determine the columns exactly like the runner does
    # First, let's read one file normally to get the base columns
    sample_file = output_files[0]
    sample_raw = pd.read_excel(sample_file, header=None, dtype=str)

    # Detect base columns using runner's function or equivalent
    # Just read with pandas normally, find 'Nội dung vấn đề' to see where base columns end
    df_header_check = pd.read_excel(sample_file)
    base_cols_count = 0
    for idx, col in enumerate(df_header_check.columns):
        if "nội dung vấn đề" in str(col).lower():
            base_cols_count = idx + 1
            break
    if base_cols_count == 0:
        base_cols_count = 17  # default fallback based on typical structure

    # We insert 5 product matching columns: Sản phẩm, Dòng SP, Model, Lớp, Điểm
    # The runner inserts them if not present. Let's see the structure from row data.

    for f in output_files:
        try:
            # Skip the first 2 rows of formatted headers
            df = pd.read_excel(f, header=None, skiprows=2)

            # The columns are:
            # 1. Base columns (approx 20 columns)
            # 2. MINOR_ORDER columns (21 columns)
            # 3. extra_cols (Sentiment, LLM_Extracted, BM25_Score)

            # Let's map columns from the end of the dataframe
            # The last 3 are: BM25_Score, LLM_Extracted, Sentiment
            # The 21 columns before that are the MINOR_ORDER labels!
            num_cols = df.shape[1]
            col_names = [f"base_col_{i}" for i in range(num_cols)]

            # Map the MINOR_ORDER columns from the right side
            # extra_cols is 3 columns
            minor_start_idx = num_cols - 3 - len(label_cols)
            for idx, label in enumerate(label_cols):
                col_names[minor_start_idx + idx] = label

            col_names[num_cols - 3] = "Sentiment"
            col_names[num_cols - 2] = "LLM_Extracted"
            col_names[num_cols - 1] = "BM25_Score"

            df.columns = col_names

            # Find the text column. It's usually around base_col_16
            comment_col = None
            # Let's read a small sample of the file with headers to find the column index of 'Nội dung vấn đề'
            df_headers = pd.read_excel(f, nrows=5)
            text_idx = None
            for idx, col in enumerate(df_headers.columns):
                if "nội dung vấn đề" in str(col).lower() or (
                    "nội dung" in str(col).lower() and "mã" not in str(col).lower()
                ):
                    text_idx = idx
                    break

            if text_idx is not None and text_idx < len(df.columns):
                comment_col = df.columns[text_idx]
            else:
                # Fallback to scanning for typical text column
                for col in df.columns:
                    if col not in label_cols and col not in [
                        "Sentiment",
                        "LLM_Extracted",
                        "BM25_Score",
                    ]:
                        # check if most elements are long strings
                        sample_vals = df[col].dropna().astype(str).tolist()[:5]
                        if sample_vals and any(len(v) > 15 for v in sample_vals):
                            comment_col = col
                            break

            if not comment_col:
                continue

            brand_idx = None
            for idx, col in enumerate(df_headers.columns):
                if (
                    "thương hiệu" in str(col).lower()
                    or "brand" in str(col).lower()
                    or "hãng" in str(col).lower()
                ):
                    brand_idx = idx
                    break
            brand_col = (
                df.columns[brand_idx]
                if brand_idx is not None and brand_idx < len(df.columns)
                else None
            )

            for idx, row in df.iterrows():
                text = row[comment_col]
                if (
                    pd.isna(text)
                    or str(text).strip() == ""
                    or str(text).strip().lower() == "nội dung vấn đề"
                ):
                    continue

                # Extract active labels
                row_labels = []
                for label in label_cols:
                    val = row[label]
                    if pd.notna(val) and str(val).strip().lower() in (
                        "x",
                        "1",
                        "true",
                        "yes",
                        "có",
                    ):
                        row_labels.append(label)

                brand = row[brand_col] if brand_col else ""
                sentiment = row["Sentiment"]

                feedback_corpus.append(
                    {
                        "file": f.name,
                        "row": idx + 3,  # because we skipped 2 header rows
                        "text": str(text).strip(),
                        "labels": row_labels,
                        "brand": str(brand).strip() if pd.notna(brand) else "",
                        "sentiment": str(sentiment).strip()
                        if pd.notna(sentiment)
                        else "",
                    }
                )
        except Exception:
            # print(f"Error reading {f.name}: {e}")
            pass

    print(f"Parsed {len(feedback_corpus)} rows of feedback.")

    # Analyze common abbreviations and typos in the feedback text
    abbrev_counts = {
        "bh": 0,
        "km": 0,
        "sp": 0,
        "đl": 0,
        "npp": 0,
        "c1": 0,
        "c2": 0,
        "bgn": 0,
        "at": 0,
        "tin thưởng": 0,
        "đèn": 0,
    }

    for item in feedback_corpus:
        text_lower = item["text"].lower()
        for abbrev in abbrev_counts:
            if abbrev in text_lower:
                abbrev_counts[abbrev] += 1

    print("\n--- Abbreviation and Keyword Occurrences in Sampled Feedback ---")
    for abbrev, count in abbrev_counts.items():
        print(f"  '{abbrev}': {count} occurrences")

    # Group by label to find good few-shot examples
    label_examples = {}
    for item in feedback_corpus:
        for lbl in item["labels"]:
            if lbl not in label_examples:
                label_examples[lbl] = []
            if len(label_examples[lbl]) < 5:
                label_examples[lbl].append(item)

    print("\n--- Example Feedbacks per Label ---")
    for lbl, examples in label_examples.items():
        print(f"\nLabel: {lbl}")
        for ex in examples:
            print(f"  - Text: {ex['text']!r}")
            print(
                f"    Labels: {ex['labels']}, Sentiment: {ex['sentiment']!r}, Brand: {ex['brand']!r}"
            )


if __name__ == "__main__":
    analyze_data()
