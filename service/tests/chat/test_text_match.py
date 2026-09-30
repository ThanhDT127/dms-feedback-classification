from __future__ import annotations

from dms.chat.ai.text_match import (
    find_candidates,
    match_tokens,
    normalize_match_text,
    phrase_starts,
)


def test_normalize_handles_d_and_whitespace():
    assert normalize_match_text("Phản hồi về Rạng  Đông") == "phan hoi ve rang dong"
    assert normalize_match_text("  ĐỔI   trả ") == "doi tra"


def test_match_tokens_split_punctuation():
    assert match_tokens("TP.HCM, 2/9") == ["tp", "hcm", "2", "9"]


def test_phrase_starts():
    tokens = ["xoa", "du", "lieu", "va", "du", "lieu"]
    assert phrase_starts(tokens, ("du", "lieu")) == [1, 4]
    assert phrase_starts(tokens, ()) == []


def test_unaccented_province_matches_exactly():
    matches = find_candidates("phan hoi o ha noi", ["Hà Nội", "Hà Nam", "Nam Định"], 0.88)
    assert [m.value for m in matches] == ["Hà Nội"]
    assert matches[0].score == 1.0
    assert matches[0].mention == "ha noi"


def test_partial_mention_keeps_every_close_candidate():
    units = ["Truyền thống Vùng 1", "Vùng 1 Duyên hải", "Nha Trang"]
    matches = find_candidates("số liệu vùng 1 tháng 8", units, 0.88)
    assert {m.value for m in matches} == {"Truyền thống Vùng 1", "Vùng 1 Duyên hải"}
    assert all(m.score >= 0.88 for m in matches)


def test_short_tokens_do_not_fuzzy_match():
    assert find_candidates("miền nam", ["Hà Nam", "Nam Định"], 0.88) == []


def test_typo_matches_above_threshold():
    matches = find_candidates("phản hồi ở khanh hoa", ["Khánh Hòa"], 0.88)
    assert matches and matches[0].value == "Khánh Hòa"
    matches = find_candidates("phản hồi ở khan hoa", ["Khánh Hòa"], 0.88)
    assert matches and matches[0].score >= 0.88


def test_limit_and_ordering():
    values = ["Đèn LED Bulb", "Đèn LED Bulb 9W", "Đèn LED Tube"]
    matches = find_candidates("den led bulb", values, 0.8, limit=2)
    assert len(matches) == 2
    assert matches[0].value == "Đèn LED Bulb"
    assert matches[0].score >= matches[1].score
