# 📐 DMS Chatbot — Sơ Đồ Kiến Trúc

---

## 1. 🏗️ C4 Container — Kiến trúc Tổng quan TO-BE

> [!NOTE]
> **Xanh dương** = module hiện có (giữ nguyên) · **Cam** = module mới cần xây · **Xám** = hệ thống ngoài

```mermaid
graph TB
    subgraph EXT["☁️ External Systems"]
        SP["📂 SharePoint\nDocument Library"]
        GEMINI["🧠 Gemini Vertex AI\nLLM Engine"]
        TEAMS["📢 MS Teams\nWebhook"]
    end

    subgraph FE["🖥️ Frontend Layer"]
        DASH["📊 Dashboard SPA\nVanilla JS — existing"]
        CHAT_UI["💬 Chat Widget\nReact — NEW"]
        REPORT_UI["📄 Report Viewer\nMarkdown — NEW"]
    end

    subgraph API["⚡ API Gateway — FastAPI"]
        AUTH["🔐 Auth API\nJWT + bcrypt"]
        ANA["📈 Analytics API\n14 endpoints"]
        CLS["🏷️ Classify API\nFile upload + jobs"]
        CHAT["🤖 Chat API\nNEW"]
        RPT["📋 Report API\nNEW"]
        SRCH["🔎 Search API\nNEW"]
    end

    subgraph ORC["🎭 Orchestration Layer — NEW"]
        RTR["🧭 Intent Router\n15 intents"]
        TOOL["🔧 Tool Executor\nTool-First pattern"]
        BLD["✍️ Response Builder\nLLM Synthesis"]
        CONV["💭 Conversation Mgr\n3-tier memory"]
    end

    subgraph DATA["💾 Data Layer"]
        SQL[("🗃️ SQLite WAL\njobs + analytics\n+ chat NEW")]
        QD[("🔮 Qdrant\n768D + BM25\nNEW")]
    end

    subgraph WRK["⚙️ Background Workers"]
        WATCH["👁️ Watcher\nPoll SharePoint"]
        PIPE["🏭 Classification\nPipeline"]
        EMB["📐 Embedding Worker\nNEW"]
    end

    DASH --> ANA
    CHAT_UI -->|"SSE stream"| CHAT
    REPORT_UI --> RPT

    CHAT --> RTR
    RTR --> TOOL
    TOOL --> ANA
    TOOL --> SRCH
    SRCH --> QD
    TOOL --> BLD
    BLD --> GEMINI
    CHAT --> CONV
    CONV --> SQL

    RPT --> TOOL

    WATCH --> SP
    WATCH --> PIPE
    PIPE --> GEMINI
    PIPE --> SQL
    PIPE -->|"on_batch_done"| EMB
    EMB --> QD

    CLS --> PIPE
    ANA --> SQL
    PIPE -->|"notify"| TEAMS
```

### Chú giải các Module Mới

| Module | Chức năng | Ưu tiên |
|:--|:--|:-:|
| 🤖 **Chat API** | `POST /api/chat`, `/api/chat/stream` — endpoint chat chính | 🔴 P0 |
| 🧭 **Intent Router** | Phân loại 15 intent (overview, trend, drill-down, lookup, report...) | 🔴 P0 |
| 🔧 **Tool Executor** | Gọi analytics service + Qdrant, trả JSON số liệu chính xác | 🔴 P0 |
| ✍️ **Response Builder** | Gemini tổng hợp tool results → Markdown response có trích dẫn | 🔴 P0 |
| 💭 **Conversation Mgr** | 3-tier memory compaction + follow-up detection | 🔴 P0 |
| 🔮 **Qdrant** | Vector DB: Dense 768D + BM25 Sparse, Hybrid RRF search | 🟠 P1 |
| 📐 **Embedding Worker** | Hook sau classification → encode + upsert vào Qdrant | 🟠 P1 |
| 🔎 **Search API** | `/api/search/semantic` — tìm feedback bằng ngôn ngữ tự nhiên | 🟠 P1 |
| 📋 **Report API** | Tạo báo cáo tổng hợp LLM + export DOCX/Excel | 🟡 P2 |
| 💬 **Chat Widget** | React component tích hợp vào Dashboard hoặc standalone | 🟡 P2 |

---

## 2. 🔄 Data Flow — Luồng Dữ Liệu Toàn Hệ Thống

```mermaid
flowchart LR
    subgraph INPUT["📥 Nguồn Dữ Liệu"]
        A1["📁 SharePoint\nExcel Files"]
        A2["📤 Web Upload"]
    end

    subgraph PROCESS["⚙️ Xử Lý"]
        B1["📖 Read Excel\n+ Normalize"]
        B2["🏷️ RAG Product\nBM25 Match"]
        B3["🧠 LLM Classify\n21 Labels"]
        B4["📐 Embed Text\n768D + BM25"]
    end

    subgraph STORE["💾 Lưu Trữ"]
        C1[("🗃️ SQLite\nfeedback_records")]
        C2[("🔮 Qdrant\nfeedback vectors")]
        C3["📂 SharePoint\nOutput/"]
    end

    subgraph SERVE["🤖 Chatbot Phục Vụ"]
        D1["🧭 Intent Router"]
        D2["🔧 SQL Aggregation\n14 Analytics Tools"]
        D3["🔍 Semantic Search\nQdrant Hybrid"]
        D4["✍️ LLM Synthesis\nGemini"]
        D5["💬 SSE Stream\nChat Response"]
    end

    A1 -->|"poll 5min"| B1
    A2 -->|"upload"| B1
    B1 --> B2 --> B3
    B3 --> C1
    B3 -->|"trigger"| B4
    B4 --> C2
    B3 --> C3

    D1 -->|"analytics"| D2
    D1 -->|"search"| D3
    D2 --> C1
    D3 --> C2
    D2 --> D4
    D3 --> D4
    D4 --> D5
```

---

## 3. 🤖 Chat Engine — Sequence Diagram

> [!IMPORTANT]
> **Pattern cốt lõi: Tool-First, LLM Assessment**
> - LLM **TUYỆT ĐỐI KHÔNG** tính toán số liệu
> - Python tool chạy SQL aggregation chính xác → LLM chỉ diễn giải

```mermaid
sequenceDiagram
    actor User as 👤 User
    participant UI as 💬 Chat UI
    participant API as ⚡ Chat API
    participant Conv as 💭 Conv Mgr
    participant Router as 🧭 Intent Router
    participant Tools as 🔧 Tool Executor
    participant DB as 🗃️ SQLite Analytics
    participant Vec as 🔮 Qdrant
    participant LLM as 🧠 Gemini

    User->>UI: "Tháng 8 có bao nhiêu phản hồi<br/>tiêu cực về đèn LED?"
    UI->>API: POST /api/chat/stream
    
    API->>Conv: load_session(user_id)
    Conv-->>API: history + slots

    API->>Router: classify(query, history)
    Router-->>API: intent=DRILL_PRODUCT<br/>date=2026-08, sentiment=Tiêu cực

    rect rgb(59, 130, 246, 0.1)
    Note over Tools,Vec: Tool-First Execution (Song song)
    API->>Tools: execute(DRILL_PRODUCT)
    
    par SQL Aggregation
        Tools->>DB: products(filter)
        DB-->>Tools: stats: [{AT04: 12}, {RLT02: 8}...]
    and Semantic Search
        Tools->>Vec: hybrid_search("đèn LED tiêu cực")
        Vec-->>Tools: top 5 feedback examples
    end
    end

    Tools-->>API: combined_results JSON

    rect rgb(249, 115, 22, 0.1)
    Note over LLM: LLM CHỈ diễn giải, KHÔNG tính số
    API->>LLM: system_prompt + tool_results + user_query
    LLM-->>UI: SSE Stream Response
    end

    Note over UI: 📊 Tổng: 47 phản hồi tiêu cực<br/>📊 Top: AT04(12), RLT02(8)<br/>💡 Tăng 23% so T7<br/>📌 VD: "Đèn AT04 bị chớp..."<br/>❓ Gợi ý: "Chi tiết AT04?"

    API->>Conv: save + compact_if_needed()
```

---

## 4. 🔍 Hybrid RAG Pipeline — Dense + BM25 + RRF

> Tham khảo kiến trúc **TLA HD V2**: Reciprocal Rank Fusion kết hợp ngữ nghĩa + keyword chính xác

```mermaid
flowchart TB
    subgraph ING["📥 Ingestion — Sau Classification"]
        I1["Classification\nPipeline Complete"]
        I2["SentenceTransformer\nvn-document-embedding\n768 dimensions"]
        I3["BM25 Tokenizer\nfastembed Sparse"]
        I4[("Qdrant Upsert\n+ Payload Metadata")]
        
        I1 --> I2
        I1 --> I3
        I2 -->|"Dense 768D"| I4
        I3 -->|"Sparse BM25"| I4
    end

    subgraph QRY["🔍 Query — Khi User Chat"]
        Q1["User Query:\n'đèn LED bị chớp nháy'"]
        Q2["Dense Encoder\n768D vector"]
        Q3["BM25 Encoder\nSparse vector"]
        
        Q1 --> Q2
        Q1 --> Q3
    end

    subgraph RET["🎯 Hybrid Retrieval"]
        R1["Prefetch Dense\nTop 20 candidates"]
        R2["Prefetch BM25\nTop 20 candidates"]
        R3["RRF Fusion\nReciprocal Rank"]
        R4["Payload Filter\ndate, unit, product\nsentiment, labels"]
        R5["Top K Results\nwith relevance scores"]
        
        Q2 --> R1
        Q3 --> R2
        R1 --> R3
        R2 --> R3
        R4 -.->|"filter"| R1
        R4 -.->|"filter"| R2
        R3 --> R5
    end

    subgraph POST["✨ Post-Processing"]
        E1["Cross-Encoder Rerank\nbge-reranker-base\nPhase 4 optional"]
        E2["Expand Context\nFull feedback + metadata"]
        E3["Format Citations\nFile: X Dòng: Y Ngày: Z"]
        
        R5 --> E1
        E1 --> E2
        E2 --> E3
    end
```

### Qdrant Vector Payload Schema

```mermaid
classDiagram
    class FeedbackVector {
        +int feedback_id
        +string source_file_key
        +string issue_date
        +string source
        +string unit_name
        +string product
        +string product_line
        +string sentiment
        +string[] labels
        +string major_group
        +string business_status
        +string content_preview
        ---
        dense: float[768]
        sparse_bm25: SparseVector
    }
```

---

## 5. 🗄️ Database Schema — ERD

> **Xanh** = bảng hiện có · **Cam** = bảng mới cho chatbot

```mermaid
erDiagram
    classification_jobs {
        text job_id PK
        text owner_username
        text filename
        text status
        int total_rows
        int rows_done
        float percent
        text created_at
    }

    feedback_records {
        int feedback_id PK
        text source_file_key
        text content
        text normalized_content
        text issue_date
        text source
        text unit_name
        text product
        text sentiment
        text classification_state
        int is_active
    }

    feedback_labels {
        int feedback_id FK
        text label
        text major_group
    }

    gemini_usage_log {
        int id PK
        text model
        text call_type
        int prompt_tokens
        int completion_tokens
        float cost_usd
    }

    chat_sessions {
        text session_id PK
        text user_id
        text title
        text active_domain
        text slots_json
        int token_count
        text expires_at
    }

    chat_messages {
        int message_id PK
        text session_id FK
        text role
        text content
        text metadata_json
        text created_at
    }

    embedding_queue {
        int queue_id PK
        int feedback_id FK
        text status
        text created_at
    }

    classification_jobs ||--o{ feedback_records : "produces"
    feedback_records ||--o{ feedback_labels : "has labels"
    feedback_records ||--o{ embedding_queue : "to embed"
    chat_sessions ||--o{ chat_messages : "contains"
    gemini_usage_log }o--|| classification_jobs : "tracks cost"
```

### Chi tiết 3 Bảng Mới

| Bảng | Mục đích | Rows ước tính |
|:--|:--|:--|
| `chat_sessions` | Phiên hội thoại, TTL 7 ngày, lưu active_domain + filter slots | ~100-500 |
| `chat_messages` | Tin nhắn user/assistant, metadata (tool_used, sources, latency) | ~1K-10K |
| `embedding_queue` | Hàng đợi batch embedding, status pending/done/error | ~10K-100K |

---

## 6. 🚀 Lộ Trình Triển Khai — 4 Giai Đoạn

```mermaid
gantt
    title DMS Chatbot — Implementation Roadmap
    dateFormat YYYY-MM-DD
    axisFormat %d/%m

    section 🔴 Phase 1 — Foundation
    DB chat tables              :p1a, 2026-09-15, 3d
    Chat API endpoint           :p1b, after p1a, 3d
    Intent Router 5 intents     :p1c, after p1a, 4d
    Tool-First connectors       :p1d, after p1c, 5d
    Response Builder            :p1e, after p1d, 3d
    Conversation Memory T1      :p1f, after p1b, 3d
    Follow-up Detection         :p1g, after p1f, 2d
    Integration Test            :p1t, after p1e, 3d

    section 🟠 Phase 2 — Semantic Search
    Qdrant Setup                :p2a, after p1t, 2d
    Embedding Model             :p2b, after p2a, 3d
    Ingestion Pipeline          :p2c, after p2b, 3d
    Hybrid Search RRF           :p2d, after p2c, 4d
    Search API                  :p2e, after p2d, 2d
    Similar Feedback            :p2f, after p2e, 2d

    section 🟡 Phase 3 — Reports
    SSE Streaming               :p3a, after p2f, 3d
    Report Generator            :p3b, after p3a, 4d
    Export DOCX Excel           :p3c, after p3b, 3d
    Report Templates            :p3d, after p3c, 2d
    Memory AI Summarize         :p3e, after p3a, 3d

    section ⚪ Phase 4 — Advanced
    Cross-Encoder Rerank        :p4a, after p3d, 3d
    Auto Reports Teams          :p4b, after p4a, 3d
    Anomaly Alerting            :p4c, after p4b, 4d
```

### Tổng kết Timeline

| Phase | Thời gian | Deliverables chính |
|:--|:-:|:--|
| 🔴 **Phase 1** | 2-3 tuần | Chat API + Intent Router + Tool-First + Memory → **chatbot hoạt động cơ bản** |
| 🟠 **Phase 2** | 1-2 tuần | Qdrant + Embedding + Hybrid Search → **tìm kiếm ngữ nghĩa trên feedback** |
| 🟡 **Phase 3** | 1-2 tuần | SSE Streaming + Report Generator + Export → **báo cáo tự động** |
| ⚪ **Phase 4** | Tùy chọn | Re-ranking + Auto Reports + Alerting → **nâng cao chất lượng** |

---

## 7. ⚖️ Ma Trận So Sánh → Quyết Định Thiết Kế

| Khía cạnh | 🔵 DMS (AS-IS) | 🟢 Ralli AI | 🟣 TLA HD | 🟠 **DMS Chatbot (TO-BE)** |
|:--|:--|:--|:--|:--|
| **Database** | SQLite WAL | MongoDB | PostgreSQL 16 | **SQLite WAL** ← giữ nguyên |
| **Vector DB** | ❌ | Qdrant 384D | Qdrant 768D+BM25 | **Qdrant 768D+BM25** ← TLA HD |
| **RAG** | BM25 product | Hybrid RRF | Hybrid V2+Rerank | **Hybrid RRF** → +Rerank P4 |
| **Chat Memory** | ❌ | 4-Tier Compact | 2-Tier RAM+DB | **3-Tier** ← hybrid cả hai |
| **Anti-Hallucination** | CoT prompt | Tool-First | Grounding Rules | **Tool-First + Grounding** |
| **Domain Router** | ❌ | 5 domains | Per-session | **15 intents** ← Ralli-inspired |
| **Report Gen** | Excel only | Excel+Zalo | DOCX+Excel | **Markdown+DOCX+Excel** |
| **Streaming** | WebSocket | HTTP stream | Polling | **SSE** ← modern standard |
| **Embedding** | ❌ | MiniLM 384D | vn-doc-embed 768D | **vn-doc-embed 768D** |
| **Token Budget** | Usage tracker | Per-user | Per-user+unit | **Per-user** ← existing tracker |

---

## 8. 🧠 Memory Architecture — 3 Tier Compaction

```mermaid
flowchart TB
    subgraph T1["Tier 1: Rule-Based Compact — Instant"]
        T1A["Khi lưu assistant response"]
        T1B["Giữ: kết luận + số liệu"]
        T1C["Bỏ: bảng dài, ví dụ thừa"]
        T1D["Target: dưới 500 tokens/turn"]
        T1A --> T1B --> T1C --> T1D
    end

    subgraph T2["Tier 2: AI Summarize — 70% threshold"]
        T2A["Total memory vượt 7K tokens"]
        T2B["Gemini tóm tắt turns cũ"]
        T2C["Giữ nguyên 3 turns gần nhất"]
        T2A --> T2B --> T2C
    end

    subgraph T3["Tier 3: FIFO Pruning — Emergency"]
        T3A["Vẫn vượt ngưỡng"]
        T3B["Drop oldest messages"]
        T3A --> T3B
    end

    T1 -->|"Nếu vẫn vượt"| T2
    T2 -->|"Nếu vẫn vượt"| T3
```

---

## 9. 🛡️ Anti-Hallucination Rules

> [!CAUTION]
> **5 Quy tắc Bất biến cho DMS Chatbot:**
>
> 1. **LLM TUYỆT ĐỐI KHÔNG tính số.** Mọi con số từ Tool → SQLite/Analytics Service.
> 2. **Mọi số liệu phải có nguồn.** `"123 phản hồi (analytics/overview, 01-31/08/2026)"`
> 3. **Không suy diễn ngoài dữ liệu.** → `"Dữ liệu này chưa có trong hệ thống."`
> 4. **Trích dẫn gốc chính xác.** Copy text từ DB, kèm `[File: X, Dòng: Y, Ngày: Z]`
> 5. **Phân biệt dữ liệu vs nhận định.** 📊 = số liệu thực, 💡 = nhận xét AI

---

## 10. 🔧 API Routes Mới

### Chat Endpoints (Phase 1)
| Method | Path | Mô tả |
|:--|:--|:--|
| `POST` | `/api/chat` | Gửi câu hỏi, nhận JSON response |
| `POST` | `/api/chat/stream` | SSE streaming response |
| `GET` | `/api/chat/sessions` | Danh sách phiên chat |
| `GET` | `/api/chat/sessions/{id}` | Chi tiết + lịch sử |
| `DELETE` | `/api/chat/sessions/{id}` | Xóa phiên |

### Search Endpoints (Phase 2)
| Method | Path | Mô tả |
|:--|:--|:--|
| `POST` | `/api/search/semantic` | Tìm feedback ngôn ngữ tự nhiên |
| `POST` | `/api/search/similar/{id}` | Phản hồi tương tự |
| `GET` | `/api/search/embedding-status` | Trạng thái indexing |

### Report Endpoints (Phase 3)
| Method | Path | Mô tả |
|:--|:--|:--|
| `POST` | `/api/reports/generate` | Tạo báo cáo LLM |
| `GET` | `/api/reports/{id}` | Lấy báo cáo |
| `POST` | `/api/reports/{id}/export` | Xuất DOCX/Excel |
