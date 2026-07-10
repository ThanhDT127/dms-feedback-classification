# 🔧 Xử lý Sự cố

> **Phiên bản tài liệu:** 1.0 — Cập nhật: 2026-07-10
> **Áp dụng cho:** DMS Feedback Classification Service — Rang Dong

---

## Mục lục

1. [Giới thiệu](#giới-thiệu)
2. [SharePoint: Lỗi 401/403](#1-sharepoint-lỗi-401403)
3. [File không được phát hiện](#2-file-không-được-phát-hiện)
4. [File bị bỏ qua (skip)](#3-file-bị-bỏ-qua-skip)
5. [Job bị stuck ở trạng thái running](#4-job-bị-stuck-ở-trạng-thái-running)
6. [WebSocket mất kết nối](#5-websocket-mất-kết-nối)
7. [Watcher không sync config](#6-watcher-không-sync-config)
8. [Phân loại sai nhãn](#7-phân-loại-sai-nhãn)
9. [Chi phí Gemini tăng bất thường](#8-chi-phí-gemini-tăng-bất-thường)
10. [Đăng nhập hết hạn](#9-đăng-nhập-hết-hạn)
11. [Xem logs](#xem-logs)

---

## Giới thiệu

Tài liệu này hướng dẫn chẩn đoán và xử lý các sự cố thường gặp trong hệ thống DMS Feedback Classification Service theo mẫu chuẩn:

```
┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│   TRIỆU CHỨNG    │ →  │   NGUYÊN NHÂN    │ →  │    XỬ LÝ         │
│  (Bạn thấy gì?)  │    │  (Vì sao vậy?)   │    │  (Làm gì?)       │
└──────────────────┘    └──────────────────┘    └──────────────────┘
```

### Trước khi bắt đầu

Trước khi xử lý bất kỳ sự cố nào, hãy kiểm tra:

1. **System logs** — Tab Thống kê → Nhật ký hệ thống trong Web UI, hoặc `docker-compose logs --tail=100`.
2. **Trạng thái service** — Cả hai process `watcher` và `web` có đang chạy không?
3. **Kết nối mạng** — Server có kết nối được internet để gọi SharePoint và Gemini API không?

---

## 1. SharePoint: Lỗi 401/403

### Triệu chứng

```
[ERROR] SharePointClient: HTTP 401 Unauthorized — token acquisition failed
[ERROR] Failed to list files in Input/: 403 Forbidden
```

Watcher ngừng poll SharePoint. Không có file mới nào được xử lý dù đã tải lên.

### Nguyên nhân

| Nguyên nhân | Cách nhận biết |
|---|---|
| `AZURE_CLIENT_SECRET` hết hạn | Log chứa `AADSTS7000215` hoặc `invalid_client` |
| `AZURE_CLIENT_ID` sai | Log chứa `AADSTS700016` hoặc `Application not found` |
| `AZURE_TENANT_ID` sai | Log chứa `AADSTS90002` hoặc `Tenant not found` |
| App chưa được cấp quyền `Files.ReadWrite.All` | HTTP 403 (thay vì 401) sau khi auth thành công |

### Xử lý

**Bước 1 — Kiểm tra và tái tạo Client Secret:**

```powershell
# Không có CLI trực tiếp; thực hiện trên Azure Portal:
# Azure Portal → Azure Active Directory → App registrations
# → Chọn app DMS → Certificates & secrets → + New client secret
# → Đặt thời hạn phù hợp → Sao chép value (chỉ hiện 1 lần)
```

**Bước 2 — Cập nhật biến môi trường:**

```env
# .env
AZURE_CLIENT_SECRET=new-secret-value-here
```

**Bước 3 — Kiểm tra quyền App Registration (nếu lỗi 403):**

```
Azure Portal → App registrations → [App DMS]
→ API permissions → Add a permission → Microsoft Graph
→ Application permissions → Files.ReadWrite.All
→ Grant admin consent
```

**Bước 4 — Restart service:**

```powershell
docker-compose restart watcher
# Hoặc nếu chạy trực tiếp:
# Dừng process watcher và khởi động lại
```

**Bước 5 — Xác nhận:** Chờ 30 giây, kiểm tra log lại — không còn thấy lỗi 401/403.

---

## 2. File không được phát hiện

### Triệu chứng

Đã tải file `.xlsx` lên SharePoint thư mục `Input/`, chờ nhiều hơn 5 phút (1 chu kỳ poll), nhưng file không được xử lý và không xuất hiện trong danh sách job.

### Nguyên nhân

| Nguyên nhân | Cách nhận biết |
|---|---|
| File nằm trong thư mục con của `Input/` | Watcher chỉ quét 1 cấp thư mục, không quét đệ quy |
| `SHAREPOINT_DRIVE_ID` trỏ sai Drive | Log poll thành công nhưng không thấy file |
| File không có phần mở rộng `.xlsx` | Watcher lọc theo đuôi file |
| File đã có trong `seen_files.json` (ID trùng) | Log xuất hiện `Already seen, skipping` |

### Xử lý

**Kiểm tra vị trí file:**

Vào SharePoint Online → Documents → tìm thư mục `Input/` → xác nhận file nằm trực tiếp trong `Input/`, không phải subfolder.

**Kiểm tra Drive ID:**

```powershell
# Dùng Microsoft Graph Explorer (https://developer.microsoft.com/en-us/graph/graph-explorer)
# GET https://graph.microsoft.com/v1.0/drives/{SHAREPOINT_DRIVE_ID}/root/children
# Nếu trả về 404 → Drive ID sai
```

**Kiểm tra seen_files.json:**

```powershell
# Xem nội dung seen_files.json
Get-Content "data\seen_files.json" | ConvertFrom-Json

# Nếu muốn xóa 1 entry để xử lý lại file:
$json = Get-Content "data\seen_files.json" | ConvertFrom-Json
$json.PSObject.Properties.Remove("ITEM_ID_CAN_XOA")
$json | ConvertTo-Json | Set-Content "data\seen_files.json" -Encoding UTF8
```

**Kiểm tra log Watcher:**

```powershell
docker-compose logs watcher --tail=50
# Tìm dòng chứa tên file hoặc "Polling Input/"
```

---

## 3. File bị bỏ qua (skip)

### Triệu chứng

```
[WARNING] Skipping file "test_file.xlsx": size 512 bytes < minimum 1024 bytes
```

File có trong `Input/`, chưa có trong `seen_files.json`, nhưng Watcher bỏ qua và không tạo job.

### Nguyên nhân

Hệ thống có ngưỡng **kích thước tối thiểu 1 KB (1,024 bytes)**. File nhỏ hơn ngưỡng này bị coi là:
- File rỗng (Excel mới tạo chưa có dữ liệu)
- File bị lỗi khi xuất
- File test/placeholder

### Xử lý

1. Kiểm tra kích thước file trên SharePoint.
2. Mở file trực tiếp để xác nhận có dữ liệu thực sự.
3. Nếu file hợp lệ nhưng < 1KB (hiếm gặp), kiểm tra lại quá trình xuất file từ hệ thống nguồn — có thể thiếu dữ liệu hoặc export bị lỗi.
4. Thay thế bằng file có đủ dữ liệu và tải lên lại.

> **Lưu ý:** File bị skip vẫn có thể được ghi vào `seen_files.json` để tránh log spam. Nếu muốn thử lại sau khi thay file, xóa ID của file cũ khỏi `seen_files.json`.

---

## 4. Job bị stuck ở trạng thái running

### Triệu chứng

Job hiển thị trạng thái `running` trong danh sách Jobs trên Web UI, nhưng không có tiến trình mới trong nhiều phút (hoặc hàng giờ). Không có log mới liên quan đến job này.

### Nguyên nhân

| Nguyên nhân | Mô tả |
|---|---|
| Worker process bị crash | Process xử lý job dừng đột ngột (OOM, lỗi unhandled exception) |
| Gemini API timeout lâu | Gọi API bị treo, không có timeout được set đủ ngắn |
| Database lock | SQLite bị lock khiến worker không thể cập nhật trạng thái job |
| File Excel bị corrupt | Pipeline xử lý file bị block ở bước đọc file |

### Xử lý

**Bước 1 — Xác nhận job bị stuck:**

```
Web UI → Tab Jobs → Tìm job với status "running"
→ Ghi lại Job ID và thời gian bắt đầu
→ Nếu chạy > 30 phút mà không có log mới → Xác nhận stuck
```

**Bước 2 — Restart web service:**

```powershell
docker-compose restart web
```

Sau khi restart, job sẽ tự động chuyển về trạng thái `queued` và được xử lý lại bởi worker mới.

**Bước 3 — Kiểm tra nguyên nhân gốc:**

```powershell
# Xem log trước thời điểm crash
docker-compose logs web --tail=200 | Select-String "ERROR|CRITICAL|Exception"
```

**Bước 4 — Nếu job tiếp tục stuck sau khi restart:**

1. Kiểm tra file Excel gốc — thử mở trực tiếp bằng Excel để xác nhận không bị corrupt.
2. Kiểm tra kết nối Gemini API — có thể Gemini đang gặp sự cố dịch vụ.
3. Kiểm tra dung lượng đĩa — SQLite cần không gian để ghi.

---

## 5. WebSocket mất kết nối

### Triệu chứng

Trên Web UI:
- Biểu tượng kết nối WebSocket hiển thị màu đỏ hoặc thông báo "Disconnected".
- Log realtime trong tab Thống kê → Nhật ký hệ thống ngừng cập nhật.
- Tiến trình job không còn được cập nhật tự động, cần F5 để tải lại.

### Nguyên nhân

| Nguyên nhân | Mô tả |
|---|---|
| Network timeout | Kết nối không hoạt động lâu → proxy/load balancer đóng kết nối |
| Server restart | Web service được restart, WebSocket server ngừng tạm thời |
| Trình duyệt sleep tab | Tab không hoạt động lâu → trình duyệt suspend kết nối WebSocket |

### Xử lý

**Tự động:** Giao diện Web UI có cơ chế **tự động reconnect** khi phát hiện WebSocket đứt. Hệ thống sẽ thử kết nối lại sau 2–5 giây. Không cần thao tác thủ công trong hầu hết trường hợp.

**Nếu không tự reconnect sau 30 giây:**
1. Nhấn **F5** để tải lại trang.
2. Đảm bảo session chưa hết hạn (xem [Mục 9 — Đăng nhập hết hạn](#9-đăng-nhập-hết-hạn)).

**Endpoint WebSocket:**

```
/ws/logs          ← Stream log hệ thống realtime
/ws/jobs/{id}     ← Stream trạng thái và tiến trình của job cụ thể
```

**Kiểm tra server WebSocket:**

```powershell
# Kiểm tra service web đang chạy
docker-compose ps web
# Phải hiển thị status "Up"
```

---

## 6. Watcher không sync config

### Triệu chứng

Thay đổi file cấu hình trong thư mục `Keyword/` hoặc `Model/` trên SharePoint nhưng hệ thống vẫn dùng cấu hình cũ sau nhiều chu kỳ poll.

### Nguyên nhân

| Nguyên nhân | Cách nhận biết |
|---|---|
| `ENABLE_SHAREPOINT_CONFIG_SYNC=false` | Watcher không bao giờ sync config |
| Thư mục `Keyword/` hoặc `Model/` trống trên SharePoint | Log: `No config files found in Keyword/` |
| File config trên SP cùng tên/nội dung với local | Watcher thấy không có thay đổi, không download lại |
| Watcher không có quyền đọc folder `Keyword/` hoặc `Model/` | Log 403 khi truy cập folder cụ thể |

### Xử lý

**Bước 1 — Kiểm tra cấu hình:**

```env
# Trong .env, đảm bảo:
ENABLE_SHAREPOINT_CONFIG_SYNC=true
```

**Bước 2 — Tải file lên SharePoint:**

1. Vào SharePoint → Documents → `Keyword/` (hoặc `Model/`).
2. Xác nhận file cấu hình đã được tải lên đúng thư mục.
3. Đảm bảo tên file khớp với những gì hệ thống mong đợi.

**Bước 3 — Kích hoạt sync thủ công:**

Web UI → Tab Files → Nút "Đồng bộ SharePoint"

**Bước 4 — Xem log sync:**

```powershell
docker-compose logs watcher --tail=30 | Select-String "sync|config|Keyword|Model"
```

---

## 7. Phân loại sai nhãn

### Triệu chứng

Kết quả phân loại trong file Output/ có nhiều nhãn không chính xác — phản hồi bị gán sai danh mục hoặc sản phẩm.

### Nguyên nhân

| Nguyên nhân | Mô tả |
|---|---|
| File Keyword/ lỗi thời | Từ khóa không phản ánh đúng ngôn ngữ khách hàng hiện tại |
| Prompt template trong Model/ cần cập nhật | Hướng dẫn phân loại chưa đủ rõ ràng cho các trường hợp mới |
| Dữ liệu RAG thiếu sản phẩm mới | Sản phẩm mới chưa có trong dữ liệu tham chiếu |
| Batch size quá lớn | Gemini context bị loãng khi xử lý quá nhiều dòng cùng lúc |

### Xử lý

**Cập nhật từ khóa:**

1. Mở file keyword hiện tại từ thư mục `Keyword/` trên SharePoint.
2. Bổ sung hoặc sửa từ khóa cho các danh mục bị phân loại sai.
3. Tải file đã chỉnh sửa lên lại SharePoint `Keyword/`.
4. Kích hoạt đồng bộ thủ công từ Web UI hoặc chờ chu kỳ poll tiếp theo.
5. Chạy lại job với file phản hồi cần kiểm tra.

**Cập nhật prompt template:**

1. Tải file cấu hình model từ `Model/` trên SharePoint.
2. Chỉnh sửa phần `system_prompt` hoặc `few_shot_examples` để cải thiện hướng dẫn phân loại.
3. Tải file lên lại.
4. Sync và chạy lại job để kiểm tra kết quả.

> **Tip:** Nên so sánh nhãn dự đoán vs. nhãn đúng trên 20–30 mẫu trước khi thay đổi cấu hình diện rộng.

---

## 8. Chi phí Gemini tăng bất thường

### Triệu chứng

Dashboard **Giám sát Gemini API** (Tab Thống kê) hiển thị chi phí ước tính tăng đột biến so với các ngày/tuần trước mà không có lý do rõ ràng.

### Nguyên nhân

| Nguyên nhân | Dấu hiệu |
|---|---|
| File kích thước lớn bất thường được xử lý | Top 10 file có 1 file chiếm > 50% tổng token |
| Job bị retry loop (xử lý cùng file nhiều lần) | Số lần gọi API cao bất thường, file lặp lại trong Top 10 |
| Prompt template bị thay đổi, trở nên dài hơn nhiều | TB token/lần gọi tăng đột biến |
| Batch size cấu hình quá nhỏ (nhiều batch hơn bình thường) | Số lần gọi tăng nhưng token/lần gọi giảm |

### Xử lý

**Bước 1 — Xác định nguồn gốc:**

```
Tab Thống kê → 🤖 Giám sát Gemini API
→ Đặt bộ lọc thời gian về ngày tăng đột biến
→ Xem bảng Top 10 file → Xác định file tiêu thụ nhiều nhất
→ Xem biểu đồ token theo ngày → Xác định ngày bắt đầu tăng
```

**Bước 2 — Kiểm tra file bất thường:**

1. Mở file Excel đứng đầu Top 10 → kiểm tra số dòng.
2. Nếu file có hàng nghìn dòng → đây là nguyên nhân hợp lệ.
3. Nếu file bình thường (< 500 dòng) nhưng token rất cao → kiểm tra prompt template.

**Bước 3 — Kiểm tra retry loop:**

```powershell
# Tìm job xử lý file đó nhiều hơn 1 lần
docker-compose logs web --tail=500 | Select-String "ten-file-bat-thuong.xlsx"
```

**Bước 4 — Kiểm tra thay đổi cấu hình:**

```powershell
# Xem lịch sử thay đổi file Model/
git log --oneline -- "data/model_config*"
# Hoặc kiểm tra thời gian sửa đổi file local
Get-Item "data\*" | Sort-Object LastWriteTime -Descending | Select-Object -First 5
```

---

## 9. Đăng nhập hết hạn

### Triệu chứng

- Trang Web UI tự động chuyển hướng về trang Login.
- Các API call trả về HTTP **401 Unauthorized**.
- Thông báo lỗi: *"Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại."*

### Nguyên nhân — Cơ chế token

Hệ thống DMS sử dụng cơ chế xác thực 2 lớp:

```
┌─────────────────────────────────────────────────────┐
│              CƠ CHẾ JWT AUTHENTICATION              │
├─────────────────┬───────────────────────────────────┤
│ access_token    │ Hiệu lực: 30 phút                 │
│                 │ Dùng cho: Mọi API request          │
│                 │ Lưu tại: localStorage              │
├─────────────────┼───────────────────────────────────┤
│ refresh_token   │ Hiệu lực: 7 ngày                  │
│                 │ Dùng cho: Lấy access_token mới     │
│                 │ Lưu tại: localStorage              │
└─────────────────┴───────────────────────────────────┘
```

- **access_token hết hạn (30 phút):** Giao diện tự động dùng `refresh_token` để lấy `access_token` mới — người dùng không cần làm gì.
- **refresh_token hết hạn (7 ngày):** Không thể tự gia hạn — người dùng phải đăng nhập lại.

### Xử lý

1. Nhấn **"Đăng nhập lại"** hoặc F5 để về trang Login.
2. Nhập lại thông tin đăng nhập.
3. Sau khi đăng nhập thành công, `access_token` và `refresh_token` mới được cấp — phiên làm việc tiếp tục bình thường.

### Lưu ý bảo mật

> ⚠️ Token được lưu trong `localStorage` của trình duyệt. Không đăng nhập vào Web UI trên máy tính công cộng hoặc máy không đáng tin cậy. Luôn đăng xuất khi không sử dụng bằng nút Logout trên giao diện.

---

## Xem Logs

Logs hệ thống là công cụ chẩn đoán quan trọng nhất. DMS cung cấp nhiều cách truy cập:

### Cách 1 — Qua Web UI (khuyến nghị)

```
Tab Thống kê (📊) → Nhật ký hệ thống
```

- Log hiển thị realtime qua WebSocket `/ws/logs`.
- Hỗ trợ lọc theo mức độ: `DEBUG`, `INFO`, `WARNING`, `ERROR`.
- Cuộn lên để xem log cũ hơn.

### Cách 2 — Qua Docker Compose

```powershell
# Xem log tất cả service
docker-compose logs --tail=100

# Xem log theo service cụ thể
docker-compose logs watcher --tail=50
docker-compose logs web --tail=50

# Follow log realtime
docker-compose logs -f watcher
docker-compose logs -f web

# Lọc log theo từ khóa
docker-compose logs web --tail=200 | Select-String "ERROR"
docker-compose logs watcher --tail=200 | Select-String "SharePoint"
```

### Cách 3 — Qua WebSocket trực tiếp

```javascript
// Kết nối WebSocket để stream log realtime
const ws = new WebSocket('ws://localhost:8000/ws/logs');
ws.onmessage = (event) => console.log(JSON.parse(event.data));
```

### Cấp độ log và ý nghĩa

| Cấp độ | Ý nghĩa | Hành động cần thiết |
|---|---|---|
| `DEBUG` | Chi tiết kỹ thuật nội bộ | Không cần — chỉ dùng khi debug sâu |
| `INFO` | Hoạt động bình thường của hệ thống | Không cần |
| `WARNING` | Tình huống đáng chú ý (file skip, retry...) | Theo dõi nếu lặp lại |
| `ERROR` | Lỗi ảnh hưởng đến chức năng | Cần xử lý |
| `CRITICAL` | Lỗi nghiêm trọng, service có thể dừng | Xử lý ngay lập tức |

---

> 📞 **Hỗ trợ thêm:** Nếu sự cố không nằm trong danh sách trên hoặc không thể tự xử lý, hãy thu thập log (`docker-compose logs > dms_logs.txt`) và liên hệ đội phát triển kèm theo file log đầy đủ.
