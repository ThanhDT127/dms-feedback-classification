"""Schema ngữ nghĩa cho Pattern 2 — nguồn chuẩn của prompt sinh SQL và SQL Guard (design b09 D2).

Chỉ khai báo view **đã áp phạm vi** của Dev A. Cột ``province``/``district`` chỉ thêm khi view thật
có (test bắt lệch sẽ đỏ nếu khai báo trước).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ALIASES_PATH = Path(__file__).resolve().parent / "data" / "column_aliases_vi.json"
ALIAS_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class Col:
    type: str  # text | date | int | real
    description_vi: str
    categorical: bool = False
    values: str | None = None  # khoá của MetadataProvider.valid_values() hoặc hằng của contract
    like_allowed: bool = False


@dataclass(frozen=True)
class ViewSpec:
    description_vi: str
    columns: dict[str, Col]
    forbidden_columns: frozenset[str] = field(default_factory=frozenset)


ISSUES_VIEW = "v_issues_current_scoped"
LABELS_VIEW = "v_issue_labels_scoped"

VIEWS: dict[str, ViewSpec] = {
    ISSUES_VIEW: ViewSpec(
        description_vi="Mỗi dòng là một phản hồi đang hiệu lực trong phạm vi của người hỏi",
        columns={
            "feedback_id": Col("int", "Khoá của dòng phản hồi (dùng để nối với nhãn)"),
            "issue_code": Col(
                "text", "Mã vấn đề; một vấn đề có thể có nhiều dòng, có dòng thiếu mã"
            ),
            "issue_date": Col("date", "Ngày phát sinh (YYYY-MM-DD)"),
            "unit_name": Col("text", "Đơn vị kinh doanh", categorical=True, values="units"),
            "sentiment": Col("text", "Cảm xúc", categorical=True, values="sentiments"),
            "business_status": Col("text", "Trạng thái xử lý", categorical=True, values="statuses"),
            "product": Col(
                "text", "Sản phẩm", categorical=True, values="products", like_allowed=True
            ),
            "product_line": Col("text", "Dòng sản phẩm", like_allowed=True),
            "model": Col("text", "Model sản phẩm", like_allowed=True),
            "source": Col("text", "Nguồn phản hồi", categorical=True, values="sources"),
            "brand": Col("text", "Thương hiệu"),
        },
        forbidden_columns=frozenset(
            {"raw_data_json", "normalized_content", "bm25_score", "content"}
        ),
    ),
    LABELS_VIEW: ViewSpec(
        description_vi="Mỗi dòng là một nhãn phân loại của một phản hồi trong phạm vi của người hỏi",
        columns={
            "feedback_id": Col("int", "Khoá phản hồi, nối với v_issues_current_scoped.feedback_id"),
            "issue_code": Col("text", "Mã vấn đề của phản hồi"),
            "label": Col("text", "Nhãn phân loại", categorical=True, values="labels"),
            "major_group": Col("text", "Nhóm nhãn lớn"),
        },
    ),
}

MEASURES: dict[str, str] = {
    "so_van_de": "COUNT(DISTINCT issue_code) — chỉ tính dòng có issue_code (khớp dashboard)",
    "so_phan_hoi": "COUNT(*) — số dòng phản hồi",
}
JOINS: tuple[str, ...] = (f"{LABELS_VIEW}.feedback_id = {ISSUES_VIEW}.feedback_id",)
ISSUE_MEASURE_HINT = "van_de"
DATE_COLUMN = "issue_date"
UNIT_COLUMN = "unit_name"


def allowed_columns(view: str) -> frozenset[str]:
    return frozenset(VIEWS[view].columns)


def all_forbidden_columns() -> frozenset[str]:
    out: set[str] = set()
    for spec in VIEWS.values():
        out |= spec.forbidden_columns
    return frozenset(out)


def categorical_columns() -> dict[str, Col]:
    return {
        name: col
        for spec in VIEWS.values()
        for name, col in spec.columns.items()
        if col.categorical
    }


def render_schema_vi() -> str:
    """Mô tả cho prompt sinh SQL; chỉ nhắc view đã áp phạm vi."""
    lines: list[str] = []
    for name, spec in VIEWS.items():
        lines.append(f"VIEW {name} — {spec.description_vi}")
        for column, col in spec.columns.items():
            flags = []
            if col.categorical:
                flags.append("giá trị phải lấy đúng từ danh sách được phép")
            if col.like_allowed:
                flags.append("được dùng LIKE")
            suffix = f" ({'; '.join(flags)})" if flags else ""
            lines.append(f"  - {column} [{col.type}]: {col.description_vi}{suffix}")
    lines.append("ĐỘ ĐO CHUẨN")
    lines.extend(f"  - {alias}: {expr}" for alias, expr in MEASURES.items())
    lines.append("NỐI BẢNG")
    lines.extend(f"  - {join}" for join in JOINS)
    return "\n".join(lines)


@lru_cache(maxsize=1)
def load_column_aliases(path: Path = ALIASES_PATH) -> dict[str, str]:
    return dict(json.loads(path.read_text(encoding="utf-8")))


def column_title(alias: str) -> str:
    """Tiêu đề tiếng Việt cho alias kết quả; alias lạ thay ``_`` bằng khoảng trắng."""
    known = load_column_aliases()
    if alias in known:
        return known[alias]
    return alias.replace("_", " ").strip() or alias
