"""Pure-LLM issue classifier with dynamic keyword/brand hints and post-validation."""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Callable

from unidecode import unidecode

from ..exceptions import PipelineCancelled
from ..gemini_client import GeminiClient
from ..prompt_renderer import render_issue_classifier_prompt
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
    "Website": (
        "Lỗi PHẦN MỀM thuần túy: lỗi trang web bán hàng/portal đại lý (DMS, hệ thống đặt hàng), "
        "app bị crash/đơ/không mở được, lỗi đăng nhập portal, lỗi hiển thị báo cáo trên hệ thống, "
        "hệ thống web xử lý chậm/timeout, lỗi giao diện phần mềm quản lý. "
        "Từ khóa đặc trưng: 'web bị lỗi', 'portal không vào được', 'app crash', 'DMS lỗi', "
        "'đăng nhập không được', 'hệ thống đơ', 'trang web chậm'. "
        "KHÔNG dùng cho: (1) lỗi SP vật lý hỏng hóc — đó là Báo lỗi; "
        "(2) HC (Home Controller/bộ điều khiển thông minh) KHÔNG KẾT NỐI được thiết bị vật lý qua wifi — "
        "nếu nguyên nhân là phần cứng/firmware thiết bị thì là Báo lỗi; "
        "nếu lỗi tính năng phần mềm của app HC (UI sai, app không nhận lệnh, cập nhật app lỗi) thì mới là Website. "
        "Ranh giới HC: 'HC không tìm thấy thiết bị' → Báo lỗi (thiết bị ko phát hiện được); "
        "'app HC hiển thị sai trạng thái' → Website (lỗi hiển thị phần mềm); "
        "'mất kết nối sau cập nhật firmware' → Báo lỗi (firmware gây mất kết nối phần cứng)."
    ),
    "Hãng": "Có nhắc đến hãng khác ngoài Rạng Đông (đối thủ cạnh tranh). Ghi tên hãng vào brand.",
    "Hoạt động": "Hoạt động marketing, trưng bày, tặng kệ, event, roadshow, tài trợ CỦA ĐỐI THỦ.",
    "CTKM, giá, cơ chế": "Giá bán, khuyến mãi, chiết khấu, chính sách bán hàng CỦA ĐỐI THỦ cạnh tranh.",
    "TT SP": "Thông tin sản phẩm, mẫu mã, tính năng, thông số, catalogue CỦA ĐỐI THỦ cạnh tranh.",
    "Tin trung lập": "Câu hoàn toàn trung tính, không khen/chê/đề xuất/yêu cầu gì. CHỈ gán khi không có nhãn nào khác.",
}

_LABEL_CONFIG_LOCK = threading.RLock()


def validate_label_payload(payload: dict) -> dict:
    """Validate and normalize a complete label configuration payload."""
    if not isinstance(payload, dict):
        raise ValueError("Label payload must be an object")

    label_definitions = payload.get("label_definitions")
    minor_order = payload.get("minor_order")
    minor_to_major = payload.get("minor_to_major")
    if not isinstance(label_definitions, dict) or not isinstance(minor_order, list) or not isinstance(minor_to_major, dict):
        raise ValueError("Missing required fields: label_definitions, minor_order, minor_to_major")

    normalized_order = [str(label).strip() for label in minor_order if str(label).strip()]
    if not normalized_order:
        raise ValueError("minor_order must contain at least one label")
    if len(set(normalized_order)) != len(normalized_order):
        raise ValueError("minor_order contains duplicate labels")

    normalized_defs = {str(k).strip(): str(v) for k, v in label_definitions.items() if str(k).strip()}
    normalized_mapping = {str(k).strip(): str(v) for k, v in minor_to_major.items() if str(k).strip()}
    missing_defs = [label for label in normalized_order if label not in normalized_defs]
    missing_mapping = [label for label in normalized_order if label not in normalized_mapping]
    if missing_defs:
        raise ValueError("label_definitions missing labels: " + ", ".join(missing_defs[:5]))
    if missing_mapping:
        raise ValueError("minor_to_major missing labels: " + ", ".join(missing_mapping[:5]))

    return {
        "label_definitions": {label: normalized_defs[label] for label in normalized_order},
        "minor_order": normalized_order,
        "minor_to_major": {label: normalized_mapping[label] for label in normalized_order},
    }


def get_label_config_snapshot() -> dict:
    """Return a consistent snapshot of the active label configuration."""
    with _LABEL_CONFIG_LOCK:
        return {
            "label_definitions": dict(LABEL_DEFINITIONS),
            "minor_order": list(MINOR_ORDER),
            "minor_to_major": dict(MINOR_TO_MAJOR),
        }


def publish_label_config(payload: dict) -> dict:
    """Publish a complete label configuration without exposing empty globals."""
    normalized = validate_label_payload(payload)
    with _LABEL_CONFIG_LOCK:
        MINOR_ORDER[:] = normalized["minor_order"]
        LABEL_DEFINITIONS.clear()
        LABEL_DEFINITIONS.update(normalized["label_definitions"])
        MINOR_TO_MAJOR.clear()
        MINOR_TO_MAJOR.update(normalized["minor_to_major"])
    return normalized


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
    minor_order = get_label_config_snapshot()["minor_order"]
    for label in minor_order:
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
    if isinstance(obj, dict):
        for key in ("results", "items", "data", "output"):
            value = obj.get(key)
            if isinstance(value, list):
                return value
        if expected_n == 1:
            return [obj]
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
    if not isinstance(parsed, dict):
        parsed = {
            "labels": {},
            "sentiment": "",
            "brand": "",
            "decision_log": [{"reason": "FALLBACK_MALFORMED_LLM_ITEM"}],
        }
    minor_order = get_label_config_snapshot()["minor_order"]
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
    if isinstance(labels_dict, list):
        labels_dict = {str(label): True for label in labels_dict}
    if not isinstance(labels_dict, dict):
        labels_dict = {}

    # Also handle if they returned a list final_minors instead
    final_minors = parsed.get("final_minors") or []
    if isinstance(final_minors, list):
        for lbl in final_minors:
            if lbl in minor_order:
                labels_dict[lbl] = True

    # Build active labels list
    active_labels = [
        label
        for label in minor_order
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
            active_labels = [lbl for lbl in prelim_minors if lbl in minor_order]
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
        self._last_usage: dict = {}
        self._last_prompt: dict = {}

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

    def _llm_json_call(self, prompt: str, cancellation_check: Callable[[], bool] | None = None) -> str:
        if cancellation_check is not None and cancellation_check():
            raise PipelineCancelled("Classification job was cancelled.")
        try:
            resp = self.gemini.generate_json(prompt, temperature=0.0)
            self._last_usage = resp.usage
            return resp.text
        except Exception as exc:
            if cancellation_check is not None and cancellation_check():
                raise PipelineCancelled("Classification job was cancelled.") from exc
            logger.error("Pure-LLM issue classifier fail: %s", exc)
        return ""

    def classify_batch(
        self,
        texts: list[str],
        matched_products: list[dict] | None = None,
        debug: bool = False,
        cancellation_check: Callable[[], bool] | None = None,
        _retry_depth: int = 0,
    ) -> list[dict]:
        if not texts:
            return []

        kw_map = self._load_kw_map()
        label_config = get_label_config_snapshot()
        minor_order = label_config["minor_order"]
        label_definitions = label_config["label_definitions"]

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

        minor_order_json = json.dumps(minor_order, ensure_ascii=False)
        label_defs = json.dumps(label_definitions, ensure_ascii=False, indent=2)
        hints_json = json.dumps(keyword_hints(kw_map), ensure_ascii=False, indent=2)
        brand_json = json.dumps(brand_hints(kw_map), ensure_ascii=False, indent=2)
        input_json = json.dumps(prompt_rows, ensure_ascii=False, indent=2)

        rendered_prompt = render_issue_classifier_prompt(
            self.settings,
            {
                "minor_order_json": minor_order_json,
                "label_defs": label_defs,
                "hints_json": hints_json,
                "brand_json": brand_json,
                "input_json": input_json,
            },
        )
        prompt = rendered_prompt.text
        self._last_prompt = {
            "source_path": str(rendered_prompt.source_path),
            "version": rendered_prompt.version,
            "sha256": rendered_prompt.sha256,
        }
        logger.debug(
            "Issue classifier prompt loaded: source=%s version=%s sha256=%s",
            rendered_prompt.source_path,
            rendered_prompt.version,
            rendered_prompt.sha256,
        )

        raw = self._llm_json_call(prompt, cancellation_check=cancellation_check)
        if debug:
            preview = raw[:800] + ("..." if len(raw) > 800 else "")
            logger.debug("RAW pure-LLM issue classifier response: %s", preview or "∅")

        n = len(texts)
        arr = _extract_json_anywhere(raw, expected_n=n)
        if not isinstance(arr, list):
            preview = (raw or "")[:300].replace("\n", "\\n")
            logger.warning(
                "classify_batch: unusable LLM JSON response; using safe fallback. raw_preview=%s",
                preview,
            )

        # --- Phase 1: Map parsed results to slots by row_index ---
        slots: list[dict | None] = [None] * n

        if isinstance(arr, list) and len(arr) > 0:
            # Build a lookup by row_index for scrambled responses
            by_row_index: dict[int, dict] = {}
            for item in arr:
                if isinstance(item, dict):
                    ri = item.get("row_index")
                    if isinstance(ri, int) and 0 <= ri < n:
                        by_row_index[ri] = item

            for idx in range(n):
                if idx in by_row_index:
                    slots[idx] = by_row_index[idx]
                elif idx < len(arr) and isinstance(arr[idx], dict):
                    slots[idx] = arr[idx]

        # Identify missing slot indices
        missing_indices = [i for i in range(n) if slots[i] is None]

        # --- Phase 2: Mini-batch retry for missing rows (only at depth 0) ---
        if 0 < len(missing_indices) < n and _retry_depth == 0:
            logger.info(
                "classify_batch: %d/%d rows missing after initial parse, retrying as mini-batch",
                len(missing_indices), n,
            )
            retry_texts = [texts[i] for i in missing_indices]
            retry_products = (
                [matched_products[i] for i in missing_indices]
                if matched_products
                else None
            )
            try:
                retry_results = self.classify_batch(
                    retry_texts,
                    matched_products=retry_products,
                    debug=debug,
                    cancellation_check=cancellation_check,
                    _retry_depth=_retry_depth + 1,
                )
                for j, orig_idx in enumerate(missing_indices):
                    if j < len(retry_results):
                        slots[orig_idx] = retry_results[j]
            except PipelineCancelled:
                raise
            except Exception as exc:
                logger.warning("Mini-batch retry failed: %s", exc)

        # Re-check for still-missing slots
        still_missing = [i for i in range(n) if slots[i] is None]

        # --- Phase 3: Sequential single-row retry for persistent failures (only at depth 0) ---
        if still_missing and _retry_depth == 0:
            logger.info(
                "classify_batch: %d rows still missing, retrying individually",
                len(still_missing),
            )
            for idx in still_missing:
                if cancellation_check is not None and cancellation_check():
                    raise PipelineCancelled("Classification job was cancelled.")
                try:
                    single_result = self.classify_batch(
                        [texts[idx]],
                        matched_products=[matched_products[idx]] if matched_products else None,
                        debug=debug,
                        cancellation_check=cancellation_check,
                        _retry_depth=_retry_depth + 1,
                    )
                    if single_result:
                        slots[idx] = single_result[0]
                except PipelineCancelled:
                    raise
                except Exception as exc:
                    logger.warning("Single-row retry for index %d failed: %s", idx, exc)

        # --- Phase 4: Final fallback for any remaining None slots ---
        _fallback_item = {
            "labels": {},
            "sentiment": "",
            "brand": "",
            "decision_log": [{"reason": "FALLBACK_ALL_RETRIES_EXHAUSTED"}],
        }

        out = []
        for idx in range(n):
            val = slots[idx]
            parsed: dict = val if isinstance(val, dict) else _fallback_item
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
