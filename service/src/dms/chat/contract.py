"""
Interface Contract — DMS AI Chatbot
====================================

Single Source of Truth (SSOT) cho giao tiếp giữa:
  - Dev B (AI Orchestrator) → sinh QueryPlan
  - Dev A (Data Engine)     → nhận QueryPlan, trả QueryResult

Dựa trên codebase thật:
  - DB Schema:  feedback_records (22 cột), feedback_labels, feedback_record_versions
  - Analytics:  14 endpoints (analytics_api.py), FeedbackAnalyticsService (1055 dòng)
  - Labels:     21 nhãn phân loại (MINOR_ORDER) + 8 nhóm chính (MINOR_TO_MAJOR)
  - User:       UserStore (username, role=admin|user, display_name)
  - Filters:    AnalyticsFilter (date_from, date_to, province, district, unit_name)

Quy ước: Muốn sửa bất kỳ trường nào → 2 dev phải đồng ý trước khi commit.
Version: 1.0.0
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


# ═══════════════════════════════════════════════════════════════════
# CONSTANTS — Từ codebase thật
# ═══════════════════════════════════════════════════════════════════

# 21 nhãn phân loại — từ issue_classifier.py:MINOR_ORDER
CLASSIFICATION_LABELS: list[str] = [
    "Báo lỗi",
    "Báo CL tốt",
    "Y/c cải tiến",
    "Đề xuất SPM",
    "Bảng giá, Catalogue",
    "Bảng biển",
    "Kệ bóng, thử đèn,…",
    "Khác",
    "Tốt/ ko tốt",
    "Trả thưởng",
    "Đề xuất",
    "Bảo hành",
    "HTPP",
    "Hàng hoá",
    "Hàng giả",
    "Website",
    "Hãng",
    "Hoạt động",
    "CTKM, giá, cơ chế",
    "TT SP",
    "Tin trung lập",
]

# Nhóm chính — từ issue_classifier.py:MINOR_TO_MAJOR
MAJOR_GROUPS: dict[str, str] = {
    "Báo lỗi": "Sản phẩm",
    "Báo CL tốt": "Sản phẩm",
    "Y/c cải tiến": "Sản phẩm",
    "Đề xuất SPM": "Sản phẩm",
    "Bảng giá, Catalogue": "Yêu cầu công cụ BH",
    "Bảng biển": "Yêu cầu công cụ BH",
    "Kệ bóng, thử đèn,…": "Yêu cầu công cụ BH",
    "Khác": "Yêu cầu công cụ BH",
    "Tốt/ ko tốt": "Giá, cơ chế RD",
    "Trả thưởng": "Giá, cơ chế RD",
    "Đề xuất": "Giá, cơ chế RD",
    "Bảo hành": "Dịch vụ",
    "HTPP": "Dịch vụ",
    "Hàng hoá": "Dịch vụ",
    "Hàng giả": "Hàng giả",
    "Website": "Website",
    "Hãng": "Đối thủ cạnh tranh",
    "Hoạt động": "Đối thủ cạnh tranh",
    "CTKM, giá, cơ chế": "Đối thủ cạnh tranh",
    "TT SP": "Đối thủ cạnh tranh",
    "Tin trung lập": "Tin trung lập",
}

SENTIMENT_LABELS: tuple[str, ...] = ("Tích cực", "Trung lập", "Tiêu cực")

# Metadata field aliases — từ input_reader.py:METADATA_ALIASES
METADATA_ALIASES: dict[str, tuple[str, ...]] = {
    "issue_code": ("Mã vấn đề", "Ma van de"),
    "issue_date": ("Ngày ghi nhận", "Ngày", "Date"),
    "source": ("Nguồn", "Source"),
    "unit_name": ("Tên đơn vị", "Đơn vị", "Unit"),
    "business_status": ("Trạng thái", "Status"),
}

# Cột chính thức trong bảng feedback_records — từ repository.py:_apply_migration_1
FEEDBACK_RECORD_COLUMNS: list[str] = [
    "feedback_id",          # INTEGER PRIMARY KEY
    "source_file_key",      # TEXT NOT NULL
    "source_file_name",     # TEXT NOT NULL
    "source_row_number",    # INTEGER NOT NULL
    "last_job_id",          # TEXT NOT NULL
    "raw_data_json",        # TEXT NOT NULL (JSON blob chứa dữ liệu gốc Excel)
    "content",              # TEXT NOT NULL (nội dung phản hồi)
    "normalized_content",   # TEXT NOT NULL (đã chuẩn hóa cho duplicate check)
    "issue_code",           # TEXT (mã vấn đề, có thể null)
    "issue_date",           # TEXT (ngày ghi nhận, ISO format)
    "source",               # TEXT (nguồn: Zalo, Hotline, Nhân viên KD...)
    "unit_name",            # TEXT (tên đơn vị: CN Miền Nam, CN Miền Bắc...)
    "business_status",      # TEXT (trạng thái: Đã xử lý, Chờ xử lý...)
    "product",              # TEXT (sản phẩm đã phân loại)
    "product_line",         # TEXT (dòng sản phẩm)
    "model",                # TEXT (model sản phẩm)
    "sentiment",            # TEXT (Tích cực / Trung lập / Tiêu cực)
    "brand",                # TEXT (thương hiệu: Rạng Đông / đối thủ)
    "bm25_score",           # REAL (điểm BM25 matching sản phẩm)
    "classification_state", # TEXT NOT NULL (pending / completed / failed)
    "is_active",            # INTEGER NOT NULL DEFAULT 1
    "created_at",           # TEXT NOT NULL (ISO timestamp)
    "updated_at",           # TEXT NOT NULL (ISO timestamp)
    "classified_at",        # TEXT (ISO timestamp)
]

# Cột dùng được cho chatbot query (an toàn, không chứa internal state)
QUERYABLE_COLUMNS: list[str] = [
    "feedback_id", "content", "normalized_content", "issue_code", "issue_date",
    "source", "unit_name", "business_status", "product", "product_line",
    "model", "sentiment", "brand", "bm25_score", "raw_data_json",
]


# ═══════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════


class QueryPattern(str, Enum):
    """Strategy Data Engine sẽ dùng để thực thi truy vấn.

    Mapping từ kiến trúc Figma Khối [3] RAG Planner:
    - Pattern 1: SQL Mẫu (Function Registry qua LLM classification)
    - Pattern 2: SQL trên Semantic View (view chuẩn hóa logic phức tạp)
    - Pattern 3: FTS5 dual-index (tìm kiếm toàn văn tiếng Việt)
    - Pattern 4: json_extract + dynamic column detection
    """

    SQL_TEMPLATE = "sql_template"
    SEMANTIC_VIEW = "semantic_view"
    FTS5_SEARCH = "fts5_search"
    JSON_EXTRACT = "json_extract"


class AnswerShape(str, Enum):
    """Hình dạng câu trả lời cho Khối [7] Response Shaper."""

    TABLE = "table"           # Bảng Markdown (top products, issues by type...)
    NARRATIVE = "narrative"   # Tường thuật tổng hợp + trích dẫn nguồn
    NUMBER = "number"         # Một con số KPI duy nhất
    LIST = "list"             # Danh sách bullet points


class QueryStatus(str, Enum):
    """Kết quả thực thi truy vấn."""

    OK = "ok"
    ERROR = "error"
    NO_DATA = "no_data"


# ═══════════════════════════════════════════════════════════════════
# USER SCOPE — Khối [2] AUTHEN trích ra, Khối [4] DATA ép vào query
# ═══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class UserScope:
    """Security context trích từ JWT/session — Figma Khối [2] AUTHEN.

    Dựa trên UserStore hiện tại (user_store.py):
    - username, role (admin|user), display_name — đã có
    - unit_ids — CẦN THÊM vào UserStore (Sprint 1, Dev A)

    SecureQueryExecutor (Khối [4]) HARD-BIND unit_ids vào mọi SQL query.
    LLM (Khối [5]) KHÔNG BAO GIỜ thấy hoặc điều khiển scope.
    """

    username: str
    role: str                                       # "admin" | "user"
    display_name: str = ""
    unit_ids: list[str] = field(default_factory=list)  # ["CN Miền Nam", "CN Miền Bắc"]

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def scope_description(self) -> str:
        """Mô tả scope cho metadata (audit log, cache key)."""
        if self.is_admin:
            return "admin (toàn quyền)"
        return f"user (đơn vị: {', '.join(self.unit_ids) or 'chưa gán'})"

    @classmethod
    def from_user_dict(cls, user: dict[str, Any]) -> UserScope:
        """Tạo scope từ user dict do get_current_user() trả về.

        User dict hiện tại (deps.py L115-148):
          {"username": "...", "role": "admin|user", "display_name": "...", "is_active": True}
        Cần mở rộng thêm "unit_ids": [...] trong UserStore.
        """
        return cls(
            username=user.get("username", ""),
            role=user.get("role", "user"),
            display_name=user.get("display_name", ""),
            unit_ids=user.get("unit_ids", []),
        )


# ═══════════════════════════════════════════════════════════════════
# QUERY PLAN — Dev B sinh ra (Khối [3]), Dev A nhận (Khối [4])
# ═══════════════════════════════════════════════════════════════════


@dataclass
class QueryPlan:
    """Request từ AI Orchestrator (Khối [3] Planner) tới Data Engine (Khối [4]).

    Dev B's Query Planner phân tích câu hỏi user → sinh QueryPlan.
    Dev A's SecureQueryExecutor nhận QueryPlan → ép scope → chạy → trả QueryResult.
    """

    # ── Luôn có ──
    pattern: QueryPattern
    answer_shape: AnswerShape
    original_query: str                  # Câu hỏi đã qua Bước 0 Contextualize
    confidence: float = 0.85             # Planner confidence (0.0 - 1.0)

    # ── Pattern 1: SQL Template (Function Registry) ──
    function_name: str | None = None     # Tên hàm: get_overview, get_daily_trend...
    params: dict[str, Any] = field(default_factory=dict)
    # params có thể chứa:
    #   date_from: str (ISO date)   — lọc từ ngày
    #   date_to: str (ISO date)     — lọc tới ngày
    #   province: str               — lọc tỉnh/thành (dùng METADATA_ALIASES)
    #   district: str               — lọc quận/huyện
    #   unit_name: str              — lọc đơn vị (NOTE: bị override bởi scope nếu user)
    #   source: str                 — lọc nguồn (Zalo, Hotline...)
    #   label: str                  — lọc theo nhãn (21 nhãn)
    #   product: str                — lọc sản phẩm
    #   business_status: str        — lọc trạng thái (Đã xử lý, Chờ xử lý...)
    #   limit: int                  — giới hạn kết quả
    #   page: int                   — trang (pagination)
    #   page_size: int              — số dòng/trang
    #   period: str                 — kỳ so sánh: month|quarter|year
    #   feedback_id: int            — tra 1 phản hồi cụ thể

    # ── Pattern 2: Semantic View ──
    sql: str | None = None               # SQL trên scoped view (chỉ SELECT)

    # ── Pattern 3: FTS5 Search ──
    fts_query: str | None = None         # Chuỗi tìm kiếm (có dấu hoặc không dấu)
    fts_filters: dict[str, Any] = field(default_factory=dict)
    # fts_filters: date_from, date_to, unit_name, sentiment, source, label
    fts_limit: int = 20                  # Max results (1-100)

    # ── Pattern 4: JSON Extract ──
    json_keys: list[str] = field(default_factory=list)  # Keys từ raw_data_json
    json_filters: dict[str, Any] = field(default_factory=dict)
    # json_filters: feedback_id, issue_code

    def to_dict(self) -> dict[str, Any]:
        """Serialize thành dict JSON-safe."""
        d = asdict(self)
        d["pattern"] = self.pattern.value
        d["answer_shape"] = self.answer_shape.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> QueryPlan:
        """Deserialize từ dict."""
        d = dict(d)
        d["pattern"] = QueryPattern(d["pattern"])
        d["answer_shape"] = AnswerShape(d["answer_shape"])
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def validate(self) -> list[str]:
        """Trả về list lỗi validation (rỗng = hợp lệ)."""
        errors: list[str] = []

        if self.pattern == QueryPattern.SQL_TEMPLATE:
            if not self.function_name:
                errors.append("Pattern 'sql_template' bắt buộc có 'function_name'")
            elif self.function_name not in FUNCTION_REGISTRY_SPEC:
                errors.append(
                    f"function_name '{self.function_name}' không tồn tại. "
                    f"Có: {', '.join(FUNCTION_REGISTRY_SPEC)}"
                )

        if self.pattern == QueryPattern.SEMANTIC_VIEW and not self.sql:
            errors.append("Pattern 'semantic_view' bắt buộc có 'sql'")

        if self.pattern == QueryPattern.FTS5_SEARCH and not self.fts_query:
            errors.append("Pattern 'fts5_search' bắt buộc có 'fts_query'")

        if self.pattern == QueryPattern.JSON_EXTRACT and not self.json_keys:
            errors.append("Pattern 'json_extract' bắt buộc có 'json_keys'")

        if not 0.0 <= self.confidence <= 1.0:
            errors.append(f"'confidence' phải 0.0-1.0, nhận {self.confidence}")

        if not self.original_query.strip():
            errors.append("'original_query' không được rỗng")

        return errors


# ═══════════════════════════════════════════════════════════════════
# QUERY RESULT — Dev A trả về (Khối [4]), Dev B nhận (Khối [5],[7])
# ═══════════════════════════════════════════════════════════════════


@dataclass
class QueryResultMetadata:
    """Metadata thực thi gắn vào mỗi QueryResult."""

    total_rows: int = 0
    query_time_ms: int = 0
    scope_applied: str = ""           # "admin" hoặc "unit_ids=[CN Miền Nam]"
    cache_hit: bool = False
    pattern_used: str = ""
    executed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class QueryResult:
    """Response từ Data Engine (Khối [4]) về AI Orchestrator.

    Dev A's SecureQueryExecutor trả về sau khi chạy query.
    Dev B's SynthesisGuard (Khối [5]) kiểm tra + ResponseShaper (Khối [7]) format.

    Cấu trúc data[]: list[dict] — mỗi dict là 1 row kết quả.
    Các key trong dict phụ thuộc vào function/pattern đã chạy.
    """

    status: QueryStatus
    data: list[dict[str, Any]] = field(default_factory=list)
    metadata: QueryResultMetadata = field(default_factory=QueryResultMetadata)
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> QueryResult:
        d = dict(d)
        d["status"] = QueryStatus(d["status"])
        if isinstance(d.get("metadata"), dict):
            d["metadata"] = QueryResultMetadata(**d["metadata"])
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    @classmethod
    def ok(cls, data: list[dict[str, Any]], **meta_kwargs: Any) -> QueryResult:
        return cls(status=QueryStatus.OK, data=data, metadata=QueryResultMetadata(**meta_kwargs))

    @classmethod
    def error(cls, message: str) -> QueryResult:
        return cls(status=QueryStatus.ERROR, error_message=message)

    @classmethod
    def no_data(cls, message: str = "Không tìm thấy dữ liệu phù hợp.") -> QueryResult:
        return cls(status=QueryStatus.NO_DATA, error_message=message)

    def cache_key(self, plan: QueryPlan, scope: UserScope) -> str:
        """Cache key = hash(plan + scope_unit_ids). Khối [6] CACHE dùng."""
        key_data = json.dumps(
            {"plan": plan.to_dict(), "scope_units": sorted(scope.unit_ids)},
            sort_keys=True, ensure_ascii=False,
        )
        return hashlib.sha256(key_data.encode()).hexdigest()[:16]


# ═══════════════════════════════════════════════════════════════════
# CHAT SESSION & MESSAGE — Khối [6] Chat Store schema
# ═══════════════════════════════════════════════════════════════════


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass
class ChatMessage:
    """1 tin nhắn trong session chat — lưu SQLite bảng chat_messages."""

    role: MessageRole
    content: str
    metadata_json: str | None = None     # {"pattern_used", "sources", "tokens", "latency_ms"}
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    message_id: int | None = None        # Auto-assigned bởi DB


@dataclass
class ChatSession:
    """1 phiên chat — lưu SQLite bảng chat_sessions."""

    session_id: str
    user_id: str                         # Khớp UserStore.username
    title: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    expires_at: str = ""                 # TTL 7 ngày, housekeeping dọn


# ═══════════════════════════════════════════════════════════════════
# FUNCTION REGISTRY SPEC — Pattern 1 hàm SQL mẫu
#
# Mapping 1:1 với các phương thức trong FeedbackAnalyticsService
# (analytics/service.py) và các endpoint trong analytics_api.py
# ═══════════════════════════════════════════════════════════════════

FUNCTION_REGISTRY_SPEC: dict[str, dict[str, Any]] = {
    # ── Dashboard & Overview ──
    "get_dashboard": {
        "description": "Toàn bộ dashboard: overview + dailyTrend + issueTypes + geography + sources + units + groups + products + status",
        "maps_to": "FeedbackAnalyticsService.dashboard()",
        "api_endpoint": "GET /api/analytics/dashboard",
        "params": ["date_from", "date_to", "province", "district", "unit_name"],
        "returns": "Object với 9 panel: overview, dailyTrend, issueTypes, geography, sources, units, groups, products, status",
    },
    "get_overview": {
        "description": "Tổng quan KPI: total_issues, processed_issues, label_coverage, sentiment_coverage, product_coverage, duplicate_rate",
        "maps_to": "FeedbackAnalyticsService.overview()",
        "api_endpoint": "GET /api/analytics/overview",
        "params": ["date_from", "date_to", "province", "district", "unit_name"],
        "returns": "Object: total_issues, processed_issues, label_coverage, sentiment_coverage, product_coverage, multi_label_rate, duplicate_record_rate, duplicate_issue_rate",
    },
    "get_comparison": {
        "description": "So sánh KPI giữa 2 kỳ (tháng, quý, năm)",
        "maps_to": "FeedbackAnalyticsService.comparison()",
        "api_endpoint": "GET /api/analytics/comparison",
        "params": ["date_from", "date_to", "period"],  # period: month|quarter|year
        "returns": "Object: current_range, previous_range, metrics (5 KPIs with change + change_percent)",
    },

    # ── Phân bổ / Distribution ──
    "get_daily_trend": {
        "description": "Xu hướng phản hồi theo ngày (tích cực, tiêu cực, trung lập)",
        "maps_to": "FeedbackAnalyticsService.daily_trend()",
        "api_endpoint": "GET /api/analytics/trends/daily",
        "params": ["date_from", "date_to", "province", "district", "unit_name"],
        "returns": "Array of {date, issue_count, sentiment_counts: {Tích cực, Trung lập, Tiêu cực, Chưa gán}}",
    },
    "get_issue_types": {
        "description": "Phân bổ theo loại vấn đề (raw_data_json > 'Loại vấn đề')",
        "maps_to": "FeedbackAnalyticsService.issue_types()",
        "api_endpoint": "GET /api/analytics/issue-types",
        "params": ["date_from", "date_to", "province", "district", "unit_name"],
        "returns": "Array of {label, issue_count, percentage}",
    },
    "get_sources": {
        "description": "Phân bổ theo nguồn phản hồi (Zalo, Hotline, Nhân viên KD, DMS...)",
        "maps_to": "FeedbackAnalyticsService.sources()",
        "api_endpoint": "GET /api/analytics/sources",
        "params": ["date_from", "date_to", "province", "district", "unit_name"],
        "returns": "Array of {label, issue_count, percentage}",
    },
    "get_units": {
        "description": "Phân bổ theo đơn vị/chi nhánh",
        "maps_to": "FeedbackAnalyticsService.units()",
        "api_endpoint": "GET /api/analytics/units",
        "params": ["date_from", "date_to", "province", "district", "unit_name"],
        "returns": "Array of {label, issue_count, percentage}",
    },
    "get_groups": {
        "description": "Phân bổ theo nhóm nhãn chính (Sản phẩm, Dịch vụ, Giá cơ chế RD...)",
        "maps_to": "FeedbackAnalyticsService.groups()",
        "api_endpoint": "GET /api/analytics/groups",
        "params": ["date_from", "date_to", "province", "district", "unit_name"],
        "returns": "Array of {label(=major_group), issue_count, percentage}",
    },
    "get_products": {
        "description": "Phân bổ theo sản phẩm đã phân loại",
        "maps_to": "FeedbackAnalyticsService.products()",
        "api_endpoint": "GET /api/analytics/products",
        "params": ["date_from", "date_to", "province", "district", "unit_name"],
        "returns": "Array of {label(=product), issue_count, percentage}",
    },
    "get_geography": {
        "description": "Phân bổ theo tỉnh/thành phố và quận/huyện",
        "maps_to": "FeedbackAnalyticsService.geography()",
        "api_endpoint": "GET /api/analytics/geography",
        "params": ["date_from", "date_to", "province", "district", "unit_name"],
        "returns": "Object: provinces[], districts[], top_province, top_district, total_issues",
    },

    # ── Trạng thái xử lý ──
    "get_status_backlog": {
        "description": "Trạng thái xử lý: đã xử lý, tồn đọng, phân bố tuổi tồn",
        "maps_to": "FeedbackAnalyticsService.status_backlog()",
        "api_endpoint": "GET /api/analytics/status-backlog",
        "params": ["date_from", "date_to", "province", "district", "unit_name"],
        "returns": "Object: statuses[], processed_count, backlog_count, backlog_rate, age_buckets[]",
    },

    # ── Chi tiết records ──
    "get_issues": {
        "description": "Danh sách phản hồi chi tiết có phân trang (pagination)",
        "maps_to": "FeedbackAnalyticsService.issues()",
        "api_endpoint": "GET /api/analytics/issues",
        "params": ["date_from", "date_to", "province", "district", "unit_name",
                    "source", "label", "product", "business_status",
                    "page", "page_size"],
        "returns": "Object: items[] (full feedback records), total, page, page_size, total_pages",
    },
    "get_priority_issues": {
        "description": "Top phản hồi ưu tiên cao (nhiều label, tiêu cực, chưa xử lý)",
        "maps_to": "FeedbackAnalyticsService.priority_issues()",
        "api_endpoint": "GET /api/analytics/priority-issues",
        "params": ["date_from", "date_to", "limit"],
        "returns": "Array of priority-scored feedback records",
    },
    "get_duplicates": {
        "description": "Các nhóm phản hồi trùng lặp/tương tự",
        "maps_to": "FeedbackAnalyticsService.duplicate_details()",
        "api_endpoint": "GET /api/analytics/duplicates",
        "params": ["date_from", "date_to", "page", "page_size"],
        "returns": "Grouped duplicate records with match counts",
    },

    # ── Ma trận & Chất lượng ──
    "get_unit_issue_type_matrix": {
        "description": "Ma trận chéo đơn vị × loại vấn đề",
        "maps_to": "FeedbackAnalyticsService.unit_issue_type_matrix()",
        "api_endpoint": "GET /api/analytics/unit-issue-type-matrix",
        "params": ["date_from", "date_to"],
        "returns": "Matrix: rows=units, columns=issue_types, cells=issue_count",
    },
    "get_data_quality": {
        "description": "Chất lượng dữ liệu: tỷ lệ thiếu trường, phân bố trạng thái phân loại",
        "maps_to": "FeedbackAnalyticsService.data_quality()",
        "api_endpoint": "GET /api/analytics/data-quality",
        "params": ["date_from", "date_to"],
        "returns": "Object: field coverage percentages, classification state distribution",
    },
}
