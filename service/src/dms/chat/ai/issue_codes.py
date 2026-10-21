"""Nhận mã vấn đề trong câu hỏi để tra phản hồi theo mã.

Mã DMS thật là số thuần 6 chữ số ("241659"); dữ liệu mẫu và test dùng dạng "NT-0106". Chỉ nhận
số đứng sau chữ "mã" để không lẫn với năm, ngày hay số lượng trong câu.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .text_match import normalize_match_text

# Mỗi mã là một bước get_issues; bằng số bước tối đa của một lượt.
MAX_CODES = 3

_CODE = r"#?(?:[a-z][a-z0-9]{1,5}-)?\d{3,}"
# "mã 206501", "mã vấn đề NT-0106", "mã phản hồi là #240625, 241659 và 251098" (đã bỏ dấu).
CODE_LOOKUP_PATTERN = re.compile(
    rf"\bma (?:van de |phan hoi |so )?(?:la )?({_CODE}(?:\s*(?:,|;|va|hoac)\s*{_CODE})*)\b"
)
_CODE_TOKEN = re.compile(r"(?:[a-z][a-z0-9]{1,5}-)?\d{3,}")
_STANDALONE_CODE = re.compile(r"(?:[a-z][a-z0-9]{1,5}-)?\d{3,}")


def codes_in(text: str) -> tuple[str, ...]:
    """Các mã nêu sau chữ "mã", theo thứ tự xuất hiện, không trùng."""
    found: dict[str, None] = {}
    for match in CODE_LOOKUP_PATTERN.finditer(normalize_match_text(text)):
        for token in _CODE_TOKEN.findall(match.group(1)):
            found.setdefault(token.upper(), None)
    return tuple(found)


def normalize_codes(values: Iterable[object]) -> tuple[str, ...]:
    """Giá trị Planner đặt vào tham số mã → mã hợp lệ; bỏ giá trị không giống mã."""
    found: dict[str, None] = {}
    for value in values:
        items = value if isinstance(value, list | tuple) else (value,)
        for item in items:
            text = normalize_match_text(str(item or "")).lstrip("#")
            if _STANDALONE_CODE.fullmatch(text):
                found.setdefault(text.upper(), None)
    return tuple(found)
