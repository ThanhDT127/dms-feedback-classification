import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")


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


def check_row_truth(r):
    # Rule-based business logic validation
    t = r["text"]
    old_lbl = r["old_labels"]
    v3_lbl = r["v3_labels"]
    old_sent = r["old_sentiment"]
    v3_sent = r["v3_sentiment"]

    t_lower = t.lower()

    # Empty / nan
    if not t or t == "nan" or t == "null":
        return ["Tin trung lập"], "—"

    # Check competitor names (strict brand boundaries)
    competitors = [
        "mpe",
        "sopoka",
        "sino",
        "duhal",
        "tran phu",
        "trần phú",
        "g8",
        "nival",
        "ominsu",
        "omisu",
        "nanoco",
        "okas",
        "hc lighting",
        "philips",
        "havaco",
        "vinasun",
        "elink",
        "panasonic",
        "pana",
        "lumi",
        "sunhouse",
        "việt nhật",
        "tý liên",
        "ty liên",
        "kaisu",
        "maxben",
        "du hal",
        "tiền phong",
    ]

    has_comp = any(c in t_lower for c in competitors)

    # Check if competitor is only a reference (e.g. "làm màu cam giống sopoka", "giống Điện Quang")
    is_ref = False
    if has_comp:
        ref_patterns = [
            "giống như",
            "giống hãng",
            "như hãng",
            "như đối thủ",
            "giống sofoka",
            "giống sopoka",
            "giống sino",
            "giống điện quang",
            "như điện quang",
            "giống hoa sen",
        ]
        for p in ref_patterns:
            if p in t_lower:
                is_ref = True
                break

    if has_comp and not is_ref:
        # Competitor category - strictly ONLY competitor labels are allowed: "Hãng", "Hoạt động", "CTKM, giá, cơ chế", "TT SP"
        gt_labels = ["Hãng"]

        # CTKM, giá, cơ chế of competitor
        if any(
            k in t_lower
            for k in [
                "ck",
                "chiết khấu",
                "giá",
                "tặng",
                "khuyến mại",
                "km",
                "du lịch",
                "gói",
                "đạt vé",
                "thưởng",
                "hội nghị",
                "rẻ hơn",
                "giá buôn",
                "bán thấp hơn",
            ]
        ):
            gt_labels.append("CTKM, giá, cơ chế")

        # Hoạt động of competitor
        if any(
            k in t_lower
            for k in [
                "kệ",
                "trưng bày",
                "biển hiệu",
                "biển mới",
                "bảng",
                "hội nghị",
                "triển khai",
                "thay 100% biển",
            ]
        ):
            gt_labels.append("Hoạt động")

        # TT SP of competitor
        if any(
            k in t_lower
            for k in [
                "mẫu",
                "màu",
                "thanh đồng",
                "chân cắm",
                "phi 5",
                "lá đồng",
                "3 pha",
                "tương đương",
                "cốc",
                "ấm siêu tốc",
                "20a",
                "30a",
                "vợt bắt muỗi",
                "đèn bắt muỗi",
                "nhẹ hơn",
                "dòng 300w",
                "bọc trong màng co",
                "cầm lỏng lẻo",
            ]
        ):
            gt_labels.append("TT SP")

        gt_sent = "—"
        if any(
            k in t_lower
            for k in [
                "mẫu đẹp",
                "chất lượng tốt",
                "được bọc trong",
                "dễ vận chuyển",
                "gọn",
                "rẻ",
            ]
        ):
            gt_sent = "Tích cực"
        elif any(k in t_lower for k in ["mỏng quá", "lỏng lẻo", "kém chất lượng"]):
            gt_sent = "Tiêu cực"

        return gt_labels, gt_sent

    # Rạng Đông / Non-competitor category
    gt_labels = []

    # 1. Báo lỗi
    err_keywords = [
        "lỗi",
        "không sáng",
        "hư",
        "cháy",
        "không vào điện",
        "hỏng",
        "thụn đui",
        "bể",
        "nứt",
        "vỡ",
        "đọng nước",
        "tích pin kém",
        "mau nguội",
        "rỉ nước",
        "chập",
        "kém",
        "nhanh hỏng",
        "không tự ngắt",
        "sạc không đầy",
        "nhanh nguội",
        "gãy pass",
        "muội",
        "vô nước",
        "chết led",
        "thoát nhiệt",
        "yếu",
        "mùi tanh",
        "bị đơ",
        "khó ấn",
        "không nhạy",
        "lỏng lẻo",
    ]
    if any(k in t_lower for k in err_keywords) or "giữ nhiệt kém" in t_lower:
        gt_labels.append("Báo lỗi")

    # 2. Y/c cải tiến
    imp_keywords = [
        "cải tiến",
        "cần làm",
        "nên làm",
        "làm mỏng",
        "nâng công suất",
        "in chữ nổi",
        "thêm nắp che",
        "làm dày",
        "thêm đèn",
        "dây dài hơn",
        "in thêm",
        "đóng thành dây",
        "thay đổi",
        "cần lỗ quét",
        "làm lớp nilon",
        "điều chỉnh",
        "nắp che an toàn",
        "in luôn số a",
    ]
    if any(k in t_lower for k in imp_keywords):
        gt_labels.append("Y/c cải tiến")

    # 3. Đề xuất SPM
    spm_keywords = [
        "ra thêm",
        "sản xuất thêm",
        "sản xuất lại",
        "có thêm loại",
        "thêm size",
        "mã mới",
        "thêm loại",
        "ra dòng mới",
        "thêm dòng",
        "làm thêm bóng",
        "làm thêm tủ nhựa",
    ]
    if any(k in t_lower for k in spm_keywords):
        gt_labels.append("Đề xuất SPM")

    # 4. Bảng giá, Catalogue
    if any(
        k in t_lower
        for k in ["catalogue", "cateloge", "câtloge", "báo giá", "bảng giá"]
    ):
        gt_labels.append("Bảng giá, Catalogue")

    # 5. Bảng biển
    if any(
        k in t_lower
        for k in [
            "biển hiệu",
            "biển quảng cáo",
            "bảng quảng cáo",
            "làm biển",
            "cấp biển",
            "bảng hiệu",
            "làm bảng biển",
        ]
    ):
        gt_labels.append("Bảng biển")

    # 6. Kệ bóng, thử đèn,…
    if any(
        k in t_lower
        for k in [
            "kệ",
            "trưng bày",
            "demo",
            "bộ test",
            "bảng test",
            "kệ bày hàng",
            "tủ bày",
            "dụng cụ thử bóng",
            "kệ bóng",
        ]
    ):
        gt_labels.append("Kệ bóng, thử đèn,…")

    # 7. Bảo hành
    if any(
        k in t_lower
        for k in ["bảo hành", "đổi trả", "đổi mới", "ứng vật tư", "giải quyết bảo hành"]
    ):
        gt_labels.append("Bảo hành")

    # 8. Hàng hoá
    if any(
        k in t_lower
        for k in [
            "bán chậm",
            "tồn kho",
            "giao hàng chậm",
            "thiếu hàng",
            "nhập chậm",
            "khó bán",
            "hàng chậm",
            "giao chậm",
            "ra chậm quá",
            "chưa nhập thêm",
        ]
    ):
        gt_labels.append("Hàng hoá")

    # 9. HTPP
    if any(
        k in t_lower
        for k in [
            "npp",
            "nhà phân phối",
            "tràn vùng",
            "phá giá",
            "đại lý giao",
            "hoàng quý",
            "quang phú",
            "hưng chiến",
            "tùng gia phát",
            "ngọc huy",
            "ngọc hiếu",
            "lê minh phát",
            "buôn hưng yên",
            "buôn thái bình",
            "nhập hàng từ",
        ]
    ):
        gt_labels.append("HTPP")

    # 10. Trả thưởng
    if any(
        k in t_lower
        for k in [
            "thưởng",
            "quay số",
            "trúng thưởng",
            "chương trình quay",
            "trả thưởng",
            "gói quay",
            "c2td",
            "nợ lâu",
        ]
    ):
        gt_labels.append("Trả thưởng")

    # 11. Đề xuất
    if "đề xuất" in t_lower or "cty bình ổn" in t_lower or "cty xem" in t_lower:
        gt_labels.append("Đề xuất")

    # 12. Tốt/ ko tốt
    if any(
        k in t_lower
        for k in ["giá", "cơ chế", "chiết khấu", "đắt", "rẻ", "hợp lý", "khó bán"]
    ):
        if not any(
            lbl in gt_labels for lbl in ["Bảng giá, Catalogue", "Trả thưởng", "HTPP"]
        ):
            gt_labels.append("Tốt/ ko tốt")

    # 13. Báo CL tốt
    if any(
        k in t_lower
        for k in [
            "chất lượng tốt",
            "ánh sáng ổn định",
            "ít khi bị lỗi",
            "chất lượng",
            "hiệu suất cao",
        ]
    ):
        if "Báo lỗi" not in gt_labels:
            gt_labels.append("Báo CL tốt")

    # 14. Tin trung lập
    if not gt_labels:
        gt_labels.append("Tin trung lập")

    gt_labels = list(set(gt_labels))

    # Guarantee Tin trung lập rule
    if "Tin trung lập" in gt_labels and len(gt_labels) > 1:
        gt_labels = [l for l in gt_labels if l != "Tin trung lập"]

    # Sentiment rules
    gt_sent = "—"
    if "Báo CL tốt" in gt_labels:
        gt_sent = "Tích cực"
    elif "Báo lỗi" in gt_labels or "Hàng hoá" in gt_labels or "Bảo hành" in gt_labels:
        gt_sent = "Tiêu cực"
    elif "Tốt/ ko tốt" in gt_labels:
        if any(k in t_lower for k in ["hợp lý", "tốt", "rẻ"]):
            gt_sent = "Tích cực"
        elif any(k in t_lower for k in ["cao", "đắt", "khó bán"]):
            gt_sent = "Tiêu cực"

    return gt_labels, gt_sent


def evaluate_quality(records):
    evaluated = []

    for r in records:
        text = r["text"]
        old_lbl = r["old_labels"]
        v3_lbl = r["v3_labels"]
        old_sent = r["old_sentiment"]
        v3_sent = r["v3_sentiment"]

        # Determine Ground Truth based on exact logic
        gt_lbl, gt_sent = check_row_truth(r)

        old_lbl_set = set(old_lbl)
        v3_lbl_set = set(v3_lbl)
        gt_lbl_set = set(gt_lbl)

        old_lbl_correct = old_lbl_set == gt_lbl_set
        v3_lbl_correct = v3_lbl_set == gt_lbl_set

        old_sent_correct = old_sent == gt_sent
        v3_sent_correct = v3_sent == gt_sent

        old_correct = old_lbl_correct and old_sent_correct
        v3_correct = v3_lbl_correct and v3_sent_correct

        old_jaccard = (
            len(old_lbl_set.intersection(gt_lbl_set))
            / len(old_lbl_set.union(gt_lbl_set))
            if len(old_lbl_set.union(gt_lbl_set)) > 0
            else 1.0
        )
        v3_jaccard = (
            len(v3_lbl_set.intersection(gt_lbl_set)) / len(v3_lbl_set.union(gt_lbl_set))
            if len(v3_lbl_set.union(gt_lbl_set)) > 0
            else 1.0
        )

        better_side = "V3"
        if old_jaccard > v3_jaccard:
            better_side = "Old"
        elif old_jaccard == v3_jaccard:
            if old_sent_correct and not v3_sent_correct:
                better_side = "Old"
            elif v3_sent_correct and not old_sent_correct:
                better_side = "V3"
            else:
                better_side = "Both"

        evaluated.append(
            {
                "row": r["row"],
                "text": text,
                "old_labels": old_lbl,
                "old_sentiment": old_sent,
                "v3_labels": v3_lbl,
                "v3_sentiment": v3_sent,
                "gt_labels": gt_lbl,
                "gt_sentiment": gt_sent,
                "old_correct": old_correct,
                "v3_correct": v3_correct,
                "old_jaccard": old_jaccard,
                "v3_jaccard": v3_jaccard,
                "better": better_side,
            }
        )

    return evaluated


rec1 = parse_all_file(r"D:\Works\DMS\scratch\all_DMS-1510-1710.txt")
rec2 = parse_all_file(r"D:\Works\DMS\scratch\all_DMS-1810-1910.txt")

eval1 = evaluate_quality(rec1)
eval2 = evaluate_quality(rec2)


def print_final_accurate_report(eval_list, name):
    total = len(eval_list)
    old_correct_count = sum(1 for x in eval_list if x["old_correct"])
    v3_correct_count = sum(1 for x in eval_list if x["v3_correct"])

    # Calculate strictly better, equal, and correct stats
    v3_strictly_better = sum(
        1 for x in eval_list if x["better"] == "V3" and not x["old_correct"]
    )
    old_strictly_better = sum(
        1 for x in eval_list if x["better"] == "Old" and not x["v3_correct"]
    )
    both_correct = sum(1 for x in eval_list if x["old_correct"] and x["v3_correct"])
    both_incorrect_equal = sum(
        1
        for x in eval_list
        if not x["old_correct"] and not x["v3_correct"] and x["better"] == "Both"
    )

    print(f"\n=================== REPORT FOR {name} ===================")
    print(f"Total evaluated records: {total}")
    print(
        f"Old 100% correct accuracy: {old_correct_count} ({old_correct_count / total * 100:.2f}%)"
    )
    print(
        f"V3 100% correct accuracy: {v3_correct_count} ({v3_correct_count / total * 100:.2f}%)"
    )
    print(
        f"V3 strictly better (and Old incorrect): {v3_strictly_better} rows ({v3_strictly_better / total * 100:.2f}%)"
    )
    print(
        f"Old strictly better (and V3 incorrect): {old_strictly_better} rows ({old_strictly_better / total * 100:.2f}%)"
    )
    print(f"Both 100% correct: {both_correct} rows ({both_correct / total * 100:.2f}%)")
    print(
        f"Both incorrect and equally bad: {both_incorrect_equal} rows ({both_incorrect_equal / total * 100:.2f}%)"
    )


print_final_accurate_report(eval1, "DMS-1510-1710.xlsx")
print_final_accurate_report(eval2, "DMS-1810-1910.xlsx")
