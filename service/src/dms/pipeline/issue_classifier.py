"""LLM refiner for notebook-style preliminary issue labels."""

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
NULL_BRAND_ALIASES = {"", "rong", "ro ng", "none", "null", "n a", "na", "khong", "kh ong", "empty"}

ISSUE_RULES = {
    "C1": "Không bịa brand, sản phẩm, hoặc nhãn ngoài danh sách hợp lệ",
    "C2": "Nếu brand là hãng khác Rạng Đông thì chỉ được giữ 4 nhãn cạnh tranh",
    "C3": "Nếu brand là Rạng Đông hoặc không có brand rõ ràng thì phải tắt 4 nhãn cạnh tranh",
    "C4": "Website chỉ khi có bằng chứng rõ về web, app, portal, login, chậm, lỗi chức năng",
    "C5": "Tin trung lập chỉ khi câu trung tính, không khen, không chê, không đề nghị",
    "C6": "Nếu vừa có tín hiệu tích cực vừa tiêu cực thì sentiment phải là Tiêu cực",
    "C7": "Mỗi item xử lý độc lập, không suy luận chéo sang item khác",
}
ISSUE_RULES_JSON = json.dumps(ISSUE_RULES, ensure_ascii=False, indent=2)
MINOR_ORDER_JSON = json.dumps(MINOR_ORDER, ensure_ascii=False)

LABEL_GUIDE = dedent(
    """
    MAJOR -> MINOR -> MÔ TẢ:

    1) Sản phẩm
    - Báo lỗi: lỗi, hỏng, không hoạt động, sai chức năng.
    - Báo CL tốt: khen chất lượng, độ bền, độ ổn định.
    - Y/c cải tiến: đề nghị cải tiến thiết kế, tính năng, thông số.
    - Đề xuất SPM: đề nghị có thêm sản phẩm hoặc phiên bản mới.

    2) Yêu cầu công cụ BH
    - Bảng giá, Catalogue: xin, báo, hoặc thiếu bảng giá, báo giá, catalogue.
    - Bảng biển: xin hoặc đề cập biển bảng, bảng hiệu, POSM dạng biển.
    - Kệ bóng, thử đèn,…: xin kệ trưng bày, bộ test, dụng cụ demo.
    - Khác: công cụ bán hàng khác ngoài các mục trên.

    3) Giá, cơ chế RD
    - Tốt/ ko tốt: đánh giá giá bán, độ cạnh tranh, dễ bán hay khó bán của RD.
    - Trả thưởng: chiết khấu, thưởng, quà, cơ chế trả thưởng của RD.
    - Đề xuất: đề xuất điều chỉnh giá, chiết khấu, CTKM, cơ chế của RD.

    4) Dịch vụ
    - Bảo hành: bảo hành, đổi trả, sửa chữa, hậu mãi.
    - HTPP: nhà phân phối, đại lý, kênh bán hàng, hệ thống phân phối.
    - Hàng hoá: tồn kho, giao hàng, thiếu hàng, hư hỏng vận chuyển, đóng gói.

    5) Hàng giả
    - Hàng giả: nghi ngờ hoặc phản ánh hàng giả, hàng nhái.

    6) Website
    - Website: vấn đề web/app/portal/login/chậm/lỗi chức năng.

    7) Đối thủ cạnh tranh
    - Hãng: có nhắc hãng khác Rạng Đông.
    - Hoạt động: hoạt động marketing, trưng bày, event, roadshow của đối thủ.
    - CTKM, giá, cơ chế: giá, khuyến mãi, cơ chế của đối thủ.
    - TT SP: thông tin, mẫu mã, tính năng, thông số sản phẩm của đối thủ.

    8) Tin trung lập
    - Tin trung lập: nội dung thông tin trung tính, không khen chê, không đề xuất.
    """
).strip()

REFINER_JSON_SCHEMA = dedent(
    """
    {
      "final_minors": ["<các minor cuối>"],
      "sentiment": "Tích cực" | "Tiêu cực" | "",
      "brand": "<giữ brand_prelim hoặc ''>",
      "decision_log": [
        {"minor": "nhãn", "action": "ADD|KEEP|REMOVE", "why": "lý do ngắn gọn"}
      ]
    }
    """
).strip()

REFINER_PROMPT_HDR = dedent(
    f"""
    Bạn là bộ soát và sửa nhãn issue. Mỗi item xử lý độc lập gồm:
    - text: câu gốc
    - prelim_minors: nhãn sơ bộ có thể thiếu hoặc thừa
    - brand_prelim: brand phát hiện, có thể rỗng
    - sent_prelim: "Tích cực" | "Tiêu cực" | ""

    Nhiệm vụ bắt buộc:
    1) Sửa nhãn: bỏ nhãn sai, thêm nhãn còn thiếu nếu có bằng chứng chắc chắn.
    2) Trả về decision_log ngắn gọn cho mỗi nhãn được thêm, giữ, hoặc bỏ.
    3) Giữ brand đúng bằng brand_prelim, không bịa brand mới.
    4) Chuẩn hoá sentiment theo C6.
    5) Không tạo nhãn ngoài danh sách hợp lệ, không suy luận chéo item.

    QUY TẮC:
    {ISSUE_RULES_JSON}

    LABEL_GUIDE:
    {LABEL_GUIDE}

    NHÃN HỢP LỆ:
    {MINOR_ORDER_JSON}

    ĐẦU RA MỖI ITEM:
    {REFINER_JSON_SCHEMA}

    KHÔNG thêm text ngoài JSON. Mỗi item đúng 1 dòng JSON.
    """
).strip()


def _prelim_true_minors(prelim: dict[str, bool]) -> list[str]:
    return [minor for minor, enabled in prelim.items() if enabled and minor in MINOR_ORDER]


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


def _normalize_decision_log(log):
    norm = []
    for item in log or []:
        if isinstance(item, dict):
            minor = (item.get("minor") or "").strip()
            action = (item.get("action") or "").strip().upper()
            why = (item.get("why") or "").strip()
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
    """Normalize raw LLM output while preserving notebook business rules."""
    finals = [minor for minor in (parsed.get("final_minors") or []) if minor in MINOR_ORDER]
    sentiment = (parsed.get("sentiment") or sentiment_fallback or "").strip()
    if sentiment not in ("Tích cực", "Tiêu cực", ""):
        sentiment = ""

    brand_raw = (parsed.get("brand") or brand_fallback or "").strip()
    brand_can = canon(brand_raw)
    if brand_can in NULL_BRAND_ALIASES:
        brand_raw = ""
        brand_can = ""
    is_competitor = bool(brand_can and brand_can not in RD_BRAND_ALIASES)

    if is_competitor:
        finals = [minor for minor in finals if minor in COMP_ALLOWED]
        if "Hãng" not in finals:
            finals = ["Hãng"] + finals
        brand_out = brand_raw
    else:
        finals = [minor for minor in finals if minor not in COMP_ALLOWED]
        brand_out = ""

    if prelim_minors is not None and not finals:
        finals = [minor for minor in prelim_minors if minor in MINOR_ORDER]

    return {
        "final_minors": finals,
        "sentiment": sentiment,
        "brand": brand_out,
        "decision_log": _normalize_decision_log(parsed.get("decision_log")),
    }


class IssueClassifier:
    """Refine preliminary issue labels using Gemini with notebook guardrails."""

    def __init__(self, gemini: GeminiClient, settings: Settings) -> None:
        self.gemini = gemini
        self.settings = settings

    @staticmethod
    def _build_refiner_items(
        texts: list[str],
        prelim_minors_list: list[list[str]],
        brands: list[str],
        sents: list[str],
    ) -> str:
        return "\n".join(
            json.dumps(
                {
                    "text": text,
                    "prelim_minors": prelim_minors,
                    "brand_prelim": brand or "",
                    "sent_prelim": sent or "",
                },
                ensure_ascii=False,
            )
            for text, prelim_minors, brand, sent in zip(texts, prelim_minors_list, brands, sents, strict=False)
        )

    def _llm_json_call_refiner(self, prompt: str) -> str:
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
                        logger.error("Issue refiner fail (attempt %d): %s", attempt, last_err)
                        return ""
                    time.sleep(self.settings.base_wait * attempt)
        return ""

    def refine_batch(
        self,
        texts: list[str],
        prelim_dicts: list[dict[str, bool]],
        brands: list[str],
        sents: list[str],
        debug: bool = False,
    ) -> list[dict]:
        prelim_minors_list = [_prelim_true_minors(prelim) for prelim in prelim_dicts]
        payload = self._build_refiner_items(texts, prelim_minors_list, brands, sents)
        prompt = (
            f"{REFINER_PROMPT_HDR}\n\n"
            f"DỮ LIỆU (mỗi dòng là 1 JSON):\n{payload}\n\n"
            f"Trả về đúng {len(texts)} dòng JSON theo thứ tự."
        )
        raw = self._llm_json_call_refiner(prompt)
        if debug:
            preview = raw[:800] + ("..." if len(raw) > 800 else "")
            logger.debug("RAW issue refiner: %s", preview or "∅")

        arr = _extract_json_anywhere(raw, expected_n=len(texts))
        if not isinstance(arr, list) or len(arr) == 0:
            arr = [
                {
                    "final_minors": prelim_minors_list[idx],
                    "sentiment": sents[idx] or "",
                    "brand": brands[idx] or "",
                    "decision_log": [{"minor": "__ALL__", "action": "KEEP", "why": "FALLBACK_PRELIM"}],
                }
                for idx in range(len(texts))
            ]

        out = []
        for idx in range(len(texts)):
            parsed = arr[idx] if idx < len(arr) and isinstance(arr[idx], dict) else {}
            out.append(
                normalize_issue_output(
                    parsed,
                    brand_fallback=brands[idx] or "",
                    sentiment_fallback=sents[idx] or "",
                    prelim_minors=prelim_minors_list[idx],
                )
            )
        return out

    def classify_batch(self, texts: list[str], debug: bool = False) -> list[dict]:
        empty_prelim = [{minor: False for minor in MINOR_ORDER} for _ in texts]
        return self.refine_batch(texts, empty_prelim, [""] * len(texts), [""] * len(texts), debug=debug)

    def classify_one(self, text: str, debug: bool = False) -> dict:
        return self.classify_batch([text], debug=debug)[0]
