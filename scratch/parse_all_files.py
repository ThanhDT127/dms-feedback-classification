import re


def parse_all_file(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    blocks = content.split("--------------------------------------------------")
    records = []

    first_block = blocks[0].strip()
    row_idx = first_block.find("Row: ")
    if row_idx != -1:
        first_block_data = first_block[row_idx:]
    else:
        first_block_data = first_block

    blocks_to_parse = [first_block_data] + blocks[1:]

    for block in blocks_to_parse:
        block = block.strip()
        if not block:
            continue

        row_match = re.search(r"^Row:\s*(\d+)", block, re.MULTILINE)
        if not row_match:
            continue

        row_num = int(row_match.group(1))

        old_match = re.search(
            r"^\s*Old:\s*(.*)\s*\|\s*Sentiment:\s*(.*)$", block, re.MULTILINE
        )
        v3_match = re.search(
            r"^\s*V3:\s*(.*)\s*\|\s*Sentiment:\s*(.*)$", block, re.MULTILINE
        )

        if not old_match or not v3_match:
            continue

        old_labels_str = old_match.group(1).strip()
        old_sentiment = old_match.group(2).strip()

        v3_labels_str = v3_match.group(1).strip()
        v3_sentiment = v3_match.group(2).strip()

        text_start = block.find("Text: ")
        if text_start != -1:
            text_end = block.find("Old: ")
            text = block[text_start + 6 : text_end].strip()
        else:
            text = ""

        try:
            old_labels = eval(old_labels_str)
        except Exception:
            old_labels = [old_labels_str]

        try:
            v3_labels = eval(v3_labels_str)
        except Exception:
            v3_labels = [v3_labels_str]

        records.append(
            {
                "row": row_num,
                "text": text,
                "old_labels": [l for l in old_labels if l and l != "nan"],
                "v3_labels": [l for l in v3_labels if l and l != "nan"],
                "old_sentiment": old_sentiment,
                "v3_sentiment": v3_sentiment,
            }
        )

    return records


rec1 = parse_all_file(r"D:\Works\DMS\scratch\all_DMS-1510-1710.txt")
rec2 = parse_all_file(r"D:\Works\DMS\scratch\all_DMS-1810-1910.txt")

print(f"File 1 (1510-1710): parsed {len(rec1)} rows")
print(f"File 2 (1810-1910): parsed {len(rec2)} rows")


# Check identical rows
def analyze_identical(records):
    identical = 0
    diff_label = 0
    diff_sentiment = 0
    diff_both = 0
    for r in records:
        lbl_match = set(r["old_labels"]) == set(r["v3_labels"])
        sent_match = r["old_sentiment"] == r["v3_sentiment"]
        if lbl_match and sent_match:
            identical += 1
        elif not lbl_match and not sent_match:
            diff_both += 1
        elif not lbl_match:
            diff_label += 1
        else:
            diff_sentiment += 1
    return identical, diff_label, diff_sentiment, diff_both


id1, dl1, ds1, db1 = analyze_identical(rec1)
id2, dl2, ds2, db2 = analyze_identical(rec2)

print(
    f"File 1: Identical={id1}, Diff Label={dl1}, Diff Sentiment={ds1}, Diff Both={db1}"
)
print(
    f"File 2: Identical={id2}, Diff Label={dl2}, Diff Sentiment={ds2}, Diff Both={db2}"
)
