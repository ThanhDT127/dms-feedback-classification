# Dịch vụ phân loại phản hồi DMS

Tài liệu tiếng Việt. Bản tiếng Anh: [README.md](README.md).

## Dịch vụ này làm gì

Dịch vụ theo dõi thư mục SharePoint `Input/`, lấy file Excel mới, phân loại từng dòng phản hồi, upload file kết quả lên `Output/`, upload checkpoint lên `Check_Point/`, và gửi thông báo.

Luồng hiện tại:

```text
SharePoint Input/
  -> Docker watcher
  -> model baseline trong Model/
  -> keyword và catalog trong Keyword/
  -> Gemini refine bằng Vertex AI hoặc API key
  -> SharePoint Output/ và Check_Point/
```

## Cấu trúc repo

```text
DMS/
  service/
    src/dms/                 code ứng dụng
    Keyword/                 asset keyword/catalog được commit
    Model/                   artifact model baseline được commit
    work/                    state runtime, không commit
    logs/                    log runtime, không commit
    .env.example             file mẫu, có commit
    .env                     cấu hình thật, không commit
    testvertex.json          key GCP thật, không commit
    Dockerfile
    docker-compose.yml
  README.md
  README.vi.md
  OPERATIONS.md
  OPERATIONS.vi.md
```

## Cái gì được đưa lên GitHub

Được commit:

- code trong `service/src/dms/`
- Dockerfile và compose
- `service/Keyword/`
- `service/Model/`
- `.env.example`
- tài liệu

Không commit:

- `service/.env`
- `service/testvertex.json`
- `service/work/`
- `service/logs/`

`work/` là state lúc service chạy, không phải source code. Không nên đưa lên git.

## Đầu vào bắt buộc khi chạy

Sau khi clone trên máy mới, cần tự đặt:

- `service/.env`
- `service/testvertex.json` nếu dùng Vertex AI

Repo đã có sẵn:

- `service/Keyword/`
- `service/Model/`

## Lựa chọn Gemini backend

Dịch vụ hỗ trợ 2 cách gọi Gemini.

### Cách 1: Vertex AI

Nên dùng cho production.

Cần các biến trong `.env`:

```env
GEMINI_BACKEND=vertex
GEMINI_MODEL=gemini-2.5-flash-lite
GCP_PROJECT_ID=your-gcp-project-id
GCP_LOCATION=global
GCP_SERVICE_ACCOUNT_JSON=/app/data/sa-key.json
```

Đặt file service account thật tại:

```text
service/testvertex.json
```

Docker sẽ mount vào container thành:

```text
/app/data/sa-key.json
```

### Cách 2: Gemini API key

Chỉ dùng nếu chủ động không chạy Vertex AI.

Cần các biến trong `.env`:

```env
GEMINI_BACKEND=apikey
GEMINI_MODEL=gemini-2.5-flash-lite
GEMINI_API_KEY=your-api-key
```

Khi `GEMINI_BACKEND=apikey`, Gemini client không dùng `GCP_PROJECT_ID` và `testvertex.json`.

## Yêu cầu SharePoint

Cần các biến trong `.env`:

```env
AZURE_TENANT_ID=...
AZURE_CLIENT_ID=...
AZURE_CLIENT_SECRET=...
SHAREPOINT_DRIVE_ID=...
SHAREPOINT_ROOT_FOLDER_ID=...
```

Azure app registration cần application permissions và admin consent:

- `Files.ReadWrite.All`
- `Mail.Send` nếu gửi email thông báo

Thư mục root SharePoint nên có:

```text
Input/
Output/
Check_Point/
Keyword/
Model/
```

`Keyword/` và `Model/` trên SharePoint được dùng khi bật SharePoint config sync.

## Các file state JSON được tạo như thế nào

Dịch vụ tự tạo các file này trong `service/work/`.

| File | Thành phần tạo | Mục đích |
|------|----------------|----------|
| `seen_files.json` | watcher | nhớ SharePoint file ID nào đã xử lý |
| `metrics.json` | metrics collector | lưu số liệu vận hành |
| `health.json` | watcher | lưu trạng thái health hiện tại |
| `config_assets_state.json` | config asset sync | nhớ metadata asset trên SharePoint |
| `config_assets/active/` | config asset sync | snapshot tốt nhất gần nhất của `Keyword/` và `Model/` |

Không cần tạo tay các file này nếu chạy mới. Service sẽ tự tạo khi khởi động.

Nếu chuyển production sang VM mới và không muốn xử lý lại file SharePoint cũ, hãy copy `service/work/` từ máy cũ sang trước khi start container mới.

## Chạy nhanh trên VM mới

```powershell
git clone https://github.com/ThanhDT127/dms-feedback-classification.git
cd dms-feedback-classification\service
copy .env.example .env
```

Sau đó điền `.env` và đặt `testvertex.json` nếu dùng Vertex AI.

Chạy service:

```powershell
docker compose up -d
docker compose ps
docker compose logs -f
```

Kiểm tra runtime:

```powershell
Get-Content .\work\health.json
Get-Content .\work\metrics.json
```

## Chuyển máy mà không xử lý lại file cũ

Trên máy cũ:

```powershell
cd D:\Works\DMS\service
```

Copy sang VM mới:

```text
.env
testvertex.json
work/
```

Trên VM mới, đặt vào:

```text
dms-feedback-classification/service/
```

Rồi chạy:

```powershell
docker compose up -d
```

File quan trọng nhất là:

```text
work/seen_files.json
```

Nếu thiếu file này, VM mới không biết file SharePoint nào đã xử lý trước đó.

## Giao diện Web Dashboard (Web UI)

Hệ thống cung cấp giao diện Web UI tại cổng `8501` (http://localhost:8501) giúp người vận hành quản lý trực quan:
- **Tổng quan (Dashboard):** Xem biểu đồ sử dụng CPU/Memory, log trực tiếp (log stream) và thông tin health check.
- **Phân loại (Classify):** Chạy phân loại theo lô (batch processing), giám sát thanh tiến trình thời gian thực và tải trực tiếp file kết quả.
- **Quản lý file:** Upload file lên server local, liệt kê file trên SharePoint và thực hiện đồng bộ hóa thủ công.
- **Thống kê (Metrics):** Xem biểu đồ số file xử lý theo ngày và phân bổ nhãn phân loại (doughnut chart).
- **Cấu hình & Thử nghiệm:** Chỉnh sửa cấu hình hệ thống (.env), cập nhật từ điển từ khóa và chạy thử nghiệm phân loại nhanh.

## Phục hồi lịch sử thống kê (Reconstruct History)

Nếu biểu đồ thống kê hiển thị sai lệch ngày hoặc trống dữ liệu nhãn trên môi trường Production mới:
```bash
docker compose exec watcher python scripts/reconstruct_history.py
```
Script sẽ tự động quét metadata SharePoint và các file đầu ra Excel lịch sử để dựng lại file state cục bộ, đồng thời sao lưu trực tiếp lên thư mục `Check_Point/` của SharePoint để tự động đồng bộ cho các VM tiếp theo.

## Dọn file tạm runtime

Sau khi một file xử lý thành công, upload thành công, và được đánh dấu `done`, service xóa các file tạm:

- `work/input/<file>.xlsx`
- `work/output/<file>_output.xlsx`
- `work/checkpoint/<file>.json`

State được bảo vệ:

- `work/seen_files.json`
- `work/metrics.json`
- `work/health.json`
- `work/config_assets_state.json`
- `work/config_assets/active/`

## Tài liệu vận hành chi tiết

Xem chi tiết trong:
- [OPERATIONS.vi.md](OPERATIONS.vi.md) - Hướng dẫn deploy, cấu hình chi tiết, phục hồi lịch sử, xử lý sự cố.
- [service/README.md](service/README.md) - Tài liệu kiến trúc và hướng dẫn dành cho lập trình viên.
