"""Pure-LLM issue classifier with dynamic keyword/brand hints and post-validation."""

from __future__ import annotations

import json
import logging
import re
import time
from textwrap import dedent

from unidecode import unidecode

from ..gemini_client import GeminiClient
from ..settings import Settings

logger = logging.getLogger("dms-watcher")


def canon(s: str) -> str:
    """Normalize text for brand and keyword comparison."""
    if not s:
        return ""
    text = unidecode(str(s)).lower()
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


MINOR_ORDER = [
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

MINOR_TO_MAJOR = {
    "Báo lỗi": "Sản phẩm",
    "Báo CL tốt": "Sản phẩm",
    "Y/c cải tiến": "Sản phẩm",
    "Đề xuất SPM": "Sản phẩm",
    "Bảng giá, Catalogue": "Yêu cầu công cụ BH",
    "Bảng biển": "Yêu cầu công cụ BH",
    "Kệ bóng, thử đèn,…": "Yêu cầu công cụ BH",
    "Khác": "Yêu cầu công cụ BH",
    "Tốt/ ko tốt": "Giá, cơ chế RD",
    "Trả thưởng": "Giá, cơ chế RD",
    "Đề xuất": "Giá, cơ chế RD",
    "Bảo hành": "Dịch vụ",
    "HTPP": "Dịch vụ",
    "Hàng hoá": "Dịch vụ",
    "Hàng giả": "Hàng giả",
    "Website": "Website",
    "Hãng": "Đối thủ cạnh tranh",
    "Hoạt động": "Đối thủ cạnh tranh",
    "CTKM, giá, cơ chế": "Đối thủ cạnh tranh",
    "TT SP": "Đối thủ cạnh tranh",
    "Tin trung lập": "Tin trung lập",
}

COMP_ALLOWED = ["Hãng", "Hoạt động", "CTKM, giá, cơ chế", "TT SP"]

RD_BRAND_ALIASES = {
    "rang dong",
    "r d",
    "rd",
    "rangdong",
    "rang dong jsc",
    "rang dong company",
    "ra ng dong",
}

NULL_BRAND_ALIASES = {
    "",
    "rong",
    "ro ng",
    "none",
    "null",
    "n a",
    "na",
    "khong",
    "kh ong",
    "empty",
}

LABEL_DEFINITIONS = {
    "Báo lỗi": "Sản phẩm vật lý bị lỗi, hỏng, cháy, không sáng, không hoạt động, sai chức năng. Chỉ dùng khi có lỗi kỹ thuật thực tế.",
    "Báo CL tốt": "Khen chất lượng sản phẩm tốt, bền, sáng tốt, ổn định, khách hài lòng.",
    "Y/c cải tiến": "Yêu cầu chỉnh sửa thiết kế, tính năng, bao bì, mẫu mã của SP HIỆN CÓ đang bán. SP vẫn hoạt động nhưng khách muốn thay đổi.",
    "Đề xuất SPM": "Đề xuất sản xuất SP MỚI chưa có: mã mới, kích thước mới, loại mới ('ra thêm', 'sản xuất thêm', 'thêm loại', 'có thêm', 'mã mới').",
    "Bảng giá, Catalogue": "Yêu cầu cung cấp bảng giá, báo giá, catalogue, tài liệu bán hàng.",
    "Bảng biển": "Yêu cầu hỗ trợ biển hiệu, biển quảng cáo, bảng hiệu cửa hàng, POSM dạng biển.",
    "Kệ bóng, thử đèn,…": "Yêu cầu kệ trưng bày, kệ bóng, tủ thử bóng, bộ test đèn, dụng cụ demo.",
    "Khác": "Yêu cầu công cụ BH CỤ THỂ khác (áo đồng phục, tờ rơi, sổ tay, POSM) mà KHÔNG phải bảng giá, biển hiệu, hay kệ. KHÔNG dùng làm nhãn mặc định.",
    "Tốt/ ko tốt": "Nhận xét về giá/cơ chế của RẠNG ĐÔNG: giá tốt/cao/rẻ, khó bán, dễ bán, cạnh tranh. Từ khóa: 'giá rẻ', 'giá cao', 'đắt hơn', 'chiết khấu', 'cơ chế', 'khó bán'.",
    "Trả thưởng": "Nhắc CỤ THỂ đến tiền thưởng, quay số, gói quay, c2td, trả thưởng chậm của Rạng Đông.",
    "Đề xuất": "ĐỀ NGHỊ thay đổi chính sách giá, cơ chế, chiết khấu, khuyến mãi CHUNG của RĐ. Khác Trả thưởng (hỏi thưởng cụ thể).",
    "Bảo hành": "Nói về QUY TRÌNH bảo hành, đổi trả, thời gian BH, hậu mãi — tức DỊCH VỤ. Khác Báo lỗi (nói về SP hỏng).",
    "HTPP": "Hệ thống phân phối: xung đột kênh C1/C2, tràn vùng, nhà phân phối, đại lý.",
    "Hàng hoá": "Logistics: tồn kho, thiếu hàng, giao hàng chậm, vận chuyển, đóng gói.",
    "Hàng giả": "Nghi ngờ hàng GIẢ/NHÁI, giả mạo thương hiệu. KHÔNG dùng cho SP kém CL chính hãng (đó là Báo lỗi).",
    "Website": "Lỗi PHẦN MỀM: web, app, portal, DMS, đăng nhập, hệ thống chậm/đơ. KHÔNG dùng cho lỗi SP vật lý.",
    "Hãng": "Có nhắc đến hãng khác ngoài Rạng Đông (đối thủ cạnh tranh). Ghi tên hãng vào brand.",
    "Hoạt động": "Hoạt động marketing, trưng bày, tặng kệ, event, roadshow, tài trợ CỦA ĐỐI THỦ.",
    "CTKM, giá, cơ chế": "Giá bán, khuyến mãi, chiết khấu, chính sách bán hàng CỦA ĐỐI THỦ cạnh tranh.",
    "TT SP": "Thông tin sản phẩm, mẫu mã, tính năng, thông số, catalogue CỦA ĐỐI THỦ cạnh tranh.",
    "Tin trung lập": "Câu hoàn toàn trung tính, không khen/chê/đề xuất/yêu cầu gì. CHỈ gán khi không có nhãn nào khác.",
}


def get_list_any(data: dict, *keys: str) -> list[str]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
    normalized = {canon(key): value for key, value in data.items()}
    for key in keys:
        value = normalized.get(canon(key))
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
    return []


def keyword_hints(kw_map: dict, limit: int = 12) -> dict[str, list[str]]:
    hints = {}
    for label in MINOR_ORDER:
        keys = [label]
        if label == "HTPP":
            keys.append("HTTP")
        hints[label] = get_list_any(kw_map, *keys)[:limit]
    return hints


def brand_hints(kw_map: dict, limit: int = 80) -> dict[str, list[str]]:
    aliases = kw_map.get("manual_brand_alias", {})
    out = {}
    for idx, (brand, values) in enumerate(aliases.items()):
        if idx >= limit:
            break
        if isinstance(values, list):
            out[str(brand)] = [str(v) for v in values[:8]]
    return out


def _inside_code_fence(text: str) -> str:
    match = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S | re.I)
    return match.group(1).strip() if match else text


def _wrap_objects_to_array(text: str) -> str:
    wrapped = re.sub(r"}\s*{", "},{", text.strip())
    if not wrapped.startswith("["):
        wrapped = "[" + wrapped
    if not wrapped.endswith("]"):
        wrapped += "]"
    return wrapped


def _safe_json_loads(text: str):
    try:
        return json.loads(text)
    except Exception:
        return None


def _extract_json_anywhere(raw: str, expected_n: int):
    if not raw:
        return None
    text = _inside_code_fence(raw)
    obj = _safe_json_loads(text)
    if isinstance(obj, list):
        return obj
    if text.count("{") >= expected_n:
        arr = _safe_json_loads(_wrap_objects_to_array(text))
        if isinstance(arr, list):
            return arr
    out = []
    for line in [line for line in text.splitlines() if line.strip()]:
        match = re.search(r"\{.*\}", line.strip())
        if not match:
            continue
        item = _safe_json_loads(match.group(0))
        if isinstance(item, dict):
            out.append(item)
    return out or None


def _normalize_decision_log(log) -> list[dict]:
    norm = []
    for item in log or []:
        if isinstance(item, dict):
            minor = (item.get("minor") or item.get("label") or "").strip()
            action = (item.get("action") or "").strip().upper()
            why = (item.get("why") or item.get("reason") or item.get("evidence") or "").strip()
            if action not in ("ADD", "KEEP", "REMOVE"):
                action = "KEEP" if minor else ""
            if minor or why or action:
                norm.append({"minor": minor, "action": action, "why": why})
        elif isinstance(item, str):
            norm.append({"minor": "", "action": "", "why": item.strip()})
    return norm


def normalize_issue_output(
    parsed: dict,
    *,
    brand_fallback: str = "",
    sentiment_fallback: str = "",
    prelim_minors: list[str] | None = None,
) -> dict:
    """Normalize raw LLM output while preserving strict brand and label consistency."""
    # 1. Extract and sanitize brand
    brand_raw = (parsed.get("brand") or brand_fallback or "").strip()
    brand_can = canon(brand_raw)

    # If brand matches Rạng Đông or is empty, clear it in the spreadsheet representation
    if brand_can in NULL_BRAND_ALIASES or brand_can in RD_BRAND_ALIASES:
        brand_out = ""
        is_competitor = False
    else:
        brand_out = brand_raw
        is_competitor = bool(brand_can)

    # 2. Extract sentiment
    sentiment = (parsed.get("sentiment") or sentiment_fallback or "").strip()
    if sentiment not in ("Tích cực", "Tiêu cực", ""):
        sentiment = ""

    # 3. Extract and filter labels
    labels_dict = parsed.get("labels") or {}
    if not isinstance(labels_dict, dict):
        labels_dict = {}

    # Also handle if they returned a list final_minors instead
    final_minors = parsed.get("final_minors") or []
    if isinstance(final_minors, list):
        for lbl in final_minors:
            if lbl in MINOR_ORDER:
                labels_dict[lbl] = True

    # Build active labels list
    active_labels = [
        label
        for label in MINOR_ORDER
        if labels_dict.get(label) is True
        or str(labels_dict.get(label)).lower() in ("true", "1", "yes", "có", "co")
    ]

    # Apply strict competitor boundaries
    if is_competitor:
        # For competitor: ONLY competitor labels allowed
        active_labels = [label for label in active_labels if label in COMP_ALLOWED]
        if "Hãng" not in active_labels:
            active_labels.insert(0, "Hãng")
    else:
        # For non-competitor: competitor labels are forbidden
        active_labels = [label for label in active_labels if label not in COMP_ALLOWED]
        brand_out = ""  # Force brand to empty string to keep spreadsheet clean

    # Guarantee "Tin trung lập" rule: if any other label is present, remove Tin trung lập
    if "Tin trung lập" in active_labels and len(active_labels) > 1:
        active_labels = [label for label in active_labels if label != "Tin trung lập"]

    # If no labels are present, use fallback prelims (if provided) or fallback to "Tin trung lập"
    if not active_labels:
        if prelim_minors:
            active_labels = [lbl for lbl in prelim_minors if lbl in MINOR_ORDER]
        else:
            active_labels = ["Tin trung lập"]

    # Extract decision log
    decision_log = parsed.get("decision_log") or []
    if not isinstance(decision_log, list):
        decision_log = []

    return {
        "final_minors": active_labels,
        "sentiment": sentiment,
        "brand": brand_out,
        "decision_log": _normalize_decision_log(decision_log),
    }


class IssueClassifier:
    """Pure-LLM issue classifier utilizing dynamic keyword hints and Python guardrails."""

    def __init__(self, gemini: GeminiClient, settings: Settings) -> None:
        self.gemini = gemini
        self.settings = settings

    def _load_kw_map(self) -> dict:
        kw_map_path = self.settings.kw_map_path
        if not kw_map_path.exists():
            logger.warning("kw_map.json not found at %s. Using empty fallback dict.", kw_map_path)
            return {}
        try:
            return json.loads(kw_map_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("Failed to load kw_map.json: %s", exc)
            return {}

    def _llm_json_call(self, prompt: str) -> str:
        last_err: Exception | None = None
        for attempt in range(1, self.settings.max_retry + 1):
            try:
                return self.gemini.generate_json(prompt, temperature=0.0)
            except Exception as exc:
                last_err = exc
                try:
                    return self.gemini.generate(prompt, temperature=0.0)
                except Exception as fallback_exc:
                    last_err = fallback_exc
                    if attempt == self.settings.max_retry:
                        logger.error("Pure-LLM issue classifier fail: %s", last_err)
                        return ""
                    time.sleep(self.settings.base_wait * attempt)
        return ""

    def classify_batch(
        self,
        texts: list[str],
        matched_products: list[dict] | None = None,
        debug: bool = False,
    ) -> list[dict]:
        if not texts:
            return []

        kw_map = self._load_kw_map()
        
        # Build prompt rows
        prompt_rows = []
        for idx, text in enumerate(texts):
            prod = matched_products[idx] if matched_products and idx < len(matched_products) else {}
            prompt_rows.append({
                "row_index": idx,
                "text": text,
                "matched_product": {
                    "model": prod.get("Model") or prod.get("model") or "",
                    "dong_sp": prod.get("Dòng SP") or prod.get("dong_sp") or "",
                    "san_pham": prod.get("Sản phẩm") or prod.get("san_pham") or ""
                }
            })

        minor_order_json = json.dumps(MINOR_ORDER, ensure_ascii=False)
        label_defs = json.dumps(LABEL_DEFINITIONS, ensure_ascii=False, indent=2)
        hints_json = json.dumps(keyword_hints(kw_map), ensure_ascii=False, indent=2)
        brand_json = json.dumps(brand_hints(kw_map), ensure_ascii=False, indent=2)
        input_json = json.dumps(prompt_rows, ensure_ascii=False, indent=2)

        prompt = dedent(
            f"""
            Bạn là hệ thống phân loại phản hồi bán hàng và marketing cho ngành chiếu sáng và thiết bị điện Rạng Đông.

            MỤC ĐÍCH
            Mỗi dòng trong dữ liệu là một phản hồi thực tế từ thị trường, khách hàng, đại lý hoặc nhân viên bán hàng.
            Nhiệm vụ của bạn là đọc phản hồi, phân tích thương hiệu (brand), đối thủ cạnh tranh, cảm xúc (sentiment), các nhãn phân loại phù hợp, và ghi lại giải thích ngắn gọn.

            QUY TẮC PHÂN LOẠI CỐT LÕI
            1. KHÔNG SUY ĐOÁN: Chỉ gán nhãn khi có bằng chứng rõ ràng trong văn bản. Không tự bịa thông tin.
            2. ĐỘC LẬP TUYỆT ĐỐI: Mỗi dòng xử lý độc lập. Không dùng thông tin từ dòng khác.
            3. ĐA NHÃN: Một phản hồi có thể gán 2-3 nhãn nếu nội dung đề cập rõ nhiều vấn đề khác nhau. Nhưng KHÔNG gán nhãn khi chỉ "ngầm hiểu" mà không có từ ngữ cụ thể.
            4. RẠNG ĐÔNG BRAND: "Rạng Đông", "Rang Dong", "RD", "RĐ" là Rạng Đông.
            5. "Đề xuất SPM" VS "Y/c cải tiến":
               - "Đề xuất SPM": Yêu cầu sản xuất thêm, ra mắt model/kích thước/loại MỚI chưa có ("ra thêm", "sản xuất thêm", "có thêm loại", "thêm size", "mã mới").
               - "Y/c cải tiến": Yêu cầu chỉnh sửa SP HIỆN CÓ đang bán ("vỏ mỏng cần cứng hơn", "phích cắm to cần nhỏ gọn lại").
            6. "Báo lỗi" VS "Bảo hành":
               - "Báo lỗi": SP bị hỏng, cháy, không sáng, không hoạt động — nói về LỖI VẬT LÝ của sản phẩm.
               - "Bảo hành": Nói về QUY TRÌNH đổi trả, thời gian BH chậm, thủ tục BH — nói về DỊCH VỤ.
               - Nếu câu vừa nhắc SP hỏng vừa nhắc đổi trả/BH → gán CẢ HAI.
            7. "Báo lỗi" VS "Y/c cải tiến":
               - "Báo lỗi": SP đã hỏng, chết, không dùng được.
               - "Y/c cải tiến": SP hoạt động bình thường nhưng thiết kế chưa tốt, khách muốn thay đổi.
               - Nếu thiết kế kém DẪN ĐẾN hỏng → gán cả hai.
            8. "Đề xuất" VS "Trả thưởng":
               - "Trả thưởng": Nhắc CỤ THỂ đến tiền thưởng, quay số, gói quay, c2td, trả thưởng chậm.
               - "Đề xuất": Gợi ý thay đổi chính sách giá/chiết khấu/khuyến mãi/cơ chế CHUNG của RĐ.
            9. "Hàng hoá" VS "HTPP":
               - "Hàng hoá": Giao hàng chậm, thiếu hàng, tồn kho, vận chuyển — vấn đề LOGISTICS.
               - "HTPP": Xung đột kênh C1/C2 phá giá, tràn vùng bán hàng — vấn đề KÊNH PHÂN PHỐI.
            10. "Hàng giả" VS "Báo lỗi": "Hàng giả" CHỈ khi nghi hàng nhái/giả mạo thương hiệu. SP kém CL nhưng là hàng chính hãng → "Báo lỗi", KHÔNG phải "Hàng giả".
            11. "Website" VS "Báo lỗi": "Website" cho lỗi phần mềm/app/web/portal. "Báo lỗi" cho lỗi sản phẩm vật lý. Hai thứ khác nhau hoàn toàn.
            12. NHÃN "Khác": CHỈ dùng khi khách yêu cầu công cụ BH cụ thể (áo, tờ rơi, sổ tay, POSM) mà KHÔNG phải bảng giá, biển hiệu, hay kệ. KHÔNG dùng "Khác" như nhãn mặc định.
            13. NHÓM CÔNG CỤ BH: "Bảng giá, Catalogue" cho bảng giá/catalogue. "Bảng biển" cho biển hiệu/biển QC. "Kệ bóng, thử đèn,…" cho kệ trưng bày/tủ thử.
            14. GIÁ CẢ RĐ ("Tốt/ ko tốt"): Bắt buộc gán nếu đề cập rõ giá rẻ/đắt/cao/chiết khấu/cơ chế của Rạng Đông.
            15. LUẬT CẠNH TRANH (ĐỐI THỦ):
               - Nếu brand là ĐỐI THỦ: CHỈ gán "Hãng", "Hoạt động", "CTKM, giá, cơ chế", "TT SP". Có thể gán nhiều nhãn đối thủ cùng lúc.
               - "Hoạt động": marketing/event/tặng kệ/tài trợ của đối thủ. "CTKM, giá, cơ chế": giá bán/chiết khấu/KM của đối thủ. "TT SP": mẫu mã/tính năng SP đối thủ.
               - Nếu brand là Rạng Đông hoặc không xác định: KHÔNG gán nhãn đối thủ.
               - KHI SO SÁNH giá RĐ với đối thủ (ví dụ: "RĐ đắt hơn MPE 23k"), ưu tiên gán cho ĐỐI THỦ (brand=tên đối thủ, nhãn CTKM/Hãng).
            16. SENTIMENT: Chỉ "Tích cực", "Tiêu cực", hoặc "". Vừa khen vừa chê → "Tiêu cực".
            17. TIN TRUNG LẬP: CHỈ gán khi câu hoàn toàn trung tính, không khen/chê/yêu cầu/đề xuất gì, và không gán nhãn nào khác.

            DANH SÁCH NHÃN HỢP LỆ THEO THỨ TỰ:
            {minor_order_json}

            ĐỊNH NGHĨA CHI TIẾT CÁC NHÃN:
            {label_defs}

            KEYWORD HINTS (CHỈ LÀ GỢI Ý — KHÔNG gán nhãn chỉ vì khớp từ khóa, phải đọc CẢ CÂU để hiểu ngữ cảnh):
            {hints_json}

            BRAND HINTS (Gợi ý nhận diện thương hiệu đối thủ):
            {brand_json}

            ĐẦU RA BẮT BUỘC:
            Trả về DUY NHẤT một JSON array hợp lệ. Không viết thêm bất kỳ từ ngữ nào ngoài JSON. Không dùng markdown code block (không có ```json ... ```).
            Số phần tử đầu ra phải bằng đúng số phần tử đầu vào. Giữ nguyên thứ tự và thuộc tính row_index.

            Mỗi phần tử output phải tuân theo cấu trúc sau:
            {{
              "row_index": 0,
              "brand": "Tên thương hiệu đối thủ (hoặc để trống '' nếu là Rạng Đông / không có)",
              "is_competitor": false,
              "sentiment": "Tích cực" | "Tiêu cực" | "",
              "labels": {{
                "Báo lỗi": false,
                "Báo CL tốt": false,
                "Y/c cải tiến": false,
                "Đề xuất SPM": false,
                "Bảng giá, Catalogue": false,
                "Bảng biển": false,
                "Kệ bóng, thử đèn,…": false,
                "Khác": false,
                "Tốt/ ko tốt": false,
                "Trả thưởng": false,
                "Đề xuất": false,
                "Bảo hành": false,
                "HTPP": false,
                "Hàng hoá": false,
                "Hàng giả": false,
                "Website": false,
                "Hãng": false,
                "Hoạt động": false,
                "CTKM, giá, cơ chế": false,
                "TT SP": false,
                "Tin trung lập": false
              }},
              "decision_log": [
                {{
                  "label": "Tên nhãn",
                  "action": "ADD | KEEP | REMOVE",
                  "evidence": "Cụm từ gốc làm minh chứng",
                  "reason": "Lý do gán nhãn ngắn gọn"
                }}
              ]
            }}

            DỮ LIỆU CẦN XỬ LÝ:
            {input_json}

            Hãy trả về kết quả dưới dạng JSON array duy nhất.
            """
        ).strip()

        raw = self._llm_json_call(prompt)
        if debug:
            preview = raw[:800] + ("..." if len(raw) > 800 else "")
            logger.debug("RAW pure-LLM issue classifier response: %s", preview or "∅")

        arr = _extract_json_anywhere(raw, expected_n=len(texts))
        if not isinstance(arr, list) or len(arr) == 0:
            arr = [{"labels": {}, "sentiment": "", "brand": "", "decision_log": [{"reason": "FALLBACK_PARSING"}]} for _ in texts]

        out = []
        for idx in range(len(texts)):
            parsed = arr[idx] if idx < len(arr) and isinstance(arr[idx], dict) else {}
            # Reconstruct index if mismatch
            parsed_idx = parsed.get("row_index")
            if parsed_idx is not None and parsed_idx != idx:
                # Find matching row index if scrambled
                matched_item = next((item for item in arr if isinstance(item, dict) and item.get("row_index") == idx), None)
                if matched_item:
                    parsed = matched_item

            out.append(
                normalize_issue_output(
                    parsed,
                    brand_fallback="",
                    sentiment_fallback="",
                    prelim_minors=None,
                )
            )
        return out

    def refine_batch(
        self,
        texts: list[str],
        prelim_dicts: list[dict[str, bool]],
        brands: list[str],
        sents: list[str],
        debug: bool = False,
    ) -> list[dict]:
        # Legacy compatibility method: behaves identical to refine_batch
        return self.classify_batch(texts, debug=debug)

    def classify_one(self, text: str, debug: bool = False) -> dict:
        return self.classify_batch([text], debug=debug)[0]
