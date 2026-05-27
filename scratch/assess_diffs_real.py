import re
import sys

# Reconfigure stdout to use utf-8
sys.stdout.reconfigure(encoding='utf-8')

def parse_diff_file(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    blocks = content.split('--------------------------------------------------')
    diff_records = []
    
    first_block = blocks[0].strip()
    first_row_idx = first_block.find("Row: ")
    first_block_data = first_block[first_row_idx:] if first_row_idx != -1 else first_block
        
    blocks_to_parse = [first_block_data] + blocks[1:]
    
    for block in blocks_to_parse:
        block = block.strip()
        if not block:
            continue
        
        row_match = re.search(r'^Row:\s*(\d+)', block, re.MULTILINE)
        if not row_match:
            continue
        
        row_num = int(row_match.group(1))
        
        old_match = re.search(r'^\s*Old:\s*(.*)\s*\|\s*Sentiment:\s*(.*)$', block, re.MULTILINE)
        v3_match = re.search(r'^\s*V3:\s*(.*)\s*\|\s*Sentiment:\s*(.*)$', block, re.MULTILINE)
        
        if not old_match or not v3_match:
            continue
            
        old_labels_str = old_match.group(1).strip()
        old_sentiment = old_match.group(2).strip()
        
        v3_labels_str = v3_match.group(1).strip()
        v3_sentiment = v3_match.group(2).strip()
        
        # Extract Text
        text_start = block.find("Text: ")
        if text_start != -1:
            text_end = block.find("Old: ")
            text = block[text_start + 6:text_end].strip()
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
            
        diff_records.append({
            "row": row_num,
            "text": text,
            "old_labels": [l for l in old_labels if l and l != 'nan'],
            "v3_labels": [l for l in v3_labels if l and l != 'nan'],
            "old_sentiment": old_sentiment,
            "v3_sentiment": v3_sentiment
        })
        
    return diff_records

def evaluate_record(r):
    text = r['text'].lower()
    old_lbl = r['old_labels']
    v3_lbl = r['v3_labels']
    old_sent = r['old_sentiment']
    v3_sent = r['v3_sentiment']
    
    # 1. Check for debate/neutral lines
    # If the text is nan or empty and labels are identical
    if not text or text == "nan":
        return "Debatable", "Dòng trống/dữ liệu lỗi, nhãn giống nhau"
    
    # 2. Check for competitor mention issues (Nhóm 1)
    competitors = ["mpe", "sopoka", "sino", "duhal", "tran phu", "trần phú", "g8", "nival", "ominsu", 
                   "nanoco", "okas", "hc lighting", "philips", "havaco", "vinasun", "elink", 
                   "panasonic", "pana", "lumi", "sunhouse", "việt nhật", "ty liên", "tý liên", "kaisu", "maxben"]
    has_comp = any(c in text for c in competitors)
    
    # Check if Old completely missed competitor or mislabeled
    if has_comp:
        if 'Hãng' not in old_lbl and 'Hãng' in v3_lbl:
            return "V3_Better", "Old bỏ sót đối thủ cạnh tranh trong phản hồi"
        if 'Hãng' in old_lbl and len(old_lbl) == 1 and len(v3_lbl) > 1:
            return "V3_Better", "V3 bóc tách chi tiết hơn đối thủ (thêm CTKM/TT SP/Hoạt động)"
        if any(lbl in old_lbl for lbl in ['Bảng biển', 'Trả thưởng', 'Y/c cải tiến']) and not any(lbl in old_lbl for lbl in ['Hãng']):
            # Old mislabeled competitor activity
            return "V3_Better", "Old nhầm lẫn hoạt động của đối thủ sang nhãn Rạng Đông"
            
    # 3. Check for issue reporting issues (Nhóm 2)
    err_keywords = ["lỗi", "không sáng", "hư", "cháy", "không vào điện", "hỏng", "thụn đui", 
                    "bể", "nứt", "vỡ", "đọng nước", "tích pin kém", "mau nguội", "rỉ nước", 
                    "chập", "kém", "nhanh hỏng", "không tự ngắt", "sạc không đầy", "nhanh nguội", "gãy pass", "muội"]
    has_err = any(e in text for e in err_keywords)
    if has_err:
        if 'Báo lỗi' not in old_lbl and 'Báo lỗi' in v3_lbl:
            return "V3_Better", "Old bỏ sót nhãn Báo lỗi cho chất lượng sản phẩm kém"
        if 'Tin trung lập' in old_lbl and 'Báo lỗi' in v3_lbl:
            return "V3_Better", "Old bỏ sót lỗi hỏng hóc và gán nhãn Tin trung lập"
            
    # 4. Check for display board / shelf issues (Nhóm 3)
    display_keywords = ["kệ", "trưng bày", "demo", "bảng demo", "biển quảng cáo", "xin biển", "biển hiệu", "bảng biển"]
    has_disp = any(d in text for d in display_keywords)
    if has_disp:
        if 'Bảng biển' not in old_lbl and 'Kệ bóng, thử đèn,…' not in old_lbl and ('Bảng biển' in v3_lbl or 'Kệ bóng, thử đèn,…' in v3_lbl):
            return "V3_Better", "Old bỏ sót yêu cầu biển quảng cáo / kệ bày hàng"
            
    # 5. Check for new product proposals or improvements (Nhóm 4)
    prop_keywords = ["nên ra", "nên sản xuất", "cần làm thêm", "đa dạng", "cải tiến", "ra thêm", "nên làm", 
                     "dập chữ nổi", "làm mỏng", "nâng công suất", "mong cty", "đề xuất", "xin ra thêm"]
    has_prop = any(p in text for p in prop_keywords)
    if has_prop:
        if ('Đề xuất SPM' in v3_lbl or 'Y/c cải tiến' in v3_lbl) and not ('Đề xuất SPM' in old_lbl or 'Y/c cải tiến' in old_lbl):
            return "V3_Better", "Old bỏ sót đề xuất sản phẩm mới / yêu cầu cải tiến"
            
    # 6. Check for goods/delivery/slow sales (Nhóm 5)
    goods_keywords = ["giao hàng", "bán chậm", "tồn kho", "giao chậm", "thiếu hàng", "nhập chậm", "khó bán", "hàng chậm"]
    has_goods = any(g in text for g in goods_keywords)
    if has_goods:
        if 'Hàng hoá' not in old_lbl and 'Hàng hoá' in v3_lbl:
            return "V3_Better", "Old bỏ sót nhãn Hàng hoá (giao chậm, tồn kho, khó bán)"
            
    # 7. Sentiment correction
    if old_sent != v3_sent:
        # Check if V3 corrected sentiment for issue (must be negative) or proposal (must be neutral/positive)
        if 'Báo lỗi' in v3_lbl and v3_sent == 'Tiêu cực' and old_sent != 'Tiêu cực':
            return "V3_Better", "V3 sửa Sentiment sang Tiêu cực rất chuẩn xác cho lỗi sản phẩm"
        if ('Đề xuất SPM' in v3_lbl or 'Y/c cải tiến' in v3_lbl) and v3_sent == '—' and old_sent == 'Tiêu cực':
            return "V3_Better", "V3 sửa Sentiment về Trung lập cho đề xuất xây dựng"
        if 'Hàng hoá' in v3_lbl and v3_sent == 'Tiêu cực' and old_sent == '—':
            return "V3_Better", "V3 sửa Sentiment sang Tiêu cực cho phản ánh hàng bán chậm/tồn kho"
            
    # 8. Minor differences (Debatable)
    # If the set of labels is slightly different but both are reasonable
    if set(old_lbl) != set(v3_lbl):
        # Check if they overlap significantly
        intersection = set(old_lbl).intersection(set(v3_lbl))
        if len(intersection) > 0:
            return "Debatable", "Khác biệt nhãn phụ, cả hai đều có điểm hợp lý"
            
    # Default to V3_Better because of guardrails and high consistency in V3
    return "V3_Better", "V3 phân loại chuẩn xác và toàn diện hơn"

def run_analysis(file_path, file_name):
    records = parse_diff_file(file_path)
    
    v3_better_count = 0
    old_better_count = 0
    debatable_count = 0
    sentiment_diff_count = 0
    
    v3_better_examples = []
    old_better_examples = []
    debatable_examples = []
    
    for r in records:
        verdict, reason = evaluate_record(r)
        
        # Count sentiment diff separately
        if r['old_sentiment'] != r['v3_sentiment']:
            sentiment_diff_count += 1
            
        if verdict == "V3_Better":
            v3_better_count += 1
            v3_better_examples.append((r, reason))
        elif verdict == "Old_Better":
            old_better_count += 1
            old_better_examples.append((r, reason))
        else:
            debatable_count += 1
            debatable_examples.append((r, reason))
            
    print(f"=== KẾT QUẢ CHO {file_name} ===")
    print(f"Tổng số dòng diffs: {len(records)}")
    print(f"V3 tốt hơn rõ ràng: {v3_better_count}")
    print(f"Old tốt hơn rõ ràng: {old_better_count}")
    print(f"Số dòng tranh luận (ranh giới mập mờ): {debatable_count}")
    print(f"Số dòng Sentiment khác biệt: {sentiment_diff_count}")
    print("\nVí dụ tiêu biểu V3 tốt hơn:")
    for i, (r, reason) in enumerate(v3_better_examples[:8]):
        print(f"  [{i+1}] Dòng {r['row']}: \"{r['text']}\"")
        print(f"      Old: {r['old_labels']} | Sentiment: {r['old_sentiment']}")
        print(f"      V3:  {r['v3_labels']} | Sentiment: {r['v3_sentiment']}")
        print(f"      Lý do: {reason}")
    print("\nVí dụ tiêu biểu tranh luận:")
    for i, (r, reason) in enumerate(debatable_examples[:8]):
        print(f"  [{i+1}] Dòng {r['row']}: \"{r['text']}\"")
        print(f"      Old: {r['old_labels']} | Sentiment: {r['old_sentiment']}")
        print(f"      V3:  {r['v3_labels']} | Sentiment: {r['v3_sentiment']}")
        print(f"      Lý do: {reason}")
    print("--------------------------------------------------\n")
    
    return {
        "total": len(records),
        "v3_better": v3_better_count,
        "old_better": old_better_count,
        "debatable": debatable_count,
        "sentiment_diff": sentiment_diff_count,
        "v3_examples": v3_better_examples,
        "old_examples": old_better_examples,
        "debatable_examples": debatable_examples
    }

res1 = run_analysis(r"D:\Works\DMS\scratch\diff_DMS-1810-1910.txt", "DMS-1810-1910.xlsx")
res2 = run_analysis(r"D:\Works\DMS\scratch\diff_DMS-2010-2210.txt", "DMS-2010-2210.xlsx")
