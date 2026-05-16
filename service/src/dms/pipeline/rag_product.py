"""RAG product matching helpers."""

from __future__ import annotations

import logging
import re
import time
from textwrap import dedent

import numpy as np
import pandas as pd
from rank_bm25 import BM25Okapi
from unidecode import unidecode

from ..gemini_client import GeminiClient
from ..settings import Settings

logger = logging.getLogger("dms-watcher")


def _canon_nodau(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", unidecode(str(s)).lower()).strip()


def _canon_l2l3_text(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").lower()).strip()


def _compile_kw_exact(kw: str):
    kw_l = _canon_l2l3_text(kw)
    if not kw_l:
        return None
    return re.compile(rf"(?<!\w){re.escape(kw_l)}(?!\w)", flags=re.UNICODE)


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", unidecode(str(s or "")).lower()).strip()


def _find_sheet(xl, name_candidates):
    sheets = {_norm(s): s for s in xl.sheet_names}
    for pat in name_candidates:
        for key, value in sheets.items():
            if re.search(pat, key):
                return value
    return None


def _find_col(cols, pats):
    cols_map = {_norm(c): c for c in cols}
    for pat in pats:
        for key, value in cols_map.items():
            if re.search(pat, key):
                return value
    return None


def _split_kw(cell):
    parts = re.split(r"[;,/\|\n]+", str(cell or ""))
    return [p.strip() for p in parts if p.strip()]


def _compile_rules_l2(df):
    if df is None or df.empty:
        return []
    ck = _find_col(df.columns, [r"keyword|tu khoa|t u khoa|kw"])
    c_line = _find_col(df.columns, [r"dong sp|d o ng.*sp|nhom sp|nho m.*sp|dòng.*sp"])
    c_prod = _find_col(df.columns, [r"san pham|s a n.*ph m|ten sp|t n.*sp|sản phẩm"])
    c_pri = _find_col(df.columns, [r"uu tien|u u.*ti n|priority|prio"])
    if not ck or not c_line or not c_prod:
        return []
    rules = []
    for _, row in df.iterrows():
        kws = _split_kw(row.get(ck, ""))
        line = str(row.get(c_line, "") or "").strip()
        prod = str(row.get(c_prod, "") or "").strip()
        pri = float(row.get(c_pri, 0) or 0)
        for kw in kws:
            patt = _compile_kw_exact(kw)
            if patt is None:
                continue
            rules.append(
                {
                    "kw": kw,
                    "kw_lower": _canon_l2l3_text(kw),
                    "patt": patt,
                    "priority": pri,
                    "dong_sp": line,
                    "san_pham": prod,
                }
            )
    rules.sort(key=lambda x: (x["priority"], len(x["kw_lower"])), reverse=True)
    return rules


def _compile_rules_l3(df):
    if df is None or df.empty:
        return []
    ck = _find_col(df.columns, [r"keyword|tu khoa|t u khoa|kw"])
    c_prod = _find_col(df.columns, [r"san pham|s a n.*ph m|ten sp|t n.*sp|sản phẩm"])
    c_pri = _find_col(df.columns, [r"uu tien|u u.*ti n|priority|prio"])
    if not ck or not c_prod:
        return []
    rules = []
    for _, row in df.iterrows():
        kws = _split_kw(row.get(ck, ""))
        prod = str(row.get(c_prod, "") or "").strip()
        pri = float(row.get(c_pri, 0) or 0)
        for kw in kws:
            patt = _compile_kw_exact(kw)
            if patt is None:
                continue
            rules.append(
                {
                    "kw": kw,
                    "kw_lower": _canon_l2l3_text(kw),
                    "patt": patt,
                    "priority": pri,
                    "san_pham": prod,
                }
            )
    rules.sort(key=lambda x: (x["priority"], len(x["kw_lower"])), reverse=True)
    return rules


class RAGProductMatcher:
    """Gemini-assisted BM25 product matcher."""

    def __init__(self, settings: Settings, gemini: GeminiClient) -> None:
        self.settings = settings
        self.gemini = gemini
        products_path = settings.df_products_path
        logger.info("Loading product catalog from %s", products_path)
        self.df_products = pd.read_excel(products_path)
        required_cols = ["Model", "Dòng SP", "Sản phẩm"]
        missing = [c for c in required_cols if c not in self.df_products.columns]
        if missing:
            raise ValueError(f"Missing columns in product catalog: {missing}")

        self.models_raw = [str(x).lower().strip() for x in self.df_products["Model"].fillna("")]
        self.models_nodau = [_canon_nodau(x) for x in self.df_products["Model"].fillna("")]
        self.bm25_raw = BM25Okapi([p.split() for p in self.models_raw])
        self.bm25_nodau = BM25Okapi([p.split() for p in self.models_nodau])

        with pd.ExcelFile(products_path) as xl:
            sheet_l2 = _find_sheet(xl, [r"loc.*lan.*2", r"loc.*2"])
            sheet_l3 = _find_sheet(xl, [r"loc.*lan.*3", r"loc.*3"])
            df_l2 = pd.read_excel(xl, sheet_l2) if sheet_l2 else None
            df_l3 = pd.read_excel(xl, sheet_l3) if sheet_l3 else None
        self.rules_l2 = _compile_rules_l2(df_l2)
        self.rules_l3 = _compile_rules_l3(df_l3)

    @staticmethod
    def _parse_llm_numbered(ans: str, n_expected: int) -> list[str]:
        out = []
        for line in (ans or "").splitlines():
            match = re.match(r"^\s*\d+[.\)]\s*(.*)$", line.strip())
            if match:
                out.append(match.group(1).strip() or "NONE")
        while len(out) < n_expected:
            out.append("NONE")
        return out[:n_expected]

    def llm_extract_batch(self, texts: list[str]) -> list[str]:
        joined = "\n".join([f"{i + 1}. {t}" for i, t in enumerate(texts)])
        prompt = dedent(
            f"""
            Bạn là bộ trích xuất sản phẩm cho lĩnh vực chiếu sáng và điện dân dụng Rạng Đông.
            Nhiệm vụ: từ văn bản, trích xuất đúng tên + mã sản phẩm xuất hiện trong câu.
            không lấy tên sản phẩm mà không có mã

            Đầu vào:
            {joined}

            Quy tắc:
            - chỉ trích xuất ra cụm sản phẩm mà có mã, model đi kèm
            - đặc biệt lưu ý: Không lấy cụm chung chung mà không có model/mã cụ thể đi kèm.
            - CHỈ copy lại đúng cụm có trong văn bản.
            - Nếu có nhiều ứng viên, chọn cụm cụ thể nhất.
            - Không suy đoán. Nếu không chắc chắn hoặc không có -> trả về "NONE".
            - Kết quả theo định dạng:
              1. <cụm hoặc NONE>
              2. <cụm hoặc NONE>

            Chỉ trả về danh sách kết quả theo thứ tự input, không giải thích thêm.
            """
        ).strip()

        for attempt in range(1, self.settings.max_retry + 1):
            try:
                resp_text = self.gemini.generate(prompt)
                return self._parse_llm_numbered(resp_text, len(texts))
            except Exception as exc:
                wait = self.settings.base_wait * attempt
                logger.warning(
                    "LLM extract error (%d/%d): %s -> sleep %.1fs",
                    attempt,
                    self.settings.max_retry,
                    exc,
                    wait,
                )
                time.sleep(wait)
        return ["NONE"] * len(texts)

    def bm25_search_dual(self, query: str, topk: int = 3) -> list[dict]:
        q_raw = (query or "").lower().split()
        q_nodau = _canon_nodau(query).split()
        s1 = self.bm25_raw.get_scores(q_raw) if q_raw else np.zeros(len(self.models_raw))
        s2 = self.bm25_nodau.get_scores(q_nodau) if q_nodau else np.zeros(len(self.models_nodau))
        scores = np.maximum(s1, s2)
        idx = np.argsort(scores)[::-1][:topk]
        return [
            {
                "Model": self.df_products.iloc[i]["Model"],
                "Dòng SP": self.df_products.iloc[i]["Dòng SP"],
                "Sản phẩm": self.df_products.iloc[i]["Sản phẩm"],
                "Score": float(scores[i]),
                "Evidence": self.models_raw[i],
            }
            for i in idx
        ]

    def _kw_match_l2(self, text: str) -> dict | None:
        t = _canon_l2l3_text(text)
        for rule in self.rules_l2:
            patt = rule.get("patt")
            if patt and patt.search(t):
                return {
                    "Dòng SP": rule["dong_sp"],
                    "Sản phẩm": rule["san_pham"],
                    "Src": "L2",
                }
        return None

    def _kw_match_l3(self, text: str) -> dict | None:
        t = _canon_l2l3_text(text)
        for rule in self.rules_l3:
            patt = rule.get("patt")
            if patt and patt.search(t):
                return {"Sản phẩm": rule["san_pham"], "Src": "L3"}
        return None

    def enrich_with_keyword_fallbacks(self, rag_batch: list[dict], texts: list[str]) -> list[dict]:
        out = []
        for item, text in zip(rag_batch, texts, strict=False):
            item = dict(item)
            line = (item.get("Dòng SP") or "").strip()
            prod = (item.get("Sản phẩm") or "").strip()
            if not line and not prod:
                hit2 = self._kw_match_l2(text)
                if hit2:
                    item["Dòng SP"] = hit2["Dòng SP"]
                    item["Sản phẩm"] = hit2["Sản phẩm"]
                    item["Model"] = ""
                    item["Score"] = 0.0
                    item["Src"] = "L2"
            if not (item.get("Sản phẩm") or "").strip():
                hit3 = self._kw_match_l3(text)
                if hit3:
                    item["Sản phẩm"] = hit3["Sản phẩm"]
                    item["Model"] = ""
                    item["Score"] = 0.0
                    item["Src"] = "L3"
            out.append(item)
        return out

    def retrieve_batch(self, texts: list[str]) -> list[dict]:
        exts = self.llm_extract_batch(texts)
        out = []
        for _, extracted in zip(texts, exts, strict=False):
            if extracted == "NONE":
                out.append(
                    {
                        "LLM_Extracted": "",
                        "Model": "",
                        "Dòng SP": "",
                        "Sản phẩm": "",
                        "Score": 0.0,
                        "Evidence": "",
                        "Src": "NONE",
                    }
                )
                continue
            cands = self.bm25_search_dual(extracted, topk=3)
            if not cands or cands[0]["Score"] < self.settings.bm25_min_score:
                out.append(
                    {
                        "LLM_Extracted": extracted,
                        "Model": "",
                        "Dòng SP": "",
                        "Sản phẩm": "",
                        "Score": 0.0,
                        "Evidence": "",
                        "Src": "NONE",
                    }
                )
            else:
                best = cands[0]
                out.append(
                    {
                        "LLM_Extracted": extracted,
                        "Model": best["Model"],
                        "Dòng SP": best["Dòng SP"],
                        "Sản phẩm": best["Sản phẩm"],
                        "Score": best["Score"],
                        "Evidence": best["Evidence"],
                        "Src": "RAG",
                    }
                )
        return out
