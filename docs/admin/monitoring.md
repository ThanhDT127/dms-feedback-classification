# 📈 Giám sát Gemini API

> **Phiên bản tài liệu:** 1.0 — Cập nhật: 2026-07-10
> **Áp dụng cho:** DMS Feedback Classification Service — Rang Dong
> **Quyền truy cập:** Chỉ tài khoản có vai trò **Admin**

---

## Mục lục

1. [Truy cập](#truy-cập)
2. [Bộ lọc thời gian](#bộ-lọc-thời-gian)
3. [Bốn thẻ thống kê](#bốn-thẻ-thống-kê)
4. [Biểu đồ token theo ngày](#biểu-đồ-token-theo-ngày)
5. [Bảng Top 10 file](#bảng-top-10-file)
6. [Nguồn dữ liệu](#nguồn-dữ-liệu)
7. [Lưu ý](#lưu-ý)

---

## Truy cập

Dashboard giám sát Gemini API được tích hợp ngay trong giao diện Web UI của DMS.

### Các bước truy cập

1. Đăng nhập vào Web UI bằng tài khoản **Admin**.
2. Chọn **tab Thống kê** (biểu tượng 📊) trên thanh điều hướng bên trái.
3. Cuộn xuống phần **🤖 Giám sát Gemini API**.

> **Lưu ý:** Mục **Giám sát Gemini API** chỉ hiển thị với người dùng có vai trò `admin`. Tài khoản thường sẽ không thấy phần này ngay cả khi truy cập đúng URL.

### Yêu cầu quyền

| Vai trò | Xem thống kê Gemini |
|---|---|
| `admin` | ✅ Có quyền |
| `user` (thông thường) | ❌ Không có quyền |
| Chưa đăng nhập | ❌ Chuyển hướng về trang Login |

---

## Bộ lọc thời gian

Tất cả số liệu trong dashboard đều phản ánh khoảng thời gian được chọn bởi bộ lọc. Có **4 chế độ lọc**:

```
┌─────────────┬──────────────┬──────────────┬─────────────────────────┐
│   Hôm nay   │   7 ngày     │   30 ngày    │      Tuỳ chọn           │
│  (Today)    │  (Last 7d)   │  (Last 30d)  │   (Custom Range)        │
└─────────────┴──────────────┴──────────────┴─────────────────────────┘
                                                    │
                                         ┌──────────┴──────────┐
                                         │  Từ ngày: [____]    │
                                         │  Đến ngày: [____]   │
                                         └─────────────────────┘
```

| Bộ lọc | Khoảng thời gian | Trường hợp sử dụng |
|---|---|---|
| **Hôm nay** | Từ 00:00 đến hiện tại (giờ server) | Kiểm tra hoạt động trong ngày |
| **7 ngày** | 7 ngày gần nhất tính từ hôm nay | Phân tích xu hướng tuần |
| **30 ngày** | 30 ngày gần nhất | Báo cáo chi phí tháng |
| **Tuỳ chọn** | Khoảng ngày do người dùng chọn | Kiểm tra sự kiện cụ thể |

Khi thay đổi bộ lọc, toàn bộ dashboard (thẻ thống kê, biểu đồ, bảng Top 10) sẽ tải lại tự động.

---

## Bốn thẻ thống kê

Phần đầu dashboard hiển thị 4 thẻ tóm tắt nhanh (summary cards) theo khoảng thời gian đã chọn:

```
┌────────────────────┐  ┌────────────────────┐  ┌────────────────────┐  ┌────────────────────┐
│   Số lần gọi API   │  │ Token đầu vào/ra   │  │  TB token/lần gọi  │  │  Chi phí ước tính  │
│                    │  │                    │  │                    │  │                    │
│       1,248        │  │  2.4M / 840K       │  │   1,924 / 673      │  │     $1.87 USD      │
│   API calls        │  │  input / output    │  │  input / output    │  │   estimated cost   │
└────────────────────┘  └────────────────────┘  └────────────────────┘  └────────────────────┘
```

### Thẻ 1 — Số lần gọi API

**Ý nghĩa:** Tổng số lần hệ thống gọi Gemini API trong khoảng thời gian đã chọn.

Hệ thống DMS thực hiện 2 loại gọi API:
- **`rag_extract`** — Truy vấn Gemini để trích xuất thông tin sản phẩm phù hợp từ dữ liệu RAG.
- **`classify_batch`** — Gửi batch phản hồi đến Gemini để phân loại nhãn.

Con số này phản ánh tổng cộng cả hai loại gọi. Nếu số lần gọi tăng đột biến mà không có file mới, cần kiểm tra xem có job bị retry loop hay không.

### Thẻ 2 — Token đầu vào / đầu ra

**Ý nghĩa:**
- **Token đầu vào (input tokens):** Số token trong prompt gửi đến Gemini — bao gồm system prompt, dữ liệu RAG, và nội dung phản hồi cần phân loại.
- **Token đầu ra (output tokens):** Số token trong kết quả nhận về từ Gemini — bao gồm nhãn phân loại và giải thích.

Token đầu vào thường chiếm **70–85%** tổng token vì prompt và dữ liệu RAG khá dài. Tỷ lệ này giúp xác định liệu cần tối ưu prompt hay dữ liệu RAG đầu vào.

### Thẻ 3 — TB token / lần gọi

**Ý nghĩa:** Số token trung bình mỗi lần gọi API, hiển thị riêng cho input và output.

Thẻ này giúp **phát hiện bất thường**:
- Nếu TB token đầu vào tăng đột ngột → có thể dữ liệu RAG hoặc prompt template bị thay đổi không mong muốn.
- Nếu TB token đầu ra tăng → Gemini đang sinh kết quả dài hơn bình thường, có thể do lỗi cấu hình model.

**Ngưỡng tham khảo (bình thường):**
| Loại call | Input TB | Output TB |
|---|---|---|
| `rag_extract` | ~800–1,200 tokens | ~100–300 tokens |
| `classify_batch` | ~1,500–3,000 tokens | ~200–600 tokens |

### Thẻ 4 — Chi phí ước tính

**Ý nghĩa:** Ước tính chi phí sử dụng Gemini API trong kỳ, tính bằng USD.

**Công thức tính:**

```
Chi phí = (Input tokens × Giá input / 1,000,000)
        + (Output tokens × Giá output / 1,000,000)
```

Giá theo model (ví dụ Gemini 1.5 Flash):
- Input: $0.075 / 1M tokens
- Output: $0.30 / 1M tokens

> ⚠️ **Lưu ý:** Đây là **ước tính** dựa trên số token được ghi nhận. Chi phí thực tế theo Google Cloud Billing có thể khác do làm tròn, thuế, và chính sách giá theo thời điểm.

---

## Biểu đồ token theo ngày

Biểu đồ **Stacked Bar Chart** hiển thị lượng token tiêu thụ theo từng ngày trong khoảng thời gian đã chọn.

```
Token theo ngày (Stacked Bar Chart)

  Tokens
  (K)
   │
 5 ┤               ████
 4 ┤          ████ ████
 3 ┤     ████ ████ ████ ████
 2 ┤████ ████ ████ ████ ████ ████
 1 ┤████ ████ ████ ████ ████ ████
   └────────────────────────────────────
     07/04 07/05 07/06 07/07 07/08 07/09

   ■ Input tokens (xanh dương)
   ■ Output tokens (xanh lá)
```

### Cách đọc biểu đồ

- **Cột xanh dương (dưới):** Token đầu vào — phản ánh khối lượng dữ liệu gửi đến Gemini mỗi ngày.
- **Cột xanh lá (trên):** Token đầu ra — phản ánh khối lượng kết quả nhận về.
- **Ngày không có cột:** Không có job nào được xử lý trong ngày đó.
- **Cột cao bất thường:** Ngày có nhiều file lớn hoặc file có nội dung dài được xử lý.

Di chuột vào từng cột để xem số liệu chi tiết (tooltip).

---

## Bảng Top 10 file

Bảng liệt kê **10 file tiêu thụ nhiều token nhất** trong khoảng thời gian đã chọn.

| # | Tên file | Input tokens | Output tokens | Tổng tokens | Số lần gọi | Thời gian xử lý |
|---|---|---|---|---|---|---|
| 1 | feedback_thang7_full.xlsx | 245,320 | 82,140 | 327,460 | 48 | 2026-07-08 09:14 |
| 2 | khieu_nai_q2.xlsx | 198,750 | 64,230 | 262,980 | 36 | 2026-07-07 14:22 |
| ... | ... | ... | ... | ... | ... | ... |

### Cột trong bảng

| Cột | Ý nghĩa |
|---|---|
| **Tên file** | Tên file `.xlsx` gốc được xử lý |
| **Input tokens** | Tổng token đầu vào cho tất cả lần gọi của file này |
| **Output tokens** | Tổng token đầu ra cho tất cả lần gọi của file này |
| **Tổng tokens** | Input + Output |
| **Số lần gọi** | Tổng số API call (`rag_extract` + `classify_batch`) |
| **Thời gian xử lý** | Thời điểm file bắt đầu được xử lý |

### Sử dụng bảng để phát hiện bất thường

- File đứng đầu nhưng không phải file lớn nhất → kiểm tra prompt hoặc cấu hình model.
- File lặp lại nhiều lần → có thể job bị retry loop, cần kiểm tra log.
- File có số lần gọi rất cao so với file khác → kiểm tra số dòng và batch size.

---

## Nguồn dữ liệu

Mọi số liệu trong dashboard đều được lấy từ cơ sở dữ liệu SQLite cục bộ của service.

### Cơ sở dữ liệu

```
d:\Works\DMS\
└── data\
    └── gemini_usage_log.db    ← SQLite database lưu lịch sử sử dụng Gemini
```

### Bảng dữ liệu chính

```sql
CREATE TABLE gemini_usage_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT NOT NULL,        -- Thời điểm gọi API (ISO 8601 UTC)
    call_type     TEXT NOT NULL,        -- 'rag_extract' hoặc 'classify_batch'
    file_name     TEXT,                 -- Tên file đang xử lý (nếu có)
    model_name    TEXT NOT NULL,        -- Tên model Gemini được dùng
    input_tokens  INTEGER NOT NULL,     -- Số token đầu vào
    output_tokens INTEGER NOT NULL,     -- Số token đầu ra
    total_tokens  INTEGER NOT NULL,     -- input_tokens + output_tokens
    cost_usd      REAL                  -- Chi phí ước tính (USD)
);
```

### Hai loại API call được ghi nhận

| Loại call | Khi nào xảy ra | Đặc điểm |
|---|---|---|
| `rag_extract` | Khi pipeline thực hiện RAG product matching | Input tokens cao hơn do kèm theo dữ liệu sản phẩm |
| `classify_batch` | Khi Gemini phân loại batch phản hồi | Nhiều lần gọi hơn, tùy thuộc vào số dòng và batch size |

Mỗi API call, dù thành công hay thất bại, đều được cố gắng ghi vào `gemini_usage_log`. Nếu ghi thất bại (ví dụ: DB bị lock), lỗi sẽ được ghi vào system log nhưng không ảnh hưởng đến pipeline.

---

## Lưu ý

> ⚠️ **Chi phí là ước tính, không phải chi phí thực tế**
> 
> Số liệu "Chi phí ước tính" trong dashboard được tính dựa trên token count và đơn giá tham khảo tại thời điểm xây dựng hệ thống. Chi phí thực tế có thể khác do:
> - Google thay đổi bảng giá Gemini API.
> - Chính sách miễn phí hoặc quota theo gói dịch vụ.
> - Thuế và phí xử lý thanh toán.
> 
> Luôn kiểm tra **Google Cloud Billing** để có số liệu chính xác.

> 📌 **Dữ liệu lịch sử không bị xóa tự động**
> 
> `gemini_usage_log.db` tích lũy dữ liệu theo thời gian và không có cơ chế tự xóa. Nếu cần quản lý dung lượng, admin có thể thực hiện backup và truncate thủ công.

> 🔒 **Quyền truy cập dashboard**
> 
> Dashboard này chứa thông tin nhạy cảm về chi phí và hiệu suất. Chỉ chia sẻ tài khoản admin với người có trách nhiệm quản lý hệ thống.
