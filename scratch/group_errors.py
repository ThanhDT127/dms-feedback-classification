# -*- coding: utf-8 -*-
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")

with open(
    "D:\\Works\\DMS\\scratch\\evaluated_results.json", "r", encoding="utf-8"
) as f:
    results = json.load(f)

# Phân nhóm các lỗi hệ thống của Old
error_groups = {}
for r in results:
    group = r["error_group"]
    if group not in error_groups:
        error_groups[group] = []
    error_groups[group].append(r)

print("CÁC NHÓM LỖI HỆ THỐNG CỦA BẢN CŨ (OLD):")
print("=" * 100)

for group, items in error_groups.items():
    print(f"\nNHÓM LỖI: {group} (Số lượng: {len(items)})")
    print("-" * 100)
    for i, item in enumerate(items[:5]):  # In tối đa 5 ví dụ tiêu biểu
        print(f"  Ví dụ {i + 1} (Row {item['row']}):")
        print(f'    Văn bản: "{item["text"]}"')
        print(
            f"    Old nhãn: {item['old_labels']} | Sentiment: {item['old_sentiment']}"
        )
        print(f"    V3 nhãn : {item['v3_labels']} | Sentiment: {item['v3_sentiment']}")
        print(f"    Lý do V3 tốt hơn: {item['reason']}")
        print()
    if len(items) > 5:
        print(f"  ... và {len(items) - 5} ví dụ khác.")
