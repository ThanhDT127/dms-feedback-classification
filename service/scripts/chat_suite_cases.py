"""Ca kiểm thử lấy từ ``docs/chatbot/bo-cau-hoi-kiem-thu-theo-moc.md`` (M1–M5 + giao diện).

Số kỳ vọng bám dữ liệu DMS thật trong ``work/classification_jobs.db`` (ảnh chụp 2026-09-21).
Mỗi ca là một chuỗi lượt trong **cùng một phiên**; phép kiểm gắn vào từng lượt. Ca có
``reuse`` không gọi lại chatbot mà kiểm lại kết quả của ca khác (vd. 28b đọc lại câu 28).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from chat_suite_checks import (
    Check,
    TurnResult,
    TurnSpec,
    block,
    commentary_not_matching,
    commentary_status,
    comparison_table_localized,
    custom,
    dropped_at_most,
    export_link,
    file_location_rows,
    found_between,
    has_commentary,
    info,
    intent,
    kpi,
    kpi_not,
    kpi_summary,
    logs_not_contain,
    no_content_column,
    no_reason,
    no_runner_error,
    no_technical_keys,
    not_status,
    nowhere,
    numbers_present,
    pattern_used,
    plan_param,
    plan_param_absent,
    quotes,
    ranking_has,
    ranking_top,
    reason,
    report_range,
    report_sections,
    status,
    subtitle_contains,
    text_contains,
    timeseries_points,
    top_ranking,
)

TV1 = "Truyền thống Vùng 1"
TV2 = "Truyền thống Vùng 2"
TV3 = "Truyền thống Vùng 3"

# Người dùng giả lập; runner dựng ``UserScope`` từ dict này giống ``chat_endpoint``.
USERS: dict[str, dict] = {
    "admin": {"username": "suite_admin", "role": "admin", "display_name": "Suite Admin"},
    "tv1": {
        "username": "suite_tv1",
        "role": "user",
        "display_name": "Suite TV1",
        "unit_ids": [TV1],
    },
}

# Cấu hình theo mốc (mục "Cấu hình để test đủ các mốc").
PROFILES: dict[str, dict[str, str]] = {
    "M5": {"chat_milestone": "M5", "chat_enabled_patterns": "sql_template,fts5_search,semantic_view"},
    "M1": {"chat_milestone": "M1", "chat_enabled_patterns": "sql_template"},
}


@dataclass
class Case:
    id: str
    group: str
    title: str
    turns: list[TurnSpec] = field(default_factory=list)
    profile: str = "M5"
    user: str = "admin"
    manual: str | None = None  # lý do chỉ kiểm được bằng tay (giao diện)
    reuse: tuple[str, ...] = ()  # kiểm lại lượt cuối của các ca này thay vì hỏi lại
    reuse_checks: list[Check] = field(default_factory=list)
    note: str = ""

    @property
    def llm_turns(self) -> int:
        return 0 if self.manual or self.reuse else len(self.turns)


def T(question: str, *checks: Check) -> TurnSpec:  # noqa: N802 - viết tắt cho bảng ca
    return TurnSpec(question=question, checks=list(checks))


def setup(question: str) -> TurnSpec:
    """Lượt dọn đường cho lượt sau; chỉ cần không lỗi."""
    return T(question, not_status("error"), no_runner_error())


def _only_units(*allowed: str) -> Check:
    def predicate(r: TurnResult) -> tuple[bool, str]:
        labels = [
            str(i.get("label"))
            for b in r.blocks_of("ranking")
            for i in b.get("payload", {}).get("items", [])
        ]
        extra = [label for label in labels if label not in allowed]
        return bool(labels) and not extra, ", ".join(labels) or "không có xếp hạng"

    return custom(f"xếp hạng chỉ gồm {', '.join(allowed)}", predicate)


def _no_data_blocks() -> Check:
    return custom(
        "không trả khối số liệu",
        lambda r: (
            not [b for b in r.blocks if b.get("kind") != "export"],
            ", ".join(str(b.get("kind")) for b in r.blocks) or "không có",
        ),
    )


def _no_steps_run() -> Check:
    return custom(
        "không thực thi truy vấn nào",
        lambda r: (not r.functions, ", ".join(r.functions) or "không"),
    )


def _has_quotes_or_relaxed() -> Check:
    def predicate(r: TurnResult) -> tuple[bool, str]:
        count = sum(len(b.get("payload", {}).get("quotes", [])) for b in r.blocks_of("quote"))
        relaxed = "RELAXED_SEARCH" in r.reasons
        return count > 0, f"{count} trích dẫn" + (" · RELAXED_SEARCH" if relaxed else "")

    return custom("có kết quả (có thể kèm RELAXED_SEARCH)", predicate)


def _any_table_or_ranking() -> Check:
    return custom(
        "có bảng hoặc xếp hạng",
        lambda r: (
            bool(r.blocks_of("table") or r.blocks_of("ranking")),
            ", ".join(str(b.get("kind")) for b in r.blocks) or "không có khối",
        ),
    )


def _ranking_sum() -> Check:
    def getter(r: TurnResult) -> str:
        items = [i for b in r.blocks_of("ranking") for i in b.get("payload", {}).get("items", [])]
        return f"tổng {sum(i.get('value') or 0 for i in items)} trên {len(items)} dòng"

    return info("tổng các dòng xếp hạng (kỳ vọng ~1.003)", getter)


def _no_sql_markers() -> Check:
    return custom(
        "nhận định không lộ khoá {{sql.*}}",
        lambda r: (
            not [t for t in r.commentary if "{{" in t or "sql." in t],
            "sạch",
        ),
    )


def _no_timeseries() -> Check:
    return custom(
        "bỏ phần theo ngày",
        lambda r: (not r.blocks_of("timeseries"), f"{len(r.blocks_of('timeseries'))} khối theo ngày"),
    )


def _summary_compressed() -> Check:
    return custom(
        "phiên đã có tóm tắt nén",
        lambda r: (r.history_summary_chars > 0, f"{r.history_summary_chars} ký tự tóm tắt"),
    )


SIMILAR_SETUP = "Liệt kê phản hồi có chữ chập chờn"
NT_PRODUCTS = "Sản phẩm nào bị phản hồi nhiều nhất ở Nha Trang?"


CASES: list[Case] = [
    # ── M1 — 12 hàm thống kê ──
    Case("1", "M1", "Tổng quan tháng 8", [T(
        "Tổng quan tháng 8/2026",
        status("ok", "partial"), intent("OVERVIEW"),
        kpi("total_issues", 2284), kpi("processed_issues", 2),
        kpi("label_coverage", 99.8, tol=0.051), kpi("sentiment_coverage", 55.6, tol=0.051),
        commentary_not_matching(r"tỉ lệ xử lý (là )?\d", "không gọi số đếm 'Đã xử lý' là tỉ lệ"),
    )]),
    Case("2", "M1", "Tổng số phản hồi", [T(
        "Có tất cả bao nhiêu phản hồi?",
        status("ok", "partial"), intent("OVERVIEW"), numbers_present(27012),
    )]),
    Case("3", "M1", "Xu hướng theo ngày tháng 6", [T(
        "Phản hồi biến động theo ngày trong tháng 6/2026 thế nào?",
        status("ok", "partial"), intent("TREND"), timeseries_points(1),
    )]),
    Case("4", "M1", "So sánh tháng 3 với tháng 4", [T(
        "So sánh tháng 3/2026 với tháng 4/2026",
        status("ok", "partial"), intent("COMPARISON"), numbers_present(3134, 2844),
        kpi_summary(),
    )]),
    Case("4b", "M1", "So sánh với kỳ liền trước (get_comparison)", [T(
        "So sánh tháng 8/2026 với kỳ trước",
        status("ok", "partial"), intent("COMPARISON"), numbers_present(2284, 2343),
        subtitle_contains("so với 01/07/2026 – 31/07/2026"),
    )], note="Nguồn cho ca 49: câu này phải ra bảng Chỉ tiêu của get_comparison."),
    Case("5", "M1", "Sản phẩm bị phản hồi nhiều nhất", [T(
        "Sản phẩm nào bị phản hồi nhiều nhất?",
        status("ok", "partial"), intent("DRILL_PRODUCT"),
        ranking_top(["Chưa xác định"], [10709]),
        ranking_has("Bulb", 1994), ranking_has("Vợt muỗi", 1635), ranking_has("Ổ cắm kéo dài, phích cắm", 1456),
    )]),
    Case("6", "M1", "Đơn vị nhiều vấn đề nhất", [T(
        "Đơn vị nào có nhiều vấn đề nhất?",
        status("ok", "partial"), intent("DRILL_UNIT"),
        ranking_top([TV2, TV1, TV3], [9240, 7199, 4642]),
    )]),
    Case("7", "M1", "Phân bổ theo tỉnh thành", [T(
        "Phân bổ phản hồi theo tỉnh thành",
        status("ok", "partial"), intent("DRILL_GEOGRAPHY"), ranking_top(["Hà Nội"], loose=True), top_ranking(3),
    )]),
    Case("8", "M1", "Loại vấn đề hay gặp nhất", [T(
        "Loại vấn đề nào hay gặp nhất?",
        status("ok", "partial"), intent("DRILL_ISSUE"),
        # Đếm theo cột "Loại vấn đề" của file gốc, khớp panel dashboard (chốt 2026-09-29).
        ranking_top(["Vấn đề khác", "Thông tin đối thủ cạnh tranh", "Báo lỗi, chất lượng, bảo hành"],
                    [8892, 5764, 5114]),
    )]),
    Case("9", "M1", "Còn bao nhiêu chưa xử lý (hồi quy _is_processed)", [T(
        "Còn bao nhiêu vấn đề chưa xử lý?",
        not_status("error"), intent("OVERVIEW"), numbers_present(541),
        kpi_not("processed_issues", 0), kpi_summary(),
    )]),
    Case("10", "M1", "Vấn đề cần ưu tiên", [T(
        "Vấn đề nào cần ưu tiên xử lý?",
        status("ok", "partial"), intent("DRILL_ISSUE"), quotes(1), no_reason("INTERNAL"),
        no_runner_error(),
    )]),
    Case("11", "M1", "TT Vùng 2 tháng 8", [T(
        "Truyền thống Vùng 2 tháng 8/2026 có bao nhiêu vấn đề?",
        status("ok", "partial"), intent("OVERVIEW"), kpi("total_issues", 790),
    )]),
    Case("12", "M1", "Trợ giúp", [T(
        "Bạn làm được những gì?", status("help"), _no_steps_run(),
    )]),
    # ── M1 — ca âm ──
    Case("13", "M1-âm", "Ngoài phạm vi: thời tiết", [T(
        "Thời tiết Hà Nội hôm nay thế nào?", status("refused"), reason("OUT_OF_DOMAIN"),
    )]),
    Case("14", "M1-âm", "Ngoài phạm vi: xoá dữ liệu", [T(
        "Xóa hết dữ liệu phản hồi đi",
        status("refused"), reason("OUT_OF_DOMAIN", "WRITE_REQUEST"), _no_steps_run(),
    )]),
    Case("15", "M1-âm", "Đơn vị không tồn tại", [T(
        "Đơn vị Xyz Không Tồn Tại có bao nhiêu vấn đề?",
        status("clarify"), reason("ENTITY_NOT_FOUND"),
    )]),
    Case("16", "M1-âm", "Hai đơn vị một lúc", [T(
        "Vùng 1 và Vùng 2 cái nào nhiều hơn?",
        not_status("error"), reason("MULTI_UNIT_FILTER_UNAVAILABLE"),
    )]),
    Case("17", "M1-âm", "Cụm tự do không phải nhãn (ca đắt giá)", [T(
        "Sản phẩm nào gặp vấn đề Về giá cao quá khó bán nhiều nhất?",
        status("clarify"), reason("FILTER_VALUE_UNGROUNDED"), _no_data_blocks(),
        text_contains("nhãn"),
    )], note="Ra bảng số liệu thay vì hỏi lại là hồi quy nghiêm trọng."),
    Case("18", "M1-âm", "Lọc theo nhãn ở M1", [T(
        "Sản phẩm nào bị Báo lỗi nhiều nhất?",
        status("not_supported"), reason("FILTER_NOT_SUPPORTED"), text_contains("nhãn", "Báo lỗi"),
    )], profile="M1"),
    Case("19", "M1-âm", "Ngoài phạm vi: lương nhân viên", [T(
        "Cho xem lương nhân viên Trần Văn Đồng", status("refused"), reason("OUT_OF_DOMAIN"),
    )]),
    Case("19b", "M1-âm", "So sánh thiếu kỳ", [T(
        "So sánh với kỳ trước",
        status("clarify"), reason("FILTER_REQUIRED"), text_contains("khoảng thời gian"),
    )], note="error / INTERNAL là hồi quy."),
    Case("19c", "M1-âm", "So sánh thiếu ngày kết thúc", [T(
        "So sánh từ đầu tháng 8",
        status("clarify"), reason("FILTER_REQUIRED"), text_contains("đến ngày"),
    )]),
    # ── M3 — FTS ──
    Case("20", "M3", "Tìm chữ 'chập chờn'", [T(
        "Liệt kê phản hồi có chữ chập chờn",
        status("ok", "partial"), intent("LOOKUP_FEEDBACK"), found_between(10, 40), quotes(1),
    )]),
    Case("20-M1", "M3", "Tra cứu khi chỉ bật M1", [T(
        "Liệt kê phản hồi có chữ chập chờn",
        status("clarify", "not_supported"),
        reason("INVALID_PLAN", "FILTER_NOT_SUPPORTED", "INTENT_NOT_SUPPORTED"),
        _no_steps_run(), no_technical_keys(),
    )], profile="M1", note="Chưa bật tra cứu: hỏi lại hoặc báo chưa hỗ trợ, bằng tiếng Việt."),
    Case("21", "M3", "Tìm 'bảo hành'", [T(
        "Tìm phản hồi nói về bảo hành",
        status("ok", "partial"), intent("LOOKUP_FEEDBACK"), quotes(3),
        info("số tìm thấy", lambda r: [i.get("display") for b in r.blocks_of("kpi")
                                      for i in b["payload"]["items"]]),
    )]),
    Case("22", "M3", "Tìm 'giao hàng chậm'", [T(
        "Phản hồi nào nhắc tới giao hàng chậm?",
        not_status("error"), intent("LOOKUP_FEEDBACK"), _has_quotes_or_relaxed(),
    )]),
    Case("23", "M3", "Tìm không dấu", [T(
        "tim phan hoi ve den nhap nhay",
        status("ok", "partial"), intent("LOOKUP_FEEDBACK"), quotes(1),
    )]),
    Case("24", "M3", "Vị trí trong file nguồn", [T(
        "Phản hồi về rỉ sét nằm ở file nào?",
        status("ok", "partial"), intent("LOOKUP_FILE"), file_location_rows(),
        no_reason("FILE_LOOKUP_UNAVAILABLE"),
    )]),
    Case("25", "M3", "Tìm tương tự sau một lượt tra cứu", [
        setup(SIMILAR_SETUP),
        T("Tìm phản hồi giống cái thứ 2",
          status("ok", "partial"), intent("LOOKUP_SIMILAR"), quotes(1),
          no_reason("SIMILAR_REFERENCE_UNKNOWN")),
    ]),
    Case("25b", "M3", "Tìm tương tự khi chưa có lượt trước", [T(
        "Tìm phản hồi giống cái thứ 2", not_status("error"), reason("SIMILAR_REFERENCE_UNKNOWN"),
    )]),
    Case("26", "M3", "Từ khoá không khớp", [T(
        "Tìm phản hồi về zzzqqq", not_status("error"), reason("NO_MATCH"),
    )]),
    Case("27", "M3", "Tra theo mã", [T(
        "Tra phản hồi mã 206501", not_status("error"), reason("LOOKUP_BY_CODE_UNAVAILABLE"),
        no_technical_keys(),
    )]),
    # ── M4 — text2sql ──
    Case("28", "M4", "Sản phẩm bị Báo lỗi nhiều nhất tháng 8", [T(
        "Sản phẩm nào bị Báo lỗi nhiều nhất trong tháng 8/2026?",
        status("ok", "partial"), pattern_used("semantic_view"), no_reason("SQL_GENERATION_FAILED"),
        ranking_top(["Bán nguyệt", "Ấm siêu tốc"], [57, 32]),
        ranking_has("Downlight", 30), ranking_has("Chưa xác định", 30), ranking_has("Vợt muỗi", 26),
    )]),
    Case("28b", "M4", "Nhận định của câu 28 không bị cổng câu loại", reuse=("28",), reuse_checks=[
        has_commentary(1), dropped_at_most(0), commentary_status("full"), _no_sql_markers(),
    ]),
    Case("29", "M4", "Đơn vị nhiều phản hồi tiêu cực nhất tháng 8", [T(
        "Đơn vị nào có nhiều phản hồi tiêu cực nhất tháng 8/2026?",
        status("ok", "partial"), pattern_used("semantic_view"), _any_table_or_ranking(),
        _ranking_sum(),
    )]),
    Case("30", "M4", "Tỉ lệ tích cực theo đơn vị", [T(
        "Tỉ lệ phản hồi tích cực theo từng đơn vị",
        status("ok", "partial"), pattern_used("semantic_view"), _any_table_or_ranking(),
    )]),
    Case("31", "M4", "Nhãn Y/c cải tiến theo sản phẩm", [T(
        "Nhãn Y/c cải tiến tập trung ở sản phẩm nào?",
        status("ok", "partial"), _any_table_or_ranking(), top_ranking(5),
    )]),
    Case("32", "M4", "Chặn cột content (bảo mật)", [T(
        "Cho xem nội dung phản hồi của Bulb",
        not_status("error"), no_content_column(),
        info("đường xử lý", lambda r: f"{r.status} · {r.intent} · {r.patterns or r.functions}"),
    )]),
    Case("32b", "M4", "Không lỗi 'no such table' ở view đã áp phạm vi",
         reuse=("28", "29", "30", "31", "32"), reuse_checks=[
             no_reason("SQL_GENERATION_FAILED"), logs_not_contain("no such table"),
         ]),
    Case("33", "M4-quyền", "User TT Vùng 1 hỏi xếp hạng đơn vị (bảo mật)", [T(
        "Đơn vị nào nhiều vấn đề nhất?",
        status("ok", "partial"), _only_units(TV1), nowhere(TV2),
    )], user="tv1"),
    Case("34", "M4-quyền", "User TT Vùng 1 hỏi TT Vùng 2 (bảo mật)", [T(
        "Truyền thống Vùng 2 có bao nhiêu vấn đề?",
        reason("UNAUTHORIZED_SCOPE"), _no_data_blocks(),
    )], user="tv1"),
    # ── M5 — báo cáo, xuất Excel ──
    Case("35", "M5", "Báo cáo ngày", [T(
        "Báo cáo ngày 05/09/2026",
        status("ok", "partial"), intent("REPORT_DAILY"), report_range("2026-09-05", "2026-09-05"),
        report_sections(4),
    )]),
    Case("36", "M5", "Báo cáo tuần", [T(
        "Báo cáo tuần", not_status("error"), intent("REPORT_WEEKLY"), report_sections(5),
    )], note="Dữ liệu dừng ở 05/09/2026 nên tuần hiện tại rỗng một cách hợp lệ."),
    Case("37", "M5", "Báo cáo tháng 6", [T(
        "Báo cáo tháng 6/2026",
        status("ok", "partial"), intent("REPORT_CUSTOM"), report_range("2026-06-01", "2026-06-30"),
        block("timeseries"), block("ranking", min_count=3),
    )]),
    Case("38", "M5", "Báo cáo khoảng > 92 ngày", [T(
        "Báo cáo từ 01/01/2026 đến 31/08/2026",
        status("ok", "partial"), reason("TREND_RANGE_TOO_LONG"), _no_timeseries(),
        block("ranking", min_count=3),
    )]),
    Case("39", "M5", "Báo cáo thiếu khoảng", [T(
        "Báo cáo", status("clarify"), reason("REPORT_RANGE_REQUIRED"),
    )]),
    Case("40", "M5", "Xuất Excel sau một lượt có dữ liệu", [
        setup("Đơn vị nào có nhiều vấn đề nhất?"),
        T("Xuất kết quả vừa rồi ra Excel", not_status("error"), intent("REPORT_EXPORT"), export_link()),
    ]),
    Case("40b", "M5", "Xuất Excel khi chưa có lượt trước", [T(
        "Xuất kết quả vừa rồi ra Excel", not_status("error"), reason("EXPORT_NO_SOURCE"),
    )]),
    # ── M5 — trí nhớ phiên ──
    Case("41", "M5-nhớ", "Kế thừa ngữ cảnh thời gian", [
        setup("Tổng quan tháng 8/2026"),
        T("Còn tháng 7 thì sao?",
          status("ok", "partial"), intent("OVERVIEW"), subtitle_contains("01/07/2026 – 31/07/2026")),
    ]),
    Case("42", "M5-nhớ", "Giữ chiều sản phẩm, đổi đơn vị", [
        T(NT_PRODUCTS, status("ok", "partial"), intent("DRILL_PRODUCT"), plan_param("unit_name", "Nha Trang")),
        T("Còn đơn vị Biên Hòa?",
          status("ok", "partial"), intent("DRILL_PRODUCT"), plan_param("unit_name", "Biên Hòa"),
          plan_param_absent("unit_name", "Nha Trang")),
    ]),
    Case("43", "M5-nhớ", "Đổi chủ đề thì không kế thừa slot", [
        setup(NT_PRODUCTS),
        T("Thôi, chuyển chủ đề khác. Báo cáo tháng 5/2026",
          not_status("error"), intent("REPORT_CUSTOM"), report_range("2026-05-01", "2026-05-31"),
          plan_param_absent("unit_name", "Nha Trang")),
    ]),
    Case("44", "M5-nhớ", "12 lượt liên tiếp rồi hỏi lại câu đầu", [
        setup("Tổng quan tháng 8/2026"),
        setup("Còn tháng 7 thì sao?"),
        setup("Sản phẩm nào bị phản hồi nhiều nhất tháng 7/2026?"),
        setup("Đơn vị nào có nhiều vấn đề nhất tháng 7/2026?"),
        setup("Loại vấn đề nào hay gặp nhất tháng 7/2026?"),
        setup("Phân bổ phản hồi theo tỉnh thành tháng 7/2026"),
        setup("Phản hồi biến động theo ngày trong tháng 7/2026 thế nào?"),
        setup("So sánh tháng 6/2026 với tháng 7/2026"),
        setup("Truyền thống Vùng 1 tháng 7/2026 có bao nhiêu vấn đề?"),
        setup("Vấn đề nào cần ưu tiên xử lý?"),
        setup("Còn bao nhiêu vấn đề chưa xử lý?"),
        T("Tổng quan tháng 8/2026",
          status("ok", "partial"), intent("OVERVIEW"), kpi("total_issues", 2284),
          _summary_compressed()),
    ]),
    # ── Giao diện ──
    Case("45", "UI", "F5 mở lại cuộc đang dở", manual="Cần trình duyệt: localStorage + tải lại trang."),
    Case("46", "UI", "+ Cuộc mới tách cuộc", manual="Cần trình duyệt: danh sách cuộc bên trái."),
    Case("47", "UI", "Xoá cuộc đang mở", manual="Cần trình duyệt: khung trắng sau khi xoá, F5."),
    Case("48", "UI", "Đổi tài khoản không thấy cuộc cũ", manual="Cần trình duyệt: đăng xuất/đăng nhập."),
    Case("49", "UI", "Bảng so sánh với kỳ trước (phần dữ liệu)", reuse=("4b",),
         reuse_checks=[comparison_table_localized()],
         note="Chỉ kiểm payload; căn lề thật trên màn hình vẫn phải xem bằng trình duyệt."),
]

CASE_INDEX: dict[str, Case] = {case.id: case for case in CASES}
