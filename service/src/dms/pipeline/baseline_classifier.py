"""Notebook-compatible baseline issue classifier backed by local model artifacts."""

from __future__ import annotations

import json
import logging
import pickle
import re
import unicodedata
from pathlib import Path
from typing import Any

import numpy as np
from scipy.sparse import csr_matrix, hstack
from unidecode import unidecode

from ..exceptions import ModelArtifactError
from ..settings import Settings
from .issue_classifier import MINOR_ORDER, canon

logger = logging.getLogger("dms-watcher")

ABBREV_MAP = {
    "k": "không",
    "ko": "không",
    "k0": "không",
    "kg": "không",
    "hok": "không",
    "khong": "không",
    "k dc": "không được",
    "k đc": "không được",
    "đc": "được",
    "kh": "khách hàng",
    "k/h": "khách hàng",
    "khach": "khách",
    "dl": "đại lý",
    "npp": "nhà phân phối",
    "ch": "cửa hàng",
    "sp": "sản phẩm",
    "spp": "sản phẩm",
    "mã sp": "mã sản phẩm",
    "cl": "chất lượng",
    "sx": "sản xuất",
    "bh": "bảo hành",
    "bhđ": "bảo hành đổi",
    "bhdt": "bảo hành đổi trả",
    "bhsc": "bảo hành sửa chữa",
    "hd": "hóa đơn",
    "hđ": "hóa đơn",
    "km": "khuyến mãi",
    "ct": "chương trình",
    "ctkm": "chương trình khuyến mãi",
    "đg": "đơn giá",
    "tt": "thanh toán",
    "tt sp": "thông tin sản phẩm",
    "cty": "công ty",
    "rđ": "rạng đông",
    "rd": "rạng đông",
}

KW_GROUP_TO_RULE = {
    "Hoạt động": "Đối thủ cạnh tranh.Hoạt động",
    "CTKM/giá/cơ chế": "Đối thủ cạnh tranh.CTKM, giá, cơ chế",
    "Thông tin SP": "Đối thủ cạnh tranh.TT SP",
    "TT SP": "Đối thủ cạnh tranh.TT SP",
}

RULE_TO_MINOR = {
    "Đối thủ cạnh tranh.Hãng": "Hãng",
    "Đối thủ cạnh tranh.Hoạt động": "Hoạt động",
    "Đối thủ cạnh tranh.CTKM, giá, cơ chế": "CTKM, giá, cơ chế",
    "Đối thủ cạnh tranh.TT SP": "TT SP",
    "Cảm xúc.Tích cực": "Cảm xúc.Tích cực",
    "Cảm xúc.Tiêu cực": "Cảm xúc.Tiêu cực",
}

COMP_MINORS = {"Hãng", "Hoạt động", "CTKM, giá, cơ chế", "TT SP"}
RD_BRAND_KEYS = {"rang dong", "rạng đông"}


def normalize_text_vi(text: str) -> str:
    s = unicodedata.normalize("NFC", str(text or "")).lower()
    s = re.sub(r"\s+", " ", s).strip()
    for short, full in ABBREV_MAP.items():
        s = re.sub(rf"(?<!\w){re.escape(short)}(?!\w)", full, s)
    s = re.sub(r"[^0-9a-zA-ZÀ-ỹ\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _norm_key(s: str) -> str:
    return re.sub(r"\s+", " ", unidecode(str(s or "")).lower()).strip()


def _get_list_any(map_obj: dict[str, Any], *keys: str) -> list[str]:
    if not isinstance(map_obj, dict):
        return []
    for key in keys:
        value = map_obj.get(key)
        if isinstance(value, list):
            return [str(item) for item in value]
    normalized = {_norm_key(key): value for key, value in map_obj.items()}
    for key in keys:
        value = normalized.get(_norm_key(key))
        if isinstance(value, list):
            return [str(item) for item in value]
    return []


def _alias_variants(s: str) -> set[str]:
    raw = str(s or "").strip()
    if not raw:
        return set()
    alias = unidecode(raw).lower()
    out = {
        raw.lower(),
        re.sub(r"[^a-z0-9]+", " ", alias).strip(),
        re.sub(r"[^a-z0-9]+", "", alias).strip(),
        re.sub(r"\.", "", alias).strip(),
    }
    return {value for value in out if len(value) > 1}


def _compile_kw_regex_map(kw_map: dict[str, Any]) -> dict[str, list[re.Pattern[str]]]:
    compiled: dict[str, list[re.Pattern[str]]] = {}
    if not isinstance(kw_map, dict):
        return compiled
    for group, value in kw_map.items():
        if group in ("manual_brand_alias", "Tích cực", "Tiêu cực"):
            continue
        if not isinstance(value, list):
            continue
        patterns = []
        for keyword in value:
            item = str(keyword).strip()
            if not item:
                continue
            if " " in item:
                patterns.append(re.compile(rf"(?<!\w){re.escape(item.lower())}(?!\w)"))
            else:
                patterns.append(re.compile(rf"\b{re.escape(item.lower())}\b", flags=re.UNICODE))
        compiled[group] = patterns
    return compiled


class BaselineIssueClassifier:
    """Load local notebook artifacts and infer preliminary issue labels."""

    REQUIRED_FILES = (
        "tfidf_word.pkl",
        "tfidf_char.pkl",
        "ovr_logreg.pkl",
        "best_thresholds.json",
        "label_cols.json",
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._load_keyword_resources()
        self._load_model_artifacts()

    def _load_keyword_resources(self) -> None:
        self.kw_map_all: dict[str, Any] = {}
        if self.settings.kw_map_path.exists():
            self.kw_map_all = json.loads(self.settings.kw_map_path.read_text(encoding="utf-8"))
        else:
            logger.warning("kw_map.json not found at %s; keyword hints disabled", self.settings.kw_map_path)

        self.pos_lexicon = sorted(set(_get_list_any(self.kw_map_all, "Tích cực", "tich cuc")))
        self.neg_lexicon = sorted(set(_get_list_any(self.kw_map_all, "Tiêu cực", "tieu cuc")))

        manual_brand_alias_map = (
            self.kw_map_all.get("manual_brand_alias", {}) if isinstance(self.kw_map_all, dict) else {}
        )
        self.brand_catalog: dict[str, dict[str, Any]] = {}
        for brand, aliases in (manual_brand_alias_map or {}).items():
            all_aliases = set()
            all_aliases |= _alias_variants(brand)
            for alias in aliases or []:
                all_aliases |= _alias_variants(alias)
            if _norm_key(brand) in RD_BRAND_KEYS:
                all_aliases |= {"rạng đông", "rang dong", "rd", "rđ", "r d"}
            self.brand_catalog[brand] = {"display": brand, "aliases": sorted(all_aliases)}

        self.kw_compiled_raw = _compile_kw_regex_map(self.kw_map_all)

    def _load_pickle(self, path: Path) -> Any:
        try:
            with path.open("rb") as handle:
                return pickle.load(handle)
        except Exception as exc:  # pragma: no cover - error path verified via public wrapper
            raise ModelArtifactError(f"Cannot load model artifact {path.name}: {exc}") from exc

    def _load_json(self, path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - error path verified via public wrapper
            raise ModelArtifactError(f"Cannot load model artifact {path.name}: {exc}") from exc

    def _load_model_artifacts(self) -> None:
        missing = [name for name, path in self.settings.required_model_artifact_paths.items() if not path.exists()]
        if missing:
            raise ModelArtifactError(
                "Missing required model artifacts in "
                f"{self.settings.model_dir}: {', '.join(sorted(missing))}"
            )

        self.tfidf_word = self._load_pickle(self.settings.tfidf_word_path)
        self.tfidf_char = self._load_pickle(self.settings.tfidf_char_path)
        self.ovr_logreg = self._load_pickle(self.settings.ovr_logreg_path)
        self.best_thresholds = self._load_json(self.settings.best_thresholds_path)
        self.label_cols = self._load_json(self.settings.label_cols_path)
        if not isinstance(self.label_cols, list) or not self.label_cols:
            raise ModelArtifactError("label_cols.json must contain a non-empty label list")

        try:
            self.threshold_vector = np.array([self.best_thresholds[col] for col in self.label_cols])
        except Exception as exc:
            raise ModelArtifactError(
                "best_thresholds.json does not cover every label in label_cols.json"
            ) from exc

        self.keyword_minors: list[str] = []
        if self.settings.keyword_minors_path.exists():
            payload = self._load_json(self.settings.keyword_minors_path)
            self.keyword_minors = [str(item) for item in payload.get("minors", [])]
            logger.info(
                "Loaded baseline model artifacts from %s (%d labels, keyword features=%d)",
                self.settings.model_dir,
                len(self.label_cols),
                len(self.keyword_minors),
            )
        else:
            logger.info(
                "Loaded baseline model artifacts from %s (%d labels, keyword features disabled)",
                self.settings.model_dir,
                len(self.label_cols),
            )

    def _kw_group(self, key: str) -> list[re.Pattern[str]]:
        return (
            self.kw_compiled_raw.get(key)
            or self.kw_compiled_raw.get(key.lower())
            or self.kw_compiled_raw.get(key.upper())
            or self.kw_compiled_raw.get(_norm_key(key))
            or []
        )

    def apply_general_keywords(self, text: str) -> set[str]:
        lowered = (text or "").lower()
        out: set[str] = set()
        for group, patterns in self.kw_compiled_raw.items():
            if group in ("Tích cực", "Tiêu cực", "manual_brand_alias"):
                continue
            if any(pattern.search(lowered) for pattern in patterns):
                rule = KW_GROUP_TO_RULE.get(group)
                if rule:
                    minor = RULE_TO_MINOR.get(rule)
                    if minor:
                        out.add(minor)
                elif group in MINOR_ORDER:
                    out.add(group)
        return out

    def detect_brand(self, text: str) -> tuple[str | None, str | None]:
        if not isinstance(text, str):
            return None, None
        raw = (text or "").lower()
        canonical = canon(text)
        hits: list[tuple[str, str]] = []
        for _display, data in self.brand_catalog.items():
            for alias in data["aliases"]:
                pattern = rf"(?<!\w){re.escape(alias)}(?!\w)"
                if re.search(pattern, canonical) or re.search(pattern, raw):
                    hits.append((data["display"], alias))
                    break
        if not hits:
            return None, None
        for display, alias in hits:
            if _norm_key(display) not in RD_BRAND_KEYS:
                return display, alias
        return hits[0]

    def regex_any(self, text: str, keywords: list[str]) -> bool:
        raw = (text or "").lower()
        canonical = canon(text)
        for keyword in keywords or []:
            item = str(keyword or "").strip()
            if not item:
                continue
            item_raw = item.lower()
            item_canon = canon(item)
            if " " in item_raw or " " in item_canon:
                if re.search(rf"(?<!\w){re.escape(item_raw)}(?!\w)", raw) or re.search(
                    rf"(?<!\w){re.escape(item_canon)}(?!\w)", canonical
                ):
                    return True
            else:
                if re.search(rf"\b{re.escape(item_raw)}\b", raw) or re.search(
                    rf"\b{re.escape(item_canon)}\b", canonical
                ):
                    return True
        return False

    def get_sentiment(self, text: str) -> str:
        pos = self.regex_any(text, self.pos_lexicon)
        neg = self.regex_any(text, self.neg_lexicon)
        if neg:
            return "Tiêu cực"
        if pos:
            return "Tích cực"
        return ""

    def _build_keyword_matrix(self, texts: list[str]) -> csr_matrix:
        if not self.keyword_minors:
            return csr_matrix((len(texts), 0), dtype=np.int8)

        compiled: list[list[re.Pattern[str]]] = []
        for minor in self.keyword_minors:
            keywords = [item for item in self.kw_map_all.get(minor, []) if item]
            compiled.append(
                [re.compile(rf"\b{re.escape(str(keyword))}\b") for keyword in keywords]
            )

        features = np.zeros((len(texts), len(self.keyword_minors)), dtype=np.int8)
        for row_idx, text in enumerate(texts):
            lowered = str(text or "").lower()
            for col_idx, patterns in enumerate(compiled):
                if any(pattern.search(lowered) for pattern in patterns):
                    features[row_idx, col_idx] = 1
        return csr_matrix(features)

    def predict_labels_baseline(self, text: str) -> dict[str, bool]:
        normalized = normalize_text_vi(text)
        x_word = self.tfidf_word.transform([normalized])
        x_char = self.tfidf_char.transform([normalized])
        x_keyword = self._build_keyword_matrix([normalized])
        features = hstack([x_word, x_char, x_keyword])
        probabilities = np.array(
            [estimator.predict_proba(features)[:, 1] for estimator in self.ovr_logreg.estimators_]
        ).ravel()
        predicted = (probabilities >= self.threshold_vector).astype(int)
        return {self.label_cols[idx]: bool(predicted[idx]) for idx in range(len(self.label_cols))}

    def apply_rules(self, text: str) -> list[str]:
        normalized = normalize_text_vi(text)
        lowered = normalized.lower()
        canonical = canon(normalized)
        hits: list[str] = []
        brand, _ = self.detect_brand(normalized)
        if brand and _norm_key(brand) not in RD_BRAND_KEYS:
            for group, rule in KW_GROUP_TO_RULE.items():
                patterns = self._kw_group(group)
                if any(pattern.search(lowered) for pattern in patterns):
                    hits.append(rule)
            price_kws = {
                "giá",
                "khuyến mãi",
                "ctkm",
                "cơ chế",
                "chiết khấu",
                "sale",
                "ck",
                "rẻ hơn",
                "flash sale",
                "ưu đãi",
                "giảm giá",
            }
            activity_kws = {"trưng bày", "làm biển", "băng rôn", "quảng cáo", "event", "roadshow", "tài trợ", "demo"}
            info_kws = {"sản phẩm", "mẫu mã", "tính năng", "thông số", "model", "datasheet", "catalog"}
            if any(keyword in canonical for keyword in price_kws):
                hits.append("Đối thủ cạnh tranh.CTKM, giá, cơ chế")
            if any(keyword in canonical for keyword in activity_kws):
                hits.append("Đối thủ cạnh tranh.Hoạt động")
            if any(keyword in canonical for keyword in info_kws):
                hits.append("Đối thủ cạnh tranh.TT SP")
            if "Đối thủ cạnh tranh.Hãng" not in hits:
                hits.append("Đối thủ cạnh tranh.Hãng")
        if self.regex_any(lowered, self.pos_lexicon):
            hits.append("Cảm xúc.Tích cực")
        if self.regex_any(lowered, self.neg_lexicon):
            hits.append("Cảm xúc.Tiêu cực")
        return sorted(set(hits))

    def infer_minor_labels(self, text: str) -> tuple[dict[str, bool], str, list[str], str]:
        labels = {key: bool(value) for key, value in self.predict_labels_baseline(text).items()}
        hits = self.apply_rules(text)
        mapped = {RULE_TO_MINOR[hit] for hit in hits if hit in RULE_TO_MINOR}
        keyword_hits = self.apply_general_keywords(text)
        brand, _ = self.detect_brand(text)
        is_competitor = bool(brand and _norm_key(brand) not in RD_BRAND_KEYS)

        for minor in MINOR_ORDER:
            labels.setdefault(minor, False)

        if is_competitor:
            for minor in list(labels.keys()):
                if minor not in COMP_MINORS:
                    labels[minor] = False
            for minor in COMP_MINORS:
                labels[minor] = minor in mapped or minor in keyword_hits
        else:
            if any(labels.get(minor, False) for minor in ["Bảo hành", "Bảng giá, Catalogue", "Bảng biển"]):
                labels["Hoạt động"] = True
            for minor in keyword_hits:
                if minor not in COMP_MINORS:
                    labels[minor] = True
            for minor in COMP_MINORS:
                labels[minor] = False

        sentiment = ""
        if "Cảm xúc.Tiêu cực" in hits:
            sentiment = "Tiêu cực"
        elif "Cảm xúc.Tích cực" in hits:
            sentiment = "Tích cực"
        return labels, sentiment, hits, brand or ""
