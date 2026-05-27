# -*- coding: utf-8 -*-
import json

with open('D:\\Works\\DMS\\scratch\\parsed_rows.json', 'r', encoding='utf-8') as f:
    rows = json.load(f)

# Phân loại và phân tích từng câu
# Ta có các nhãn:
# 'Tốt/ ko tốt', 'Tin trung lập', 'Đề xuất SPM', 'Y/c cải tiến', 'Báo lỗi', 'Hãng', 'Hoạt động', 'CTKM, giá, cơ chế', 'Trả thưởng', 'Báo CL tốt', 'Bảng giá, Catalogue', 'HTPP', 'TT SP', 'Bảo hành', 'Hàng hoá', 'Bảng biển', 'Kệ bóng, thử đèn,…', 'Đề xuất'
# Hãy in ra danh sách các khác biệt để chúng ta có thể đọc và phân loại:

print("DANH SÁCH KHÁC BIỆT NHÃN VÀ SENTIMENT:")
print("="*80)

for idx, r in enumerate(rows):
    print(f"Row {r['row']}: {r['text']}")
    print(f"  Old: {r['old_labels']} | Sentiment: {r['old_sentiment']}")
    print(f"  V3 : {r['v3_labels']} | Sentiment: {r['v3_sentiment']}")
    print("-"*80)
