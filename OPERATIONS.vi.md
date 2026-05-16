# Hướng dẫn vận hành DMS

Tài liệu tiếng Việt. Bản tiếng Anh: [OPERATIONS.md](OPERATIONS.md).

## 1. Tổng quan dịch vụ

DMS service là một Python watcher chạy bằng Docker. Dịch vụ chạy trong `service/` với:

```text
python -m dms
```

Nhiệm vụ chính:

- poll SharePoint `Input/`
- tải file `.xlsx` mới
- phân loại từng dòng bằng local baseline model, keyword/product assets, và Gemini
- upload workbook kết quả lên SharePoint `Output/`
- upload checkpoint lên SharePoint `Check_Point/`
- ghi health, metrics, và state files tại local
- dọn file tạm sau khi xử lý thành công

## 2. Thư mục runtime

```text
service/
  src/dms/                 source code
  Keyword/                 keyword/product asset gốc, có commit
  Model/                   artifact model baseline gốc, có commit
  work/                    state runtime, không commit
  logs/                    log runtime, không commit
  .env                     secret và cấu hình thật, không commit
  testvertex.json          GCP service account key, không commit
```

Docker mount:

| Đường dẫn host | Đường dẫn container | Chế độ | Mục đích |
|----------------|---------------------|--------|----------|
| `./Keyword` | `/app/data/Keyword` | read-only | fallback keyword/product assets |
| `./Model` | `/app/data/Model` | read-only | fallback model artifacts |
| `./testvertex.json` | `/app/data/sa-key.json` | read-only | Vertex AI service account key |
| `./work` | `/app/data/work` | read-write | runtime state |
| `./logs` | `/app/data/logs` | read-write | service logs |

## 3. Cấu hình bắt buộc

Tất cả cấu hình runtime nằm trong `service/.env` và một số giá trị cố định trong `docker-compose.yml`.

Tạo `.env` từ file mẫu:

```powershell
cd dms-feedback-classification\service
copy .env.example .env
```

### Azure AD

Bắt buộc:

```env
AZURE_TENANT_ID=...
AZURE_CLIENT_ID=...
AZURE_CLIENT_SECRET=...
```

Azure app registration cần permissions:

- `Files.ReadWrite.All` để đọc/ghi SharePoint files
- `Mail.Send` nếu có gửi email

Hai permission này phải là application permissions và đã admin consent.

### SharePoint

Bắt buộc:

```env
SHAREPOINT_DRIVE_ID=...
SHAREPOINT_ROOT_FOLDER_ID=...
```

Thư mục dưới `SHAREPOINT_ROOT_FOLDER_ID` nên có:

```text
Input/
Output/
Check_Point/
Keyword/
Model/
```

`Input/`, `Output/`, và `Check_Point/` dùng cho xử lý file.

`Keyword/` và `Model/` dùng cho SharePoint config sync nếu bật.

### Gemini backend: Vertex AI

Khuyến nghị dùng cho production.

`.env`:

```env
GEMINI_BACKEND=vertex
GEMINI_MODEL=gemini-2.5-flash-lite
GCP_PROJECT_ID=your-project-id
GCP_LOCATION=global
GCP_SERVICE_ACCOUNT_JSON=/app/data/sa-key.json
```

File local bắt buộc:

```text
service/testvertex.json
```

GCP project phải bật Vertex AI API, và service account phải có quyền gọi model Gemini đã chọn.

### Gemini backend: API key

Chế độ tùy chọn.

`.env`:

```env
GEMINI_BACKEND=apikey
GEMINI_MODEL=gemini-2.5-flash-lite
GEMINI_API_KEY=your-api-key
```

Trong chế độ này, Gemini client không dùng `testvertex.json`.

### Cấu hình xử lý

```env
POLL_INTERVAL_SECONDS=300
LLM_BATCH_SIZE=20
CKPT_EVERY=50
```

Ý nghĩa:

- `POLL_INTERVAL_SECONDS`: bao lâu watcher check SharePoint một lần
- `LLM_BATCH_SIZE`: số dòng Excel trong một batch gọi Gemini
- `CKPT_EVERY`: tần suất ghi checkpoint khi pipeline đang xử lý

### SharePoint config sync

```env
ENABLE_SHAREPOINT_CONFIG_SYNC=true
SHAREPOINT_KEYWORD_FOLDER=Keyword
SHAREPOINT_MODEL_FOLDER=Model
```

Khi bật:

- service check SharePoint `Keyword/` và `Model/` trước mỗi poll cycle
- asset nào đổi thì tải về staging folder trong `work/config_assets/`
- asset hợp lệ được publish vào `work/config_assets/active/`
- runtime dependency được reload giữa các cycle

### Runtime cleanup

```env
ENABLE_RUNTIME_CLEANUP=true
CLEANUP_OUTPUT_TTL_DAYS=7
CLEANUP_LOG_TTL_DAYS=7
CLEANUP_STAGING_TTL_HOURS=24
```

Cleanup xóa file tạm, nhưng giữ state cần cho service chạy liên tục.

## 4. Các file state runtime

Service tự tạo các file này.

| Đường dẫn | Tạo khi nào | Mục đích |
|-----------|-------------|----------|
| `work/seen_files.json` | khi watcher save lần đầu | nhớ SharePoint item ID đã xử lý |
| `work/metrics.json` | khi metrics flush lần đầu | counters, thời gian, tỉ lệ thành công |
| `work/health.json` | khi health update lần đầu | trạng thái hiện tại của service |
| `work/config_assets_state.json` | khi config sync lần đầu | metadata remote của `Keyword/` và `Model/` |
| `work/config_assets/active/` | khi config sync thành công lần đầu | snapshot runtime tốt nhất gần nhất |

Không cần tạo tay các file JSON này nếu chạy mới. Khởi động service là nó sẽ tự tạo.

Cần phân biệt:

- `Keyword/` và `Model/` là source fallback được commit
- `work/config_assets/active/` là snapshot đang được runtime dùng khi SharePoint config sync bật

## 5. Deploy VM mới từ đầu

Dùng khi VM mới được phép bắt đầu như một instance mới.

```powershell
git clone https://github.com/ThanhDT127/dms-feedback-classification.git
cd dms-feedback-classification\service
copy .env.example .env
```

Sau đó:

1. điền `.env`
2. đặt `testvertex.json` nếu dùng Vertex AI
3. kiểm tra có `Keyword/`
4. kiểm tra có `Model/`
5. start Docker

```powershell
docker compose up -d
docker compose ps
docker compose logs -f
```

Rủi ro:

- nếu không có `work/seen_files.json`, VM mới không biết file SharePoint nào đã xử lý trước đó
- các file cũ trong SharePoint `Input/` có thể bị xử lý lại

## 6. Chuyển VM mà không xử lý lại file cũ

Dùng khi thay máy hoặc chuyển service đang chạy sang VM mới.

Từ máy cũ, copy:

```text
service/.env
service/testvertex.json
service/work/
```

Đặt vào repo đã clone trên VM mới:

```text
dms-feedback-classification/service/.env
dms-feedback-classification/service/testvertex.json
dms-feedback-classification/service/work/
```

Sau đó start:

```powershell
cd dms-feedback-classification\service
docker compose up -d
docker compose logs -f
```

State tối thiểu nên giữ:

- `work/seen_files.json`
- `work/config_assets_state.json`
- `work/config_assets/active/`

Tốt nhất:

- copy cả thư mục `service/work/`

Lý do:

- `seen_files.json` ngăn file SharePoint cũ bị xử lý lại
- `config_assets_state.json` giữ metadata asset remote
- `config_assets/active/` giữ snapshot asset tốt nhất gần nhất

## 7. Checkpoint và resume

Checkpoint theo từng file nằm ở:

```text
work/checkpoint/
```

Nó hữu ích khi một file đang xử lý dở hoặc đang retry.

Sau khi file đã xử lý thành công, upload thành công, và được đánh dấu `done`, file tạm local sẽ bị cleanup:

- `work/input/<file>.xlsx`
- `work/output/<file>_output.xlsx`
- `work/checkpoint/<file>.json`

Vì vậy với file đã xong, resume chủ yếu dựa vào:

```text
work/seen_files.json
```

Muốn ép một file chạy lại:

1. dừng container
2. mở `work/seen_files.json`
3. tìm entry theo `name`
4. xóa entry đó hoặc đổi `status` thành `retry`
5. start container lại

Lệnh xem state:

```powershell
Get-Content .\work\seen_files.json
```

## 8. Cập nhật asset từ SharePoint

Khi file keyword hoặc model trên SharePoint được cập nhật:

1. watcher phát hiện metadata thay đổi ở poll cycle tiếp theo
2. file đổi được tải về `work/config_assets/cfgsync-*`
3. asset được validate
4. active snapshot được cập nhật trong `work/config_assets/active/`
5. pipeline dependencies được reload trước khi xử lý file

File source local trong `service/Keyword/` và `service/Model/` không bị ghi đè.

Kiểm tra keyword snapshot đang chạy:

```powershell
Get-Content .\work\config_assets\active\Keyword\kw_map.json
```

## 9. Monitoring

Lệnh hay dùng trong `service/`:

```powershell
docker compose ps
docker compose logs -f
Get-Content .\work\health.json
Get-Content .\work\metrics.json
Get-Content .\work\config_assets_state.json
```

Dấu hiệu khỏe:

- container `Up`
- log có `Composition root ready`
- log có poll cycle
- `health.json` cập nhật `last_poll`
- list SharePoint `Input/` thành công

## 10. Lỗi thường gặp

### Container không start

Kiểm tra:

- có `.env`
- có `testvertex.json` nếu dùng Vertex AI
- có `Keyword/`
- có `Model/`
- Azure client secret còn đúng
- SharePoint IDs đúng

### `No such container: dms-feedback-watcher`

Service chưa chạy.

```powershell
cd dms-feedback-classification\service
docker compose up -d
docker compose ps
```

### Asset SharePoint đã đổi nhưng `service/Keyword/` không đổi

Đây là đúng thiết kế.

Runtime đọc snapshot sync ở:

```text
work/config_assets/active/
```

`Keyword/` và `Model/` local chỉ là fallback source read-only.

### VM mới xử lý lại file cũ

Nguyên nhân:

- chưa copy `work/seen_files.json` từ máy cũ

Cách sửa:

- dừng container trên VM mới
- copy `service/work/` từ máy cũ sang
- start container lại

## 11. Ghi chú bảo mật

Không bao giờ commit:

- `.env`
- `testvertex.json`
- Azure client secrets
- API keys
- `work/`
- `logs/`

Nếu secret đã bị lộ, rotate secret trong Azure hoặc GCP trước khi tiếp tục dùng môi trường đó.
