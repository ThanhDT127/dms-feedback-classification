"""Nạp prompt của chat từ ``chat/ai/prompts/`` (design b01 D4).

``prompt_renderer.render_template`` chỉ thay bộ biến cố định của prompt phân loại, nên chat
dùng hàm render riêng nhưng giữ cùng ``RenderedPrompt`` (version + sha256 của template).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

from ...prompt_renderer import RenderedPrompt

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# Chỉ `{ten_bien}` mới là placeholder; JSON mẫu dạng `{"key": ...}` không bị đụng tới.
_PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")


def render_prompt_text(template: str, variables: Mapping[str, str]) -> str:
    """Thay placeholder trong một lượt, nên giá trị chứa `{x}` không bị thay tiếp."""
    names = set(_PLACEHOLDER.findall(template))
    missing = sorted(names - set(variables))
    if missing:
        raise ValueError("Missing prompt variables: " + ", ".join(missing))
    unused = sorted(set(variables) - names)
    if unused:
        raise ValueError("Unused prompt variables: " + ", ".join(unused))
    return _PLACEHOLDER.sub(lambda m: variables[m.group(1)], template).strip()


def sanitize_prompt_data(text: str, limit: int | None = None) -> str:
    """Nội dung không đáng tin đưa vào khối dữ liệu: không cho đóng/mở thẻ, gộp khoảng trắng."""
    cleaned = " ".join((text or "").replace("<", "‹").replace(">", "›").split())
    if limit is not None and len(cleaned) > limit:
        cleaned = cleaned[: limit - 1].rstrip() + "…"
    return cleaned


@lru_cache(maxsize=32)
def _read_template(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_prompt(name: str, variables: Mapping[str, str]) -> RenderedPrompt:
    path = PROMPTS_DIR / f"{name}.txt"
    template = _read_template(path)
    return RenderedPrompt(
        text=render_prompt_text(template, variables),
        source_path=path,
        version=path.stem,
        sha256=hashlib.sha256(template.encode("utf-8")).hexdigest(),
    )
