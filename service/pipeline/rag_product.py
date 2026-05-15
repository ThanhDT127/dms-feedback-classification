"""
RAG Product matching module — BM25 dual search + LLM product extraction + L2/L3 keyword fallback.

Ported from notebook Cell 8 (RAG: LLM extract + BM25 dual + L2/L3 EXACT MATCH).
"""
import re
import time
import json
import numpy as np
import pandas as pd
from textwrap import dedent
from rank_bm25 import BM25Okapi
from unidecode import unidecode

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    DF_PRODUCTS_PATH,
    BM25_MIN_SCORE,
    MAX_RETRY,
    BASE_WAIT,
    logger,
)
from gemini_client import generate


# ── BM25 helpers ─────────────────────────────────────────────────────────────
def _canon_nodau(s: str) -> str:
    """Normalize to lowercase ASCII without diacritics for BM25 no-dấu."""
    return re.sub(r"[^a-z0-9 ]", " ", unidecode(str(s)).lower()).strip()


# ── L2/L3 helpers (exact match, giữ dấu) ────────────────────────────────────
def _canon_l2l3_text(s: str) -> str:
    """Only lower + normalize whitespace; KEEP diacritics for exact match."""
    return re.sub(r"\s+", " ", str(s or "").lower()).strip()


def _compile_kw_exact(kw: str):
    """Compile regex for exact word-boundary match, keeping diacritics."""
    kw_l = _canon_l2l3_text(kw)
    if not kw_l:
        return None
    return re.compile(rf"(?<!\w){re.escape(kw_l)}(?!\w)", flags=re.UNICODE)


def _norm(s):
    """Normalize for sheet/column name matching (removes diacritics)."""
    return re.sub(r"[^a-z0-9 ]", " ", unidecode(str(s or "")).lower()).strip()


def _find_sheet(xl, name_candidates):
    """Find a sheet by fuzzy name matching."""
    sheets = {_norm(s): s for s in xl.sheet_names}
    for pat in name_candidates:
        for k, v in sheets.items():
            if re.search(pat, k):
                return v
    return None


def _find_col(cols, pats):
    """Find a column by fuzzy name matching."""
    cols_map = {_norm(c): c for c in cols}
    for p in pats:
        for k, v in cols_map.items():
            if re.search(p, k):
                return v
    return None


def _split_kw(cell):
    """Split keyword cell by common delimiters."""
    parts = re.split(r"[;,/\|\n]+", str(cell or ""))
    return [p.strip() for p in parts if p.strip()]


# ── Rule compilation (L2/L3) ────────────────────────────────────────────────
def _compile_rules_L2(df):
    """Compile L2 rules from sheet: keyword → (Dòng SP, Sản phẩm)."""
    if df is None or df.empty:
        return []
    ck = _find_col(df.columns, [r"keyword|tu khoa|t u khoa|kw"])
    c_line = _find_col(df.columns, [r"dong sp|d o ng.*sp|nhom sp|nho m.*sp|dòng.*sp"])
    c_prod = _find_col(df.columns, [r"san pham|s a n.*ph m|ten sp|t n.*sp|sản phẩm"])
    c_pri = _find_col(df.columns, [r"uu tien|u u.*ti n|priority|prio"])

    rules = []
    if not ck or not c_line or not c_prod:
        return rules

    for _, r in df.iterrows():
        kws = _split_kw(r.get(ck, ""))
        line = str(r.get(c_line, "") or "").strip()
        prod = str(r.get(c_prod, "") or "").strip()
        pri = float(r.get(c_pri, 0) or 0)

        for kw in kws:
            patt = _compile_kw_exact(kw)
            if patt is None:
                continue
            rules.append({
                "kw": kw,
                "kw_lower": _canon_l2l3_text(kw),
                "patt": patt,
                "priority": pri,
                "dong_sp": line,
                "san_pham": prod,
            })

    rules.sort(key=lambda x: (x["priority"], len(x["kw_lower"])), reverse=True)
    return rules


def _compile_rules_L3(df):
    """Compile L3 rules from sheet: keyword → Sản phẩm."""
    if df is None or df.empty:
        return []
    ck = _find_col(df.columns, [r"keyword|tu khoa|t u khoa|kw"])
    c_prod = _find_col(df.columns, [r"san pham|s a n.*ph m|ten sp|t n.*sp|sản phẩm"])
    c_pri = _find_col(df.columns, [r"uu tien|u u.*ti n|priority|prio"])

    rules = []
    if not ck or not c_prod:
        return rules

    for _, r in df.iterrows():
        kws = _split_kw(r.get(ck, ""))
        prod = str(r.get(c_prod, "") or "").strip()
        pri = float(r.get(c_pri, 0) or 0)

        for kw in kws:
            patt = _compile_kw_exact(kw)
            if patt is None:
                continue
            rules.append({
                "kw": kw,
                "kw_lower": _canon_l2l3_text(kw),
                "patt": patt,
                "priority": pri,
                "san_pham": prod,
            })

    rules.sort(key=lambda x: (x["priority"], len(x["kw_lower"])), reverse=True)
    return rules


class RAGProductMatcher:
    """
    RAG-based product matcher combining:
    1. LLM extraction of product names/codes from text
    2. BM25 dual search (raw + no-diacritics) against product catalog
    3. L2/L3 exact keyword match fallback
    """

    def __init__(self, products_path: str = None):
        """
        Initialize with product catalog and keyword rules.

        Args:
            products_path: Path to "Phân Chia Nhóm Sản Phẩm V2.xlsx".
                          Defaults to config.DF_PRODUCTS_PATH.
        """
        products_path = products_path or str(DF_PRODUCTS_PATH)
        logger.info("Loading product catalog from %s", products_path)

        # ── Load product catalog ──
        self.df_products = pd.read_excel(products_path)
        required_cols = ["Model", "Dòng SP", "Sản phẩm"]
        missing = [c for c in required_cols if c not in self.df_products.columns]
        assert not missing, f"Missing columns in product catalog: {missing}"

        # ── Build BM25 indices ──
        self.models_raw = [str(x).lower().strip() for x in self.df_products["Model"].fillna("")]
        self.models_nodau = [_canon_nodau(x) for x in self.df_products["Model"].fillna("")]
        self.bm25_raw = BM25Okapi([p.split() for p in self.models_raw])
        self.bm25_nodau = BM25Okapi([p.split() for p in self.models_nodau])

        # ── Load L2/L3 rules ──
        xl = pd.ExcelFile(products_path)
        sheet_l2 = _find_sheet(xl, [r"loc.*lan.*2", r"loc.*2"])
        sheet_l3 = _find_sheet(xl, [r"loc.*lan.*3", r"loc.*3"])

        df_l2 = pd.read_excel(xl, sheet_l2) if sheet_l2 else None
        df_l3 = pd.read_excel(xl, sheet_l3) if sheet_l3 else None
        self.rules_l2 = _compile_rules_L2(df_l2)
        self.rules_l3 = _compile_rules_L3(df_l3)

        logger.info(
            "RAG ready: %d products, %d L2 rules, %d L3 rules",
            len(self.df_products), len(self.rules_l2), len(self.rules_l3),
        )

    # ── LLM extraction ──────────────────────────────────────────────────────
    @staticmethod
    def _parse_llm_numbered(ans: str, n_expected: int) -> list[str]:
        """Parse numbered list response from LLM."""
        out = []
        for line in (ans or "").splitlines():
            m = re.match(r"^\s*\d+[\.\\)]\s*(.*)$", line.strip())
            if m:
                out.append(m.group(1).strip() or "NONE")
        while len(out) < n_expected:
            out.append("NONE")
        return out[:n_expected]

    def llm_extract_batch(self, texts: list[str]) -> list[str]:
        """
        Use LLM to extract product model codes from a batch of texts.

        Returns list of extracted product strings or "NONE".
        """
        joined = "\n".join([f"{i+1}. {t}" for i, t in enumerate(texts)])
        prompt = dedent(f"""
        Bạn là bộ trích xuất sản phẩm cho lĩnh vực chiếu sáng & điện dân dụng Rạng Đông.
        Nhiệm vụ: từ văn bản, trích xuất đúng tên + mã sản phẩm xuất hiện trong câu.
        không lấy tên sản phẩm mà không có mã

        Đầu vào:
        {joined}

        Quy tắc:
        - chỉ trích xuất ra cụm sản phẩm mà có mã, model đi kèm
        - đặc biệt lưu ý :Không lấy cụm chung chung (vd: "đèn LED", "ổ cắm", "bình giữ nhiệt","atomat") mà không có model/mã cụ thể đi kèm.
        - CHỈ copy lại đúng cụm có trong văn bản (giữ nguyên dấu/cách/hoa-thường/ký tự).
        - Ưu tiên A: Mã/model (chứa chữ+số): VD OC10, AT10 8w, PC01, LED A60 9W...
        - Nếu có biến thể dạng "AT10 8w/12w" → lấy biến thể đầy đủ trước và sau dấu phân tách → "AT10 8w/12W".
        - ĐẶC BIỆT LƯU Ý: KHÔNG TRÍCH MÃ , MODEL CỦA SẢN PHẨM HÃNG KHÁC RẠNG ĐÔNG
        - Lưu ý: phân biệt chính xác câu có đề cập đến sản phâm không
        - Nếu có nhiều ứng viên, chọn cụm **cụ thể nhất** (model/mã dài hơn có ưu tiên).
        - Không suy đoán, không mô tả. Nếu không chắc chắn hoặc không có → trả về "NONE".
        - Kết quả phải theo đúng định dạng *mỗi dòng một mục*:
          1. <cụm hoặc NONE>
          2. <cụm hoặc NONE>
          ...

        Chỉ trả về danh sách kết quả theo thứ tự input, không giải thích thêm.
        """).strip()

        for attempt in range(1, MAX_RETRY + 1):
            try:
                resp_text = generate(prompt)
                return self._parse_llm_numbered(resp_text, len(texts))
            except Exception as e:
                wait = BASE_WAIT * attempt
                logger.warning("LLM extract error (%d/%d): %s → sleep %.1fs", attempt, MAX_RETRY, e, wait)
                time.sleep(wait)
        return ["NONE"] * len(texts)

    # ── BM25 search ──────────────────────────────────────────────────────────
    def bm25_search_dual(self, query: str, topk: int = 3) -> list[dict]:
        """Dual BM25 search: raw Vietnamese + ASCII no-diacritics."""
        q_raw = (query or "").lower().split()
        q_nodau = _canon_nodau(query).split()
        s1 = self.bm25_raw.get_scores(q_raw) if q_raw else np.zeros(len(self.models_raw))
        s2 = self.bm25_nodau.get_scores(q_nodau) if q_nodau else np.zeros(len(self.models_nodau))
        scores = np.maximum(s1, s2)
        idx = np.argsort(scores)[::-1][:topk]
        return [{
            "Model": self.df_products.iloc[i]["Model"],
            "Dòng SP": self.df_products.iloc[i]["Dòng SP"],
            "Sản phẩm": self.df_products.iloc[i]["Sản phẩm"],
            "Score": float(scores[i]),
            "Evidence": self.models_raw[i],
        } for i in idx]

    # ── L2/L3 keyword match ─────────────────────────────────────────────────
    def _kw_match_L2(self, text: str) -> dict | None:
        """Match exact L2 keyword rules (keeps diacritics)."""
        t = _canon_l2l3_text(text)
        for r in self.rules_l2:
            patt = r.get("patt")
            if patt and patt.search(t):
                return {"Dòng SP": r["dong_sp"], "Sản phẩm": r["san_pham"], "Src": "L2", "KW": r["kw"]}
        return None

    def _kw_match_L3(self, text: str) -> dict | None:
        """Match exact L3 keyword rules (keeps diacritics)."""
        t = _canon_l2l3_text(text)
        for r in self.rules_l3:
            patt = r.get("patt")
            if patt and patt.search(t):
                return {"Sản phẩm": r["san_pham"], "Src": "L3", "KW": r["kw"]}
        return None

    def enrich_with_keyword_fallbacks(self, rag_batch: list[dict], texts: list[str]) -> list[dict]:
        """
        Apply L2/L3 keyword fallback after RAG:
        - If RAG returned empty Dòng SP & Sản phẩm → try L2 (exact)
        - If still empty Sản phẩm → try L3 (exact)
        """
        out = []
        for item, text in zip(rag_batch, texts):
            item = dict(item)
            line = (item.get("Dòng SP") or "").strip()
            prod = (item.get("Sản phẩm") or "").strip()

            # L2 when both line & prod are empty
            if not line and not prod:
                hit2 = self._kw_match_L2(text)
                if hit2:
                    item["Dòng SP"] = hit2["Dòng SP"]
                    item["Sản phẩm"] = hit2["Sản phẩm"]
                    item["Model"] = ""
                    item["Score"] = 0.0
                    item["Src"] = "L2"

            # L3 when still missing product
            if not (item.get("Sản phẩm") or "").strip():
                hit3 = self._kw_match_L3(text)
                if hit3:
                    item["Sản phẩm"] = hit3["Sản phẩm"]
                    item["Model"] = ""
                    item["Score"] = 0.0
                    item["Src"] = "L3"

            out.append(item)
        return out

    # ── Main entry point ─────────────────────────────────────────────────────
    def retrieve_batch(self, texts: list[str]) -> list[dict]:
        """
        Full RAG pipeline for a batch of texts:
        1. LLM extract product codes
        2. BM25 dual search against catalog
        3. Return structured results

        Returns list of dicts with keys:
            LLM_Extracted, Model, Dòng SP, Sản phẩm, Score, Evidence, Src
        """
        exts = self.llm_extract_batch(texts)
        out = []
        for t, e in zip(texts, exts):
            if e == "NONE":
                out.append({
                    "LLM_Extracted": "", "Model": "", "Dòng SP": "", "Sản phẩm": "",
                    "Score": 0.0, "Evidence": "", "Src": "NONE",
                })
                continue
            cands = self.bm25_search_dual(e, topk=3)
            if not cands or cands[0]["Score"] < BM25_MIN_SCORE:
                out.append({
                    "LLM_Extracted": e, "Model": "", "Dòng SP": "", "Sản phẩm": "",
                    "Score": 0.0, "Evidence": "", "Src": "NONE",
                })
            else:
                b = cands[0]
                out.append({
                    "LLM_Extracted": e,
                    "Model": b["Model"],
                    "Dòng SP": b["Dòng SP"],
                    "Sản phẩm": b["Sản phẩm"],
                    "Score": b["Score"],
                    "Evidence": b["Evidence"],
                    "Src": "RAG",
                })
        return out
