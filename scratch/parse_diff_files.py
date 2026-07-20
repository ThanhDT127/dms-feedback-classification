import re


def parse_diff_file(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Split content by the separator line
    blocks = content.split("--------------------------------------------------")

    diff_records = []
    header_info = ""

    # Parse the first block for file header info
    first_block = blocks[0].strip()
    # Find the first occurrence of "Row: "
    first_row_idx = first_block.find("Row: ")
    if first_row_idx != -1:
        header_info = first_block[:first_row_idx].strip()
        first_block_data = first_block[first_row_idx:]
    else:
        first_block_data = first_block

    blocks_to_parse = [first_block_data] + blocks[1:]

    for block in blocks_to_parse:
        block = block.strip()
        if not block:
            continue

        # Regex to parse Row, Text, Old, V3
        row_match = re.search(r"^Row:\s*(\d+)", block, re.MULTILINE)
        if not row_match:
            continue

        row_num = int(row_match.group(1))

        # Extract Old and V3 info
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

        # Extract Text (which is between "Text: " and "Old: ")
        text_start = block.find("Text: ")
        if text_start != -1:
            text_end = block.find("Old: ")
            text = block[text_start + 6 : text_end].strip()
        else:
            text = ""

        # Parse labels lists
        try:
            old_labels = eval(old_labels_str)
        except Exception:
            old_labels = [old_labels_str]

        try:
            v3_labels = eval(v3_labels_str)
        except Exception:
            v3_labels = [v3_labels_str]

        diff_records.append(
            {
                "row": row_num,
                "text": text,
                "old_labels": old_active_labels(old_labels),
                "v3_labels": old_active_labels(v3_labels),
                "old_sentiment": old_sentiment,
                "v3_sentiment": v3_sentiment,
            }
        )

    return header_info, diff_records


def old_active_labels(label_list):
    # Filter out empty or duplicate labels
    return [l for l in label_list if l and l != "nan"]


# Let's run parser on both files
path_1 = r"D:\Works\DMS\scratch\diff_DMS-1810-1910.txt"
path_2 = r"D:\Works\DMS\scratch\diff_DMS-2010-2210.txt"

header1, records1 = parse_diff_file(path_1)
header2, records2 = parse_diff_file(path_2)

print(f"File 1: {header1}")
print(f"Total parsed records 1: {len(records1)}")
print(f"File 2: {header2}")
print(f"Total parsed records 2: {len(records2)}")


# Basic stats on sentiment difference
def analyze_sentiment_diff(records):
    diff_count = 0
    for r in records:
        if r["old_sentiment"] != r["v3_sentiment"]:
            diff_count += 1
    return diff_count


print(f"File 1 sentiment differences: {analyze_sentiment_diff(records1)}")
print(f"File 2 sentiment differences: {analyze_sentiment_diff(records2)}")
