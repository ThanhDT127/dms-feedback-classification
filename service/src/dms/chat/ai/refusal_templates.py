"""Thông báo cho người dùng khi Plan Guard không chạy plan (design b03 D10).

Mọi câu chữ nằm ở một chỗ để: (1) test kiểm được là không lộ tên hàm, tên bảng hay câu SQL;
(2) b05 chỉ việc hiển thị, không tự nghĩ lời từ chối.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from .types import Reason

# Chuỗi tuyệt đối không được xuất hiện trong template (test 2.2 kiểm).
FORBIDDEN_FRAGMENTS: tuple[str, ...] = (
    "get_",
    "SELECT",
    "WHERE",
    "GROUP BY",
    "feedback_records",
    "v_issues_current",
    "unit_name",
    "sql",
)

TEMPLATES: dict[Reason, str] = {
    Reason.UNAUTHORIZED_SCOPE: (
        "Bạn không có quyền xem dữ liệu của {dropped_units}. Bạn có thể hỏi về: {allowed_units}."
    ),
    Reason.PARTIAL_REFUSAL: (
        "Tôi đã bỏ {dropped_units} khỏi câu hỏi vì bạn không có quyền xem. "
        "Kết quả dưới đây chỉ tính trên {allowed_units}."
    ),
    Reason.MULTI_UNIT_FILTER_UNAVAILABLE: (
        "Hiện mỗi lần chỉ xem được một đơn vị. Bạn chọn giúp một trong: {options}."
    ),
    Reason.OUT_OF_DOMAIN: (
        "Tôi chỉ trả lời được câu hỏi về dữ liệu phản hồi của khách hàng: "
        "số lượng, xu hướng, sản phẩm, đơn vị, địa bàn và tình trạng xử lý."
    ),
    Reason.INVALID_PLAN: (
        "Tôi chưa hiểu rõ câu hỏi. Bạn nêu giúp khoảng thời gian và điều muốn xem "
        "(ví dụ: tổng quan tháng 8, hay sản phẩm bị phản hồi nhiều nhất)."
    ),
    Reason.LOW_CONFIDENCE: (
        "Tôi chưa chắc mình hiểu đúng ý bạn. Bạn diễn đạt lại rõ hơn giúp tôi được không?"
    ),
    Reason.INVALID_DATE: (
        "Khoảng thời gian trong câu hỏi chưa hợp lệ. "
        "Bạn nêu lại giúp mốc thời gian, ví dụ: tháng 8/2026, hoặc từ 01/08 đến 31/08."
    ),
    Reason.ENTITY_AMBIGUOUS: "Ý bạn là {options}?",
    Reason.ENTITY_NOT_FOUND: (
        "Tôi không tìm thấy {dimension_label} nào tên là “{mention}” trong dữ liệu."
    ),
    Reason.FILTER_NOT_SUPPORTED: (
        "Hiện tôi chưa lọc được theo {filter_name} cho câu hỏi này. "
        "Bạn thử bỏ điều kiện đó, hoặc hỏi theo cách khác giúp tôi."
    ),
    Reason.INTENT_NOT_SUPPORTED: (
        "Chức năng này chưa có trong phiên bản hiện tại. "
        "Tôi đang hỗ trợ: tổng quan, xu hướng, so sánh, phân bổ theo sản phẩm, đơn vị, "
        "địa bàn, loại vấn đề và tra cứu phản hồi."
    ),
    Reason.TIMEOUT: (
        "Câu hỏi này mất nhiều thời gian hơn mức cho phép. "
        "Bạn thử thu hẹp khoảng thời gian rồi hỏi lại giúp tôi."
    ),
    Reason.INTERNAL: "Có lỗi khi lấy dữ liệu. Bạn thử lại sau ít phút giúp tôi.",
    Reason.SAMPLE_UNAVAILABLE: (
        "Tôi không lấy được ví dụ minh hoạ cho phần này; số liệu thống kê vẫn đầy đủ."
    ),
    Reason.NO_DATA: (
        "Không có dữ liệu nào trong khoảng {range}. "
        "Bạn thử mở rộng khoảng thời gian hoặc bỏ bớt điều kiện lọc."
    ),
    Reason.COMMENTARY_UNAVAILABLE: (
        "Phần nhận định chưa sẵn sàng lúc này; số liệu phía trên vẫn đầy đủ."
    ),
    Reason.SINGLE_KPI: "{label}: {value}.",
    Reason.NO_MATCH: (
        "Không tìm thấy phản hồi nào chứa các từ: {terms} ({filters}). "
        "Bạn thử đổi từ khoá, bỏ bớt điều kiện lọc hoặc mở rộng khoảng thời gian."
    ),
    Reason.NO_SEARCH_TERMS: "Bạn muốn tìm phản hồi có nội dung gì?",
    Reason.SQL_GENERATION_FAILED: (
        "Tôi chưa lập được truy vấn cho câu hỏi này. Bạn thử hỏi đơn giản hơn, ví dụ theo một "
        "chiều: sản phẩm, đơn vị, nguồn hoặc nhãn trong một khoảng thời gian."
    ),
    Reason.NO_DATA_FILTERED: (
        "Không có dữ liệu khớp điều kiện: {filters}. "
        "Bạn thử mở rộng khoảng thời gian hoặc bỏ bớt điều kiện lọc."
    ),
    Reason.RELAXED_SEARCH: (
        "Không tìm thấy phản hồi chứa đủ các từ bạn nhập (đã bỏ bớt: {dropped}), "
        "dưới đây là kết quả gần nhất."
    ),
    Reason.LOOKUP_BY_CODE_UNAVAILABLE: (
        "Hiện tôi chưa tra được phản hồi theo mã vấn đề. Bạn có thể mô tả nội dung phản hồi "
        "cần tìm, hoặc hỏi phản hồi tương tự một trích dẫn vừa hiển thị."
    ),
    Reason.FILE_LOOKUP_UNAVAILABLE: (
        "Hiện tôi chưa xem được phản hồi nằm ở file và dòng nào. Tính năng này sẽ có khi dữ liệu "
        "trích dẫn nguồn sẵn sàng."
    ),
    Reason.SIMILAR_REFERENCE_UNKNOWN: (
        "Tôi chưa xác định được phản hồi bạn muốn so sánh. Bạn chọn một trích dẫn trong câu trả "
        'trước (ví dụ: "giống cái thứ 2") hoặc mô tả nội dung phản hồi cần tìm.'
    ),
    Reason.TOPIC_RESET: "Đã bắt đầu chủ đề mới. Bạn muốn hỏi gì tiếp theo?",
    Reason.REPORT_RANGE_REQUIRED: "Bạn muốn báo cáo từ ngày nào đến ngày nào?",
    Reason.TREND_RANGE_TOO_LONG: (
        "Khoảng thời gian dài hơn {max_days} ngày nên tôi bỏ phần diễn biến theo ngày; "
        "các phần còn lại vẫn đầy đủ."
    ),
    Reason.SECTION_UNAVAILABLE: "Không lấy được dữ liệu",
    Reason.EXPORT_NO_SOURCE: "Bạn muốn xuất dữ liệu nào?",
    Reason.EXPORT_FAILED: (
        "Tôi chưa tạo được file lúc này. Bạn thử lại sau ít phút giúp tôi."
    ),
    Reason.EXPORT_TOO_LARGE: (
        "Dữ liệu cần xuất vượt dung lượng cho phép. "
        "Bạn thu hẹp khoảng thời gian hoặc bớt điều kiện lọc rồi xuất lại giúp tôi."
    ),
    Reason.HELP: (
        "Tôi trả lời câu hỏi về phản hồi của khách hàng: tổng quan theo kỳ, xu hướng theo ngày, "
        "so sánh hai kỳ, phân bổ theo sản phẩm, đơn vị, địa bàn, loại vấn đề, "
        "và tra cứu nội dung phản hồi."
    ),
}

MAX_OPTIONS = 5


def join_vi(values: Iterable[str]) -> str:
    """Nối danh sách theo lối tiếng Việt: "A, B và C"."""
    items = [str(value) for value in values if str(value).strip()]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " và " + items[-1]


def format_options(values: Sequence[str], limit: int = MAX_OPTIONS) -> str:
    return join_vi(list(values)[:limit])


def render(reason: Reason, **values: object) -> str:
    """Dựng thông báo cho ``reason``; thiếu placeholder thì ném KeyError để test bắt được."""
    template = TEMPLATES[reason]
    return template.format(**values)
