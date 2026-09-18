"""Tests for intent priority override guardrails (Luật 8-10).

Covers:
- Purchase intent suppressing Báo lỗi (Luật 8)
- Guidance intent suppressing Báo lỗi (Luật 10)
- Pre-purchase policy inquiry suppressing Bảo hành (Luật 9)
- Backward compatibility when original_text is not provided
"""

from __future__ import annotations

from dms.pipeline.issue_classifier import (
    _apply_intent_overrides,
    normalize_issue_output,
)

# ── Task 4.1: Purchase intent overrides Báo lỗi ──


def test_purchase_intent_suppresses_bao_loi():
    """Luật 8: 'bị hỏng muốn mua thay thế' → Báo lỗi removed."""
    text = "Bóng đèn nhà tôi bị hỏng nên đang muốn mua thay thế"
    result = _apply_intent_overrides(text, ["Báo lỗi"])
    assert "Báo lỗi" not in result


def test_hard_defect_preserves_bao_loi_despite_purchase_intent():
    """Luật 8 exception: hard defect (rò điện) keeps Báo lỗi."""
    text = "Cái đèn mua tuần trước bị rò điện, muốn đổi cái khác"
    result = _apply_intent_overrides(text, ["Báo lỗi"])
    assert "Báo lỗi" in result


def test_no_purchase_intent_preserves_bao_loi():
    """No purchase signal → Báo lỗi stays."""
    text = "Đèn LED panel mới lắp 3 tháng đã bị nhấp nháy liên tục"
    result = _apply_intent_overrides(text, ["Báo lỗi"])
    assert "Báo lỗi" in result


# ── Task 4.2: Guidance intent overrides Báo lỗi ──


def test_guidance_request_suppresses_bao_loi():
    """Luật 10: asking for help/guide → Báo lỗi removed."""
    text = "Anh mua ổ cắm này về nhưng không cài đặt được, hướng dẫn lại anh"
    result = _apply_intent_overrides(text, ["Báo lỗi"])
    assert "Báo lỗi" not in result


def test_confirmed_defect_despite_guidance_language():
    """User followed instructions but product still broken → Báo lỗi stays."""
    text = "Cài đặt đúng hướng dẫn rồi mà đèn vẫn nhấp nháy liên tục, chắc bị lỗi mạch"
    result = _apply_intent_overrides(text, ["Báo lỗi"])
    # No guidance regex match here (no "hướng dẫn lại", "chỉ cách", etc.)
    assert "Báo lỗi" in result


# ── Task 4.3: Pre-purchase policy overrides Bảo hành ──


def test_pre_purchase_policy_suppresses_bao_hanh():
    """Luật 9: asking warranty before buying → Bảo hành removed."""
    text = "Bên mình có đèn này không? Bảo hành mấy năm shop?"
    result = _apply_intent_overrides(text, ["Bảo hành"])
    assert "Bảo hành" not in result


def test_genuine_warranty_request_preserves_bao_hanh():
    """Already bought and requesting warranty → Bảo hành stays."""
    text = "Anh mua cái đèn hôm trước bị hỏng rồi, bảo hành đổi trả cho anh"
    result = _apply_intent_overrides(text, ["Bảo hành"])
    # Even though "mua" is in text, the pre-purchase policy pattern
    # ("bảo hành mấy/bao lâu/thế nào") does NOT match "bảo hành đổi trả"
    assert "Bảo hành" in result


# ── Task 4.4: Backward compatibility ──


def test_normalize_backward_compat_no_original_text():
    """When original_text is not provided, behavior is identical to before."""
    parsed = {
        "labels": {"Báo lỗi": True},
        "sentiment": "",
        "brand": "",
        "decision_log": [],
    }
    result_without = normalize_issue_output(parsed)
    result_with_empty = normalize_issue_output(parsed, original_text="")
    assert result_without["final_minors"] == result_with_empty["final_minors"]
    assert "Báo lỗi" in result_without["final_minors"]


def test_normalize_with_original_text_suppresses():
    """When original_text is provided with purchase intent, Báo lỗi removed."""
    parsed = {
        "labels": {"Báo lỗi": True},
        "sentiment": "",
        "brand": "",
        "decision_log": [],
    }
    result = normalize_issue_output(
        parsed,
        original_text="Bóng nhà bị hỏng rồi, muốn mua thay thế",
    )
    assert "Báo lỗi" not in result["final_minors"]
    # Should fallback to Tin trung lập since all labels removed
    assert "Tin trung lập" in result["final_minors"]


def test_normalize_empty_labels_after_override_falls_back():
    """When all labels removed by overrides, fallback to Tin trung lập."""
    parsed = {
        "labels": {"Báo lỗi": True},
        "sentiment": "",
        "brand": "",
        "decision_log": [],
    }
    result = normalize_issue_output(
        parsed,
        original_text="Đèn cũ hỏng, cần mua cái mới",
    )
    assert "Tin trung lập" in result["final_minors"]
