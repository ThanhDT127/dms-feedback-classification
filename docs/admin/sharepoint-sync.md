# ☁️ Đồng bộ SharePoint

> **Phiên bản tài liệu:** 1.0 — Cập nhật: 2026-07-10
> **Áp dụng cho:** DMS Feedback Classification Service — Rang Dong

---

## Mục lục

1. [Tổng quan luồng](#tổng-quan-luồng)
2. [Các thư mục SharePoint](#các-thư-mục-sharepoint)
3. [Cơ chế seen_files.json](#cơ-chế-seen_filesjson)
4. [Đồng bộ thủ công](#đồng-bộ-thủ-công)
5. [Cấu hình](#cấu-hình)
6. [Lỗi thường gặp](#lỗi-thường-gặp)

---

## Tổng quan luồng

Dịch vụ Watcher chạy nền, định kỳ kiểm tra thư mục `Input/` trên SharePoint và xử lý các file Excel mới theo pipeline phân loại tự động.

```
╔══════════════════════════════════════════════════════════════════════════╗
║                     LUỒNG ĐỒNG BỘ SHAREPOINT                           ║
╚══════════════════════════════════════════════════════════════════════════╝

  ┌─────────────────────┐
  │  SharePoint Input/  │  ← Người dùng tải file .xlsx lên đây
  └──────────┬──────────┘
             │  Poll mỗi 300 giây
             ▼
  ┌─────────────────────┐
  │   Watcher Service   │  ← Kiểm tra file mới (chưa có trong seen_files.json)
  └──────────┬──────────┘
             │  Download .xlsx về local temp
             ▼
  ┌─────────────────────┐
  │  seen_files.json    │  ← Ghi nhận file đã xử lý (tránh trùng lặp)
  └──────────┬──────────┘
             │
             ▼
  ┌─────────────────────┐
  │  Pipeline Classify  │  ← RAG product matching → classify_batch → Gemini
  └──────────┬──────────┘
             │
      ┌──────┴───────┐
      ▼              ▼
┌──────────┐  ┌─────────────────┐
│ Output/  │  │ Check_Point/    │  ← Kết quả & checkpoint ghi lên SharePoint
│  .xlsx   │  │  state files    │
└──────────┘  └─────────────────┘
```

**Chu kỳ poll mặc định:** 300 giây (5 phút). Có thể điều chỉnh qua biến môi trường `POLL_INTERVAL_SECONDS`.

---

## Các thư mục SharePoint

| Thư mục | Mục đích | Hành động tự động |
|---|---|---|
| `Input/` | Chứa các file `.xlsx` đầu vào cần phân loại | Watcher đọc và download file mới về server |
| `Output/` | Lưu kết quả phân loại đã hoàn thành | Watcher upload file kết quả lên sau khi pipeline chạy xong |
| `Check_Point/` | Lưu trạng thái checkpoint của từng job | Watcher ghi checkpoint để có thể resume nếu bị gián đoạn |
| `Keyword/` | Chứa file cấu hình từ khóa phân loại | Watcher đọc và sync về local khi `ENABLE_SHAREPOINT_CONFIG_SYNC=true` |
| `Model/` | Chứa file cấu hình model (prompt template, tham số) | Watcher đọc và sync về local khi `ENABLE_SHAREPOINT_CONFIG_SYNC=true` |

### Ghi chú về thư mục Input/

- Chỉ các file có phần mở rộng `.xlsx` mới được xử lý.
- File có kích thước nhỏ hơn **1 KB** sẽ bị bỏ qua tự động (coi là file rỗng hoặc bị lỗi).
- Tên file phải là duy nhất; nếu trùng tên với file đã xử lý trong `seen_files.json`, file sẽ bị bỏ qua.

### Ghi chú về thư mục Keyword/ và Model/

Khi `ENABLE_SHAREPOINT_CONFIG_SYNC=true`, mỗi lần Watcher poll thành công, hệ thống sẽ:
1. Liệt kê các file trong `Keyword/` và `Model/` trên SharePoint.
2. So sánh với phiên bản local.
3. Download phiên bản mới hơn nếu có thay đổi.

---

## Cơ chế seen_files.json

### Vai trò

`seen_files.json` là cơ chế bảo vệ chính để tránh **xử lý trùng lặp** cùng một file nhiều lần. Mỗi file `.xlsx` sau khi được Watcher phát hiện và đưa vào pipeline, ID của file sẽ được ghi vào `seen_files.json`.

### Vị trí lưu trữ

```
d:\Works\DMS\
└── data\
    └── seen_files.json        ← Mặc định (có thể cấu hình lại)
```

### Cấu trúc file

```json
{
  "01BXY3ABCDEF1234567890": "2026-07-09T08:32:11.452Z",
  "01BXY3GHIJKL0987654321": "2026-07-09T14:17:05.821Z"
}
```

Trong đó:
- **Key** = SharePoint Item ID hoặc Drive Item ID của file
- **Value** = Thời điểm hệ thống phát hiện và bắt đầu xử lý file (ISO 8601 UTC)

### Khi nào file bị bỏ qua?

```
Watcher poll → liệt kê file trong Input/ → kiểm tra ID trong seen_files.json
                                                        │
                                           ┌────────────┴────────────┐
                                           ▼                         ▼
                                    ID đã tồn tại              ID chưa có
                                    → BỎ QUA (skip)            → XỬ LÝ (process)
                                                                → Ghi ID vào seen_files.json
```

### Xóa thủ công để xử lý lại

Nếu cần xử lý lại một file đã được ghi nhận (ví dụ: pipeline lỗi giữa chừng):

1. Mở file `data/seen_files.json` bằng bất kỳ trình soạn thảo nào.
2. Xóa dòng chứa ID của file cần xử lý lại.
3. Lưu file.
4. Watcher sẽ phát hiện lại file trong chu kỳ poll tiếp theo.

> **Lưu ý:** Không nên xóa toàn bộ `seen_files.json` trừ khi muốn xử lý lại tất cả file trong `Input/`.

---

## Đồng bộ thủ công

Ngoài chu kỳ tự động, người dùng có thể kích hoạt đồng bộ theo yêu cầu từ giao diện Web UI.

### Các bước thực hiện

1. Đăng nhập vào Web UI (mặc định: `http://localhost:8000`).
2. Chọn **tab Files** (biểu tượng 📁) trên thanh điều hướng bên trái.
3. Tìm nút **"Đồng bộ SharePoint"** ở góc trên bên phải của giao diện.
4. Nhấn nút → hệ thống sẽ gửi yêu cầu đồng bộ ngay lập tức, không cần chờ đến chu kỳ 300 giây tiếp theo.
5. Trạng thái đồng bộ sẽ hiển thị trên giao diện (thành công / thất bại).

### Khi nào nên đồng bộ thủ công?

| Tình huống | Lý do |
|---|---|
| Vừa tải file mới lên SharePoint và muốn xử lý ngay | Không muốn chờ tối đa 5 phút |
| Vừa cập nhật file cấu hình trong `Keyword/` hoặc `Model/` | Áp dụng cấu hình mới ngay lập tức |
| Sau khi khắc phục lỗi kết nối SharePoint | Đồng bộ lại sau khi creds được cập nhật |

---

## Cấu hình

Các biến môi trường liên quan đến đồng bộ SharePoint, thường được khai báo trong file `.env` hoặc `docker-compose.yml`:

| Biến môi trường | Mặc định | Mô tả |
|---|---|---|
| `POLL_INTERVAL_SECONDS` | `300` | Thời gian (giây) giữa các lần Watcher poll SharePoint. Giảm xuống để xử lý nhanh hơn, tăng lên để giảm tải API. |
| `UPLOAD_INPUT_TO_SHAREPOINT` | `false` | Nếu `true`, sau khi phân loại xong, file kết quả sẽ được upload tự động lên thư mục `Output/` trên SharePoint. |
| `ENABLE_SHAREPOINT_CONFIG_SYNC` | `true` | Nếu `true`, Watcher sẽ tự đồng bộ file cấu hình từ `Keyword/` và `Model/` về local mỗi chu kỳ poll. |
| `SHAREPOINT_DRIVE_ID` | _(bắt buộc)_ | Drive ID của SharePoint document library. Sai giá trị này sẽ khiến Watcher không tìm thấy file. |
| `AZURE_CLIENT_ID` | _(bắt buộc)_ | Client ID của Azure App Registration dùng để xác thực với Microsoft Graph API. |
| `AZURE_CLIENT_SECRET` | _(bắt buộc)_ | Client Secret của Azure App Registration. Hết hạn hoặc sai sẽ gây lỗi 401. |
| `AZURE_TENANT_ID` | _(bắt buộc)_ | Tenant ID của tổ chức trên Azure Active Directory. |

### Ví dụ cấu hình trong .env

```env
# SharePoint sync
POLL_INTERVAL_SECONDS=300
UPLOAD_INPUT_TO_SHAREPOINT=true
ENABLE_SHAREPOINT_CONFIG_SYNC=true
SHAREPOINT_DRIVE_ID=b!xYzAbCdEfGhIjKlMnOpQrS

# Azure credentials
AZURE_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
AZURE_CLIENT_ID=yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy
AZURE_CLIENT_SECRET=your-client-secret-here
```

---

## Lỗi thường gặp

### ❌ Lỗi 401 Unauthorized khi kết nối SharePoint

**Triệu chứng:** Log xuất hiện `401 Unauthorized` hoặc `Authentication failed` khi Watcher thực hiện poll.

**Nguyên nhân:**
- `AZURE_CLIENT_SECRET` đã hết hạn (Azure App Registration secret có thời hạn).
- `AZURE_CLIENT_ID` hoặc `AZURE_TENANT_ID` bị sai.
- App Registration chưa được cấp quyền `Files.ReadWrite.All` trên Microsoft Graph.

**Xử lý:**
1. Vào Azure Portal → App Registrations → chọn app → Certificates & Secrets.
2. Tạo Client Secret mới, sao chép giá trị.
3. Cập nhật `AZURE_CLIENT_SECRET` trong file `.env`.
4. Restart service: `docker-compose restart` hoặc khởi động lại process Watcher.

---

### ❌ File không được phát hiện

**Triệu chứng:** Đã tải file lên SharePoint `Input/` nhưng Watcher không xử lý sau nhiều chu kỳ poll.

**Nguyên nhân:**
- File không nằm trực tiếp trong thư mục `Input/` (nằm trong thư mục con).
- `SHAREPOINT_DRIVE_ID` sai, Watcher đang poll sai Drive.
- File không có phần mở rộng `.xlsx`.

**Xử lý:**
1. Xác nhận file nằm trực tiếp trong `Input/` (không phải subfolder).
2. Kiểm tra `SHAREPOINT_DRIVE_ID` bằng cách vào Microsoft Graph Explorer: `GET /drives/{id}/root/children`.
3. Đổi tên file sang `.xlsx` nếu cần.
4. Kiểm tra log: `docker-compose logs watcher --tail=50`.

---

### ❌ File bị skip (bỏ qua) mà không xử lý

**Triệu chứng:** File có trong `Input/`, chưa có trong `seen_files.json`, nhưng vẫn bị bỏ qua.

**Nguyên nhân:**
- Kích thước file nhỏ hơn **1 KB** — hệ thống coi là file rỗng, bị lỗi, hoặc file test.

**Xử lý:**
1. Kiểm tra kích thước file trên SharePoint.
2. Nếu file thực sự có dữ liệu nhưng < 1KB, kiểm tra lại quá trình xuất file Excel.
3. Xem log để xác nhận lý do skip: tìm dòng `Skipping file` kèm tên file.

---

> 💡 **Tip:** Để theo dõi hoạt động Watcher theo thời gian thực, truy cập tab **Thống kê → Nhật ký hệ thống** trong Web UI hoặc xem WebSocket stream tại `/ws/logs`.
