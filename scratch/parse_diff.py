# -*- coding: utf-8 -*-
import re
import json


def parse_diff(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Split by the separator
    blocks = content.split("--------------------------------------------------")

    parsed_rows = []

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        # We can extract Row, Text, Old, V3
        row_match = re.search(r"^Row:\s*(\d+)", block, re.MULTILINE)
        if not row_match:
            continue
        row_id = int(row_match.group(1))

        # Find Old and V3 lines
        old_match = re.search(
            r"^\s*Old:\s*(.+?)\s*\|\s*Sentiment:\s*(.+)$", block, re.MULTILINE
        )
        v3_match = re.search(
            r"^\s*V3:\s*(.+?)\s*\|\s*Sentiment:\s*(.+)$", block, re.MULTILINE
        )

        if not old_match or not v3_match:
            continue

        old_labels = eval(old_match.group(1))
        old_sentiment = old_match.group(2).strip()

        v3_labels = eval(v3_match.group(1))
        v3_sentiment = v3_match.group(2).strip()

        # Extract Text: everything between "Text:" and "  Old:"
        text_start = block.find("Text:") + 5
        text_end = block.find("  Old:")
        text = block[text_start:text_end].strip()

        parsed_rows.append(
            {
                "row": row_id,
                "text": text,
                "old_labels": old_labels,
                "old_sentiment": old_sentiment,
                "v3_labels": v3_labels,
                "v3_sentiment": v3_sentiment,
            }
        )

    return parsed_rows


if __name__ == "__main__":
    rows = parse_diff("D:\\Works\\DMS\\scratch\\diff_DMS-1510-1710.txt")
    print(f"Total parsed rows: {len(rows)}")
    with open("D:\\Works\\DMS\\scratch\\parsed_rows.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
