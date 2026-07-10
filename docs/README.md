# 📚 Tài liệu DMS Feedback Classification Service

**DMS Feedback Classification Service** là hệ thống tự động phân loại ý kiến phản hồi từ thị trường và đại lý của **Rạng Đông**. Hệ thống liên tục quét thư mục `Input/` trên SharePoint để phát hiện các file Excel mới, tự động ánh xạ từng phản hồi về sản phẩm chính xác bằng bộ khớp RAG (BM25 + RapidFuzz), rồi dùng mô hình ngôn ngữ lớn **Gemini 2.5 Flash Lite** để phân loại đa nhãn theo 21 nhãn chi tiết thuộc 8 nhóm lớn. Kết quả được ghi lại vào file Excel định dạng chuẩn và đẩy lên SharePoint `Output/`, đồng thời gửi báo cáo tức thì qua Microsoft Teams hoặc Email. Người dùng cũng có thể tương tác trực tiếp qua **Web Dashboard** với real-time streaming log và theo dõi tiến trình phân loại.

---

## 🗺️ Sơ đồ tổng quan hệ thống

```
┌─────────────────────────────────────────────────────────────────────┐
│                        SHAREPOINT ONLINE                            │
│   Input/ ──────────────────────────────────────► Output/            │
│   Keyword/ ◄──────────────────────────────────── Check_Point/       │
└─────────┬───────────────────────────────────────────────────────────┘
          │  Microsoft Graph API (poll mỗi 5 phút)
          ▼
┌─────────────────────────────┐
│      WATCHER DAEMON          │
│  ┌───────────────────────┐  │
│  │  ConfigSync (hot-     │  │
│  │  reload kw_map.json)  │  │
│  └───────────┬───────────┘  │
│              │               │
│  ┌───────────▼───────────┐  │
│  │   Pipeline Runner     │  │
│  │  ┌─────────────────┐  │  │
│  │  │ RAG Product     │  │  │
│  │  │ Matcher (BM25)  │  │  │
│  │  └────────┬────────┘  │  │
│  │  ┌────────▼────────┐  │  │
│  │  │ Issue Classifier│  │  │
│  │  │ (Gemini LLM)    │  │  │
│  │  └────────┬────────┘  │  │
│  │           │ 21 nhãn   │  │
│  └───────────▼───────────┘  │
│         OUTPUT EXCEL         │
└─────────────┬───────────────┘
              │ Kết quả + Cảnh báo
              ▼
┌─────────────────────────────┐    ┌──────────────────────────────────┐
│   MS Teams / Email           │    │         WEB DASHBOARD            │
│   Adaptive Cards             │    │                                  │
└─────────────────────────────┘    │  Người dùng ──► Trình duyệt      │
                                   │       │                           │
                                   │       ▼                           │
                                   │  FastAPI Web Server               │
                                   │  ├── REST API endpoints           │
                                   │  ├── WebSocket (log stream)       │
                                   │  └── WebSocket (progress)         │
                                   │       │                           │
                                   │       ▼                           │
                                   │  Pipeline Runner (manual job)     │
                                   │  SQLite Job Store (durable queue) │
                                   └──────────────────────────────────┘
```

---

## 📖 Điều hướng tài liệu

Chọn phần tài liệu phù hợp với vai trò của bạn:

### 👤 Người dùng nghiệp vụ

Dành cho nhân viên quản lý chất lượng, bộ phận dịch vụ khách hàng và quản trị dữ liệu — những người tải file lên và đọc kết quả phân loại.

| Tài liệu | Mô tả |
| :--- | :--- |
| [📘 Hướng dẫn sử dụng](USER_GUIDE.md) | Hướng dẫn toàn diện từ cài đặt, sử dụng Web Dashboard, đến đọc kết quả Excel |
| [📊 Định dạng file Excel](user/excel-format.md) | Quy cách cột bắt buộc, cột tuỳ chọn; cấu trúc file đầu vào và đầu ra |
| [🏷️ Tra cứu nhãn phân loại](user/labels-reference.md) | Định nghĩa chi tiết 21 nhãn thuộc 8 nhóm, kèm ví dụ feedback mẫu |

---

### 🔧 Quản trị viên

Dành cho kỹ sư DevOps/SysAdmin chịu trách nhiệm duy trì hạ tầng, cấu hình secrets và giám sát hệ thống.

| Tài liệu | Mô tả |
| :--- | :--- |
| [⚙️ Vận hành hệ thống](OPERATIONS.md) | Hướng dẫn triển khai Docker, cấu hình `.env`, lệnh Makefile và quản lý checkpoint |
| [🔗 Đồng bộ SharePoint](admin/sharepoint-sync.md) | Cấu hình Azure AD, quyền Graph API, cơ chế hot-reload cấu hình từ SharePoint |
| [📈 Giám sát & Metrics](admin/monitoring.md) | Theo dõi metrics hệ thống, cảnh báo Teams/Email, đọc log JSON Lines |
| [🛠️ Xử lý sự cố](admin/troubleshooting.md) | Chẩn đoán lỗi phổ biến, checklist khởi động, khôi phục checkpoint |

---

### 👨‍💻 Developer / Tích hợp

Dành cho lập trình viên muốn tích hợp API, đọc WebSocket hoặc mở rộng hệ thống.

| Tài liệu | Mô tả |
| :--- | :--- |
| [📐 Tài liệu kỹ thuật](TECHNICAL_DOCUMENT.md) | Kiến trúc chi tiết, codebase map, data models, pipeline internals |
| [🌐 Tổng quan API](api/overview.md) | Kiến trúc REST API, authentication, versioning, error codes |
| [📋 Danh sách endpoints](api/endpoints.md) | Tất cả REST endpoints với request/response schema mẫu |
| [🔌 WebSocket API](api/websocket.md) | Giao thức WebSocket log streaming và job progress |

---

## 🔗 Liên kết nhanh

| | |
| :--- | :--- |
| **Repository** | `https://github.com/ThanhDT127/dms-feedback-classification` |
| **Web Dashboard (local)** | `http://localhost:8000` |
| **API Docs (Swagger)** | `http://localhost:8000/docs` |
| **API Docs (ReDoc)** | `http://localhost:8000/redoc` |

---

*📅 Cập nhật lần cuối: tháng 7/2026*
