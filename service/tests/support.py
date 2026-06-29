from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
from scipy.sparse import hstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.multiclass import OneVsRestClassifier


def write_keyword_map(keyword_dir: Path) -> None:
    keyword_dir.mkdir(parents=True, exist_ok=True)
    kw_map = {
        "Tích cực": ["tốt", "ổn"],
        "Tiêu cực": ["lỗi", "hỏng", "chậm"],
        "manual_brand_alias": {
            "Philips": ["philips"],
            "Rạng Đông": ["rạng đông", "rang dong", "rd"],
        },
        "Website": ["website", "web", "portal", "đăng nhập"],
        "Hoạt động": ["trưng bày", "quảng cáo"],
        "CTKM/giá/cơ chế": ["khuyến mãi", "giảm giá"],
        "TT SP": ["mẫu mã", "tính năng"],
    }
    (keyword_dir / "kw_map.json").write_text(
        json.dumps(kw_map, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def write_baseline_artifacts(
    model_dir: Path, keyword_dir: Path, include_keyword_minors: bool = True
) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    write_keyword_map(keyword_dir)

    texts = [
        "đèn bị lỗi cần bảo hành",
        "website đăng nhập chậm và lỗi",
        "philips đang khuyến mãi lớn",
        "sản phẩm tốt và ổn định",
        "cần bảo hành đổi trả",
        "đối thủ quảng cáo trưng bày mạnh",
    ]
    label_cols = ["Báo lỗi", "Bảo hành", "Website", "Hãng", "Hoạt động", "CTKM, giá, cơ chế"]
    y = np.array(
        [
            [1, 1, 0, 0, 0, 0],
            [1, 0, 1, 0, 0, 0],
            [0, 0, 0, 1, 0, 1],
            [0, 0, 0, 0, 0, 0],
            [0, 1, 0, 0, 0, 0],
            [0, 0, 0, 1, 1, 0],
        ],
        dtype=int,
    )

    normalized = texts
    tfidf_word = TfidfVectorizer(ngram_range=(1, 2))
    tfidf_char = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5))
    x_word = tfidf_word.fit_transform(normalized)
    x_char = tfidf_char.fit_transform(normalized)

    keyword_minors = ["Website", "Hoạt động", "CTKM, giá, cơ chế"] if include_keyword_minors else []
    if keyword_minors:
        kw_map = json.loads((keyword_dir / "kw_map.json").read_text(encoding="utf-8"))
        kw_features = np.zeros((len(texts), len(keyword_minors)), dtype=np.int8)
        for row_idx, text in enumerate(normalized):
            lowered = text.lower()
            for col_idx, minor in enumerate(keyword_minors):
                if any(keyword.lower() in lowered for keyword in kw_map.get(minor, [])):
                    kw_features[row_idx, col_idx] = 1
        x_keyword = kw_features
        features = hstack([x_word, x_char, x_keyword])
    else:
        features = hstack([x_word, x_char])

    classifier = OneVsRestClassifier(LogisticRegression(max_iter=500))
    classifier.fit(features, y)

    (model_dir / "tfidf_word.pkl").write_bytes(pickle.dumps(tfidf_word))
    (model_dir / "tfidf_char.pkl").write_bytes(pickle.dumps(tfidf_char))
    (model_dir / "ovr_logreg.pkl").write_bytes(pickle.dumps(classifier))
    (model_dir / "best_thresholds.json").write_text(
        json.dumps({label: 0.4 for label in label_cols}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (model_dir / "label_cols.json").write_text(
        json.dumps(label_cols, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if include_keyword_minors:
        (model_dir / "keyword_minors.json").write_text(
            json.dumps({"minors": keyword_minors}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
