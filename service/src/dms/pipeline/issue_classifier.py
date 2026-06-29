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
    "Báo lỗi": "Sản phẩm vật lý bị lỗi kỹ thuật, hỏng, cháy, không sáng, không hoạt động, lệch ren, rò điện, nứt vỡ thực tế. KHÔNG dùng cho phàn nàn về thiết kế, kích thước, độ dày/mỏng vỏ nhựa/thanh đồng, phích to vướng (các phàn nàn thiết kế này thuộc Y/c cải tiến).",
    "Báo CL tốt": "Khen chất lượng sản phẩm tốt, bền, sáng tốt, ổn định, khách hài lòng.",
    "Y/c cải tiến": "Yêu cầu chỉnh sửa hoặc phàn nàn về thiết kế, kích thước, tính năng, độ dày/mỏng của vỏ nhựa/thanh đồng, bao bì, mẫu mã, phích to vướng, kết cấu của sản phẩm HIỆN CÓ đang bán (như vỏ mỏng cần làm dày hơn, phích to cần làm nhỏ lại, thanh đồng mỏng cần làm dày).",
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

    # Apply competitor boundaries
    if is_competitor:
        # If competitor, we ALLOW both competitor and RD labels (no hard filter).
        # We ensure "Hãng" is in active_labels if any competitor label is active or brand is set.
        has_comp_label = any(label in COMP_ALLOWED for label in active_labels)
        if (has_comp_label or brand_out) and "Hãng" not in active_labels:
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
            prompt_rows.append(
                {
                    "row_index": idx,
                    "text": text,
                    "matched_product": {
                        "model": prod.get("Model") or prod.get("model") or "",
                        "dong_sp": prod.get("Dòng SP") or prod.get("dong_sp") or "",
                        "san_pham": prod.get("Sản phẩm") or prod.get("san_pham") or "",
                    },
                }
            )

        minor_order_json = json.dumps(MINOR_ORDER, ensure_ascii=False)
        label_defs = json.dumps(LABEL_DEFINITIONS, ensure_ascii=False, indent=2)
        hints_json = json.dumps(keyword_hints(kw_map), ensure_ascii=False, indent=2)
        brand_json = json.dumps(brand_hints(kw_map), ensure_ascii=False, indent=2)
        input_json = json.dumps(prompt_rows, ensure_ascii=False, indent=2)

        prompt_override = self.settings.keyword_dir / "system_prompt.txt"
        loaded_override = False
        prompt = ""
        if prompt_override.is_file():
            try:
                template = prompt_override.read_text(encoding="utf-8")
                prompt = (
                    template.replace("{minor_order_json}", minor_order_json)
                    .replace("{label_defs}", label_defs)
                    .replace("{hints_json}", hints_json)
                    .replace("{brand_json}", brand_json)
                    .replace("{input_json}", input_json)
                ).strip()
                loaded_override = True
            except Exception as exc:
                logger.error("Failed to load prompt override; using default: %s", exc)

        if not loaded_override:
            prompt = dedent(
                f"""
            Bạn là hệ thống phân loại phản hồi bán hàng và marketing cho ngành chiếu sáng và thiết bị điện Rạng Đông.

            MỤC ĐÍCH
            Mỗi dòng trong dữ liệu là một phản hồi thực tế từ thị trường, khách hàng, đại lý hoặc nhân viên bán hàng.
            Nhiệm vụ của bạn là đọc phản hồi, phân tích thương hiệu (brand), cảm xúc (sentiment), các nhãn phân loại phù hợp, và ghi lại giải thích ngắn gọn.

            QUY TRÌNH SUY LUẬN CƯỠNG BỨC (CHAIN-OF-THOUGHT)
            Để đảm bảo độ chính xác tuyệt đối, bạn phải suy luận theo trình tự sau và ghi vào JSON đầu ra:
            1. Phân tích Thương hiệu & Đối thủ: Rà soát xem câu có nhắc tới thương hiệu đối thủ nào (Asia, Sopoka, Philips, v.v.) không. Ghi tên thương hiệu vào "brand".
            2. Phân tích Cảm xúc (Sentiment): Đánh giá thái độ người viết ("Tích cực", "Tiêu cực", hoặc để trống "" cho trung lập/đóng góp xây dựng).
            3. Phân tích Lập luận Từng Nhãn (decision_log): Với mỗi nhãn bạn dự kiến gán (ADD), bạn phải ghi rõ minh chứng ("evidence") trích xuất trực tiếp từ câu phản hồi và lý do gán nhãn ngắn gọn ("reason").
            4. Quyết định Nhãn Phân Loại (labels): Dựa hoàn toàn trên các lập luận ở bước 3 để điền true/false cho từng nhãn trong danh sách 21 nhãn. Quyết định của nhãn phải nhất quán 100% với phần lập luận trước đó.

            RANH GIỚI NGỮ NGHĨA VÀ LUẬT LOẠI TRỪ (SEMANTIC BOUNDARIES)
            1. "Báo lỗi" VS "Y/c cải tiến":
               - "Báo lỗi": Chỉ gán khi có lỗi kỹ thuật vật lý thực tế, hỏng hóc, cháy nổ, không hoạt động, lệch ren, rò điện, nứt vỡ.
               - "Y/c cải tiến": Gán cho yêu cầu chỉnh sửa, phàn nàn thiết kế, kích thước, độ dày vỏ nhựa/thanh đồng, bao bì, mẫu mã, phích to vướng của sản phẩm HIỆN CÓ (Ví dụ: "ổ chịu tải vỏ hơi mềm" -> gán Y/c cải tiến, KHÔNG gán Báo lỗi).
            2. "Báo lỗi" VS "Bảo hành":
               - "Báo lỗi": Nói về lỗi hỏng hóc vật lý của sản phẩm.
               - "Bảo hành": Nói về QUY TRÌNH bảo hành, thời gian BH lâu, đổi trả chậm — tức là chất lượng DỊCH VỤ.
               - Nếu vừa nhắc SP hỏng vừa phàn nàn đổi trả bảo hành chậm -> Gán CẢ HAI nhãn.
            3. "HTPP" VS "Hàng hoá":
               - "HTPP": Vấn đề kênh phân phối: tranh chấp C1/C2 phá giá, lấn vùng, tràn vùng bán hàng.
               - "Hàng hoá": Vấn đề logistics: giao hàng chậm, thiếu hàng, tồn kho, đóng gói vận chuyển.
            4. "Đề xuất" VS "Trả thưởng":
               - "Trả thưởng": Hỏi/phàn nàn cụ thể về tiền thưởng, gói quay số, chương trình C2TD, nợ thưởng.
               - "Đề xuất": Đề nghị thay đổi cơ chế chính sách giá/chiết khấu/khuyến mãi CHUNG của Rạng Đông.
            5. "Đề xuất SPM" VS "Y/c cải tiến":
               - "Đề xuất SPM": Yêu cầu sản xuất dòng sản phẩm MỚI chưa từng có ("ra thêm", "sản xuất thêm", "thêm mã mới").
               - "Y/c cải tiến": Góp ý chỉnh sửa chi tiết của sản phẩm HIỆN CÓ đang bán.
            6. "Hãng" (Nhãn đối thủ): Chỉ gán nhãn đối thủ ("Hãng", "Hoạt động", "CTKM, giá, cơ chế", "TT SP") khi "brand" là tên hãng đối thủ cạnh tranh. Nếu ý chính là góp ý Rạng Đông và đối thủ chỉ là ví dụ tham khảo (Ví dụ: "làm màu cam giống Sopoka") -> Gán Y/c cải tiến, brand để trống và KHÔNG gán nhãn đối thủ.

            TỪ ĐIỂN TỪ VIẾT TẮT & SAO CHÍNH TẢ (SPELL GUARD GLOSSARY)
            Đọc cả câu để hiểu nghĩa của các từ viết tắt và từ sai chính tả sau, tuyệt đối KHÔNG bắt nhầm từ khóa đơn lẻ:
            - "tin thưởng" hoặc "tin thưởng" thực chất là viết sai chính tả của "tin tưởng" (trust/believe) -> Tuyệt đối KHÔNG gán nhãn "Trả thưởng".
            - "bh" -> viết tắt của "bảo hành".
            - "sp" -> viết tắt của "sản phẩm".
            - "km" -> viết tắt của "khuyến mại/khuyến mãi".
            - "npp" -> viết tắt của "nhà phân phối".
            - "đl" -> viết tắt của "đại lý".
            - "c1", "c2" -> viết tắt của đại lý/nhà phân phối "cấp 1", "cấp 2" -> Thuộc nhãn HTPP.
            - "bgn" -> viết tắt của đèn "bán nguyệt".
            - "at", "attomat", "atomat" -> viết tắt của thiết bị đóng cắt "aptomat".
            - "ch" -> viết tắt của "cửa hàng".

            DANH SÁCH NHÃN HỢP LỆ THEO THỨ TỰ:
            {minor_order_json}

            ĐỊNH NGHĨA CHI TIẾT CÁC NHÃN:
            {label_defs}

            KEYWORD HINTS (Chỉ là gợi ý, bắt buộc phải đọc cả câu để hiểu ngữ cảnh):
            {hints_json}

            BRAND HINTS (Gợi ý nhận diện thương hiệu đối thủ):
            {brand_json}

            VÍ DỤ SUY LUẬN MẪU (FEW-SHOT EXAMPLES)

            Ví dụ 1:
            Input: "led Bun trụ 10w Asia giá rẻ 12k hợp lý dễ bán. Rạng Đông cao hơn gấp 3 lần."
            Output:
            {{
              "row_index": 0,
              "brand": "Asia",
              "is_competitor": true,
              "sentiment": "Tiêu cực",
              "decision_log": [
                {{
                  "label": "Hãng",
                  "action": "ADD",
                  "evidence": "led Bun trụ 10w Asia",
                  "reason": "Nhắc tới thương hiệu đối thủ cạnh tranh Asia"
                }},
                {{
                  "label": "CTKM, giá, cơ chế",
                  "action": "ADD",
                  "evidence": "giá rẻ 12k hợp lý dễ bán",
                  "reason": "Đề cập đến chính sách giá bán của đối thủ cạnh tranh"
                }},
                {{
                  "label": "Tốt/ ko tốt",
                  "action": "ADD",
                  "evidence": "Rạng Đông cao hơn gấp 3 lần",
                  "reason": "Nhận xét so sánh giá bán của Rạng Đông cao, đắt hơn đối thủ"
                }}
              ],
              "labels": {{
                "Báo lỗi": false,
                "Báo CL tốt": false,
                "Y/c cải tiến": false,
                "Đề xuất SPM": false,
                "Bảng giá, Catalogue": false,
                "Bảng biển": false,
                "Kệ bóng, thử đèn,…": false,
                "Khác": false,
                "Tốt/ ko tốt": true,
                "Trả thưởng": false,
                "Đề xuất": false,
                "Bảo hành": false,
                "HTPP": false,
                "Hàng hoá": false,
                "Hàng giả": false,
                "Website": false,
                "Hãng": true,
                "Hoạt động": false,
                "CTKM, giá, cơ chế": true,
                "TT SP": false,
                "Tin trung lập": false
              }}
            }}

            Ví dụ 2:
            Input: "ổ chịu tải mới ra được 1 loại 3c Cty lên ra đủ các mã để phục vụ hết nhu cầu của người dùng, phích chịu tại lên cải tiến dẹt cắm đỡ tốn diện tích"
            Output:
            {{
              "row_index": 1,
              "brand": "",
              "is_competitor": false,
              "sentiment": "",
              "decision_log": [
                {{
                  "label": "Đề xuất SPM",
                  "action": "ADD",
                  "evidence": "Cty lên ra đủ các mã để phục vụ hết nhu cầu",
                  "reason": "Đề xuất sản xuất thêm các mã ổ cắm chịu tải mới chưa có"
                }},
                {{
                  "label": "Y/c cải tiến",
                  "action": "ADD",
                  "evidence": "phích chịu tại lên cải tiến dẹt cắm đỡ tốn diện tích",
                  "reason": "Góp ý cải tiến thiết kế dẹt cho phích cắm chịu tải hiện có"
                }}
              ],
              "labels": {{
                "Báo lỗi": false,
                "Báo CL tốt": false,
                "Y/c cải tiến": true,
                "Đề xuất SPM": true,
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
              }}
            }}

            Ví dụ 3:
            Input: "aptomat Rạng Đông mới được 1 năm thợ vẫn chưa tin thưởng"
            Output:
            {{
              "row_index": 2,
              "brand": "",
              "is_competitor": false,
              "sentiment": "Tiêu cực",
              "decision_log": [
                {{
                  "label": "Tin trung lập",
                  "action": "ADD",
                  "evidence": "chưa tin thưởng",
                  "reason": "Từ 'tin thưởng' là viết sai chính tả của 'tin tưởng' (trust). Phản hồi thể hiện sự thiếu tin tưởng của thợ, không phàn nàn lỗi kỹ thuật cụ thể của SP nên gán nhãn Tin trung lập và loại trừ Trả thưởng."
                }}
              ],
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
                "Tin trung lập": true
              }}
            }}

            Ví dụ 4:
            Input: "Khách phản hồi vbm02 vặn dễ bị lệch ren, cty đổi trả BH hơi lâu"
            Output:
            {{
              "row_index": 3,
              "brand": "",
              "is_competitor": false,
              "sentiment": "Tiêu cực",
              "decision_log": [
                {{
                  "label": "Báo lỗi",
                  "action": "ADD",
                  "evidence": "vbm02 vặn dễ bị lệch ren",
                  "reason": "Phản ánh lỗi vật lý kỹ thuật lệch ren của sản phẩm VBM02"
                }},
                {{
                  "label": "Bảo hành",
                  "action": "ADD",
                  "evidence": "cty đổi trả BH hơi lâu",
                  "reason": "Phàn nàn về quy trình và thời gian dịch vụ bảo hành đổi trả lâu"
                }}
              ],
              "labels": {{
                "Báo lỗi": true,
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
                "Bảo hành": true,
                "HTPP": false,
                "Hàng hoá": false,
                "Hàng giả": false,
                "Website": false,
                "Hãng": false,
                "Hoạt động": false,
                "CTKM, giá, cơ chế": false,
                "TT SP": false,
                "Tin trung lập": false
              }}
            }}

            ĐẦU RA BẮT BUỘC:
            Trả về DUY NHẤT một JSON array hợp lệ. Không viết thêm bất kỳ từ ngữ nào ngoài JSON. Không dùng markdown code block (không có ```json ... ```).
            Số phần tử đầu ra phải bằng đúng số phần tử đầu vào. Giữ nguyên thứ tự và thuộc tính row_index.

            Mỗi phần tử output phải tuân theo cấu trúc sau (lập luận suy luận trước, quyết định nhãn cuối cùng):
            {{
              "row_index": 0,
              "brand": "Tên thương hiệu đối thủ (hoặc để trống '' nếu là Rạng Đông / không có)",
              "is_competitor": false,
              "sentiment": "Tích cực" | "Tiêu cực" | "",
              "decision_log": [
                {{
                  "label": "Tên nhãn",
                  "action": "ADD | KEEP | REMOVE",
                  "evidence": "Cụm từ gốc làm minh chứng",
                  "reason": "Lý do gán nhãn ngắn gọn"
                }}
              ],
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
              }}
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
            arr = [
                {
                    "labels": {},
                    "sentiment": "",
                    "brand": "",
                    "decision_log": [{"reason": "FALLBACK_PARSING"}],
                }
                for _ in texts
            ]

        out = []
        for idx in range(len(texts)):
            parsed = arr[idx] if idx < len(arr) and isinstance(arr[idx], dict) else {}
            # Reconstruct index if mismatch
            parsed_idx = parsed.get("row_index")
            if parsed_idx is not None and parsed_idx != idx:
                # Find matching row index if scrambled
                matched_item = next(
                    (
                        item
                        for item in arr
                        if isinstance(item, dict) and item.get("row_index") == idx
                    ),
                    None,
                )
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
