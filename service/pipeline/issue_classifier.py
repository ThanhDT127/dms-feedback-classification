"""
Issue classification module — LLM-based direct classification with guardrails.

Ported from notebook Cell 10 (LLM issue classifier: direct classification + guardrails).
"""
import re
import json
import time
from textwrap import dedent
from unidecode import unidecode

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    MAX_RETRY,
    BASE_WAIT,
    logger,
)
from gemini_client import generate_json, generate


# ── Shared helpers ───────────────────────────────────────────────────────────
def canon(s: str) -> str:
    """Normalize string for brand comparison."""
    if not s:
        return ""
    s2 = unidecode(str(s)).lower()
    s2 = re.sub(r"[^a-z0-9\s]+", " ", s2)
    return re.sub(r"\s+", " ", s2).strip()


# ── Issue taxonomy ───────────────────────────────────────────────────────────
MINOR_ORDER = [
    "Báo lỗi", "Báo CL tốt", "Y/c cải tiến", "Đề xuất SPM",
    "Bảng giá, Catalogue", "Bảng biển", "Kệ bóng, thử đèn,…", "Khác",
    "Tốt/ ko tốt", "Trả thưởng", "Đề xuất",
    "Bảo hành", "HTPP", "Hàng hoá",
    "Hàng giả", "Website",
    "Hãng", "Hoạt động", "CTKM, giá, cơ chế", "TT SP",
    "Tin trung lập"
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
    "rang dong", "r d", "rd", "rangdong", "rang dong jsc",
    "rang dong company", "ra ng dong"
}

NULL_BRAND_ALIASES = {
    "", "rong", "ro ng", "none", "null", "n a", "na",
    "khong", "kh ong", "empty"
}


# ── Issue classification rules & prompts ────────────────────────────────────
ISSUE_RULES = {
    "C1": "Không bịa brand, sản phẩm, hoặc nhãn ngoài danh sách hợp lệ",
    "C2": "Nếu brand là hãng khác Rạng Đông thì chỉ được giữ 4 nhãn cạnh tranh",
    "C3": "Nếu brand là Rạng Đông hoặc không có brand rõ ràng thì phải tắt 4 nhãn cạnh tranh",
    "C4": "Website chỉ khi có bằng chứng rõ về web, app, portal, login, chấm, lỗi chức năng",
    "C5": "Tin trung lập chỉ khi câu trung tính, không khen, không chê, không đề nghị",
    "C6": "Nếu vừa có tín hiệu tích cực vừa tiêu cực thì sentiment phải là Tiêu cực",
    "C7": "Mỗi item xử lý độc lập, không suy luận chéo sang item khác",
}
ISSUE_RULES_JSON = json.dumps(ISSUE_RULES, ensure_ascii=False, indent=2)
MINOR_ORDER_JSON = json.dumps(MINOR_ORDER, ensure_ascii=False)

LABEL_GUIDE = dedent("""
MAJOR → MINOR → MÔ TẢ:

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
- Website: vấn đề web/app/portal/login/chấm/lỗi chức năng.

7) Đối thủ cạnh tranh
- Hãng: có nhắc hãng khác Rạng Đông.
- Hoạt động: hoạt động marketing, trưng bày, event, roadshow của đối thủ.
- CTKM, giá, cơ chế: giá, khuyến mãi, cơ chế của đối thủ.
- TT SP: thông tin, mẫu mã, tính năng, thông số sản phẩm của đối thủ.

8) Tin trung lập
- Tin trung lập: nội dung thông tin trung tính, không khen chê, không đề xuất.
""").strip()

ISSUE_JSON_SCHEMA = dedent("""
{
  "final_minors": ["<các minor cuối cùng>"],
  "sentiment": "Tích cực" | "Tiêu cực" | "",
  "brand": "<hãng đối thủ nếu có, ngược lại để rỗng>",
  "decision_log": [
    {"minor": "nhãn", "action": "ADD|KEEP|REMOVE", "why": "lý do ngắn"}
  ]
}
""").strip()

ISSUE_PROMPT_HDR = dedent(f"""
Bạn là bộ phân loại phản hồi marketing. Với mỗi item, hãy phân loại trực tiếp issue labels cuối cùng.

Đầu vào mỗi item chỉ có:
- text: câu phản hồi gốc

Nhiệm vụ bắt buộc:
1) Chọn final_minors từ đúng danh sách nhãn hợp lệ.
2) Chọn sentiment là "Tích cực", "Tiêu cực", hoặc rỗng.
3) Chỉ điền brand khi câu nói rõ về hãng cạnh tranh khác Rạng Đông.
4) Không bịa nhãn, không bịa brand, không suy luận vượt quá bằng chứng trong câu.
5) Trả về decision_log ngắn gọn cho các nhãn được thêm, giữ, hoặc loại.

QUY TẮC:
{ISSUE_RULES_JSON}

LABEL_GUIDE:
{LABEL_GUIDE}

NHÃN HỢP LỆ:
{MINOR_ORDER_JSON}

ĐẦU RA MỖI ITEM:
{ISSUE_JSON_SCHEMA}

KHÔNG thêm text ngoài JSON. Mỗi item đúng 1 dòng JSON theo đúng thứ tự input.
""").strip()


# ── JSON parsing helpers ────────────────────────────────────────────────────
def _inside_code_fence(s: str) -> str:
    m = re.search(r"```(?:json)?\s*(.*?)```", s, flags=re.S | re.I)
    return m.group(1).strip() if m else s


def _wrap_objects_to_array(s: str) -> str:
    s2 = re.sub(r"}\s*{", "},{", s.strip())
    if not s2.startswith("["):
        s2 = "[" + s2
    if not s2.endswith("]"):
        s2 = s2 + "]"
    return s2


def _safe_json_loads(s: str):
    try:
        return json.loads(s)
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
    for ln in [ln for ln in text.splitlines() if ln.strip()]:
        m = re.search(r"\{.*\}", ln.strip())
        if not m:
            continue
        one = _safe_json_loads(m.group(0))
        if isinstance(one, dict):
            out.append(one)
    return out or None


# ── Normalization ────────────────────────────────────────────────────────────
def _normalize_decision_log(log):
    norm = []
    for it in (log or []):
        if isinstance(it, dict):
            minor = (it.get("minor") or "").strip()
            action = (it.get("action") or "").strip().upper()
            why = (it.get("why") or "").strip()
            if action not in ("ADD", "KEEP", "REMOVE"):
                action = "KEEP" if minor else ""
            if minor or why or action:
                norm.append({"minor": minor, "action": action, "why": why})
        elif isinstance(it, str):
            norm.append({"minor": "", "action": "", "why": it.strip()})
    return norm


def _normalize_issue_output(parsed: dict) -> dict:
    """Apply guardrails and normalization to LLM output."""
    finals = [m for m in (parsed.get("final_minors") or []) if m in MINOR_ORDER]
    sent = (parsed.get("sentiment") or "").strip()
    if sent not in ("Tích cực", "Tiêu cực", ""):
        sent = ""

    brand_raw = (parsed.get("brand") or "").strip()
    brand_can = canon(brand_raw)
    if brand_can in NULL_BRAND_ALIASES:
        brand_raw = ""
        brand_can = ""
    is_competitor = bool(brand_can and brand_can not in RD_BRAND_ALIASES)

    if is_competitor:
        finals = [m for m in finals if m in COMP_ALLOWED]
        if "Hãng" not in finals:
            finals = ["Hãng"] + finals
        brand_out = brand_raw
    else:
        finals = [m for m in finals if m not in COMP_ALLOWED]
        brand_out = ""

    return {
        "final_minors": finals,
        "sentiment": sent,
        "brand": brand_out,
        "decision_log": _normalize_decision_log(parsed.get("decision_log")),
    }


# ── LLM calls ───────────────────────────────────────────────────────────────
def _build_issue_items(texts: list[str]) -> str:
    return "\n".join(
        json.dumps({"text": t}, ensure_ascii=False)
        for t in texts
    )


def _llm_json_call_issue(prompt: str, max_retry: int = 3, wait: float = 4.0) -> str:
    last_err = None
    for attempt in range(1, max_retry + 1):
        try:
            return generate_json(prompt, temperature=0.0)
        except Exception as e:
            last_err = e
            try:
                return generate(prompt, temperature=0.0)
            except Exception as e2:
                last_err = e2
                if attempt == max_retry:
                    logger.error("Issue classifier fail (attempt %d): %s", attempt, last_err)
                    return ""
                time.sleep(wait * attempt)


def llm_issue_classify_batch(texts: list[str], debug: bool = False) -> list[dict]:
    """
    Classify a batch of texts into issue labels using LLM.

    Returns list of dicts with keys:
        final_minors, sentiment, brand, decision_log
    """
    payload = _build_issue_items(texts)
    prompt = (
        f"{ISSUE_PROMPT_HDR}\n\n"
        f"DỮ LIỆU (mỗi dòng là 1 JSON):\n{payload}\n\n"
        f"Trả về đúng {len(texts)} dòng JSON theo thứ tự."
    )
    raw = _llm_json_call_issue(prompt, max_retry=MAX_RETRY, wait=BASE_WAIT)
    if debug:
        logger.debug("RAW from issue classifier (<=800 chars): %s", (raw[:800] + ("..." if len(raw) > 800 else "")) or "∅")

    arr = _extract_json_anywhere(raw, expected_n=len(texts))
    if not isinstance(arr, list) or len(arr) == 0:
        arr = [{} for _ in texts]

    out = []
    for i in range(len(texts)):
        parsed = arr[i] if i < len(arr) and isinstance(arr[i], dict) else {}
        item = _normalize_issue_output(parsed)
        out.append(item)
    return out


def llm_issue_classify_one(text: str, debug: bool = False) -> dict:
    """Classify a single text."""
    return llm_issue_classify_batch([text], debug=debug)[0]
