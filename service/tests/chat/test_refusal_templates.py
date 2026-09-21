from __future__ import annotations

import pytest

from dms.chat.ai.refusal_templates import (
    FORBIDDEN_FRAGMENTS,
    MAX_OPTIONS,
    TEMPLATES,
    format_options,
    join_vi,
    render,
)
from dms.chat.ai.types import Reason


def test_every_reason_has_a_template():
    missing = [reason.value for reason in Reason if reason not in TEMPLATES]
    assert not missing, f"thiếu template cho: {missing}"


@pytest.mark.parametrize("reason", sorted(TEMPLATES, key=lambda r: r.value))
def test_templates_do_not_leak_internals(reason):
    text = TEMPLATES[reason].lower()
    leaked = [fragment for fragment in FORBIDDEN_FRAGMENTS if fragment.lower() in text]
    assert not leaked, f"{reason.value} lộ chi tiết nội bộ: {leaked}"


def test_render_fills_placeholders():
    message = render(
        Reason.UNAUTHORIZED_SCOPE,
        dropped_units="Nha Trang",
        allowed_units="Truyền thống Vùng 1",
    )
    assert "Nha Trang" in message
    assert "Truyền thống Vùng 1" in message
    assert "{" not in message


def test_render_without_required_placeholder_raises():
    with pytest.raises(KeyError):
        render(Reason.ENTITY_AMBIGUOUS)


def test_join_vi_uses_vietnamese_conjunction():
    assert join_vi([]) == ""
    assert join_vi(["A"]) == "A"
    assert join_vi(["A", "B"]) == "A và B"
    assert join_vi(["A", "B", "C"]) == "A, B và C"


def test_format_options_caps_at_five():
    values = [f"Đơn vị {index}" for index in range(1, 9)]
    rendered = format_options(values)
    assert rendered.count(",") == MAX_OPTIONS - 2
    assert "Đơn vị 6" not in rendered
