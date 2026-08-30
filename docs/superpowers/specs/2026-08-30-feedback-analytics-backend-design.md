# Feedback Analytics Backend Design

## Mục tiêu

Lưu từng phản hồi thật khi hệ thống phân loại file, cùng kết quả phân loại của
phản hồi đó, để dashboard truy vấn lịch sử mà không phải đọc lại Excel hoặc
`metrics.json`.

Phạm vi này chỉ là backend. Dashboard được thực hiện ở nhánh riêng sau khi API
ổn định.

## Nguyên tắc dữ liệu

- Không tự sinh hoặc suy luận các trường nghiệp vụ: `Mã vấn đề`, ngày, nguồn,
  đơn vị và trạng thái xử lý. Cột không có trong input được lưu `NULL`.
- `feedback_id` và khóa nguồn là khóa kỹ thuật nội bộ. Chúng không thay thế Mã
  vấn đề và không được dùng để tính KPI “vấn đề duy nhất”.
- Giá trị `NULL` của nguồn/đơn vị được API trình bày là `Chưa xác định`; dữ liệu
  gốc trong database vẫn là `NULL`.
- Mọi KPI dùng “Mã vấn đề duy nhất” chỉ dùng các record có `issue_code` không
  rỗng. Response phải trả số record bị loại vì thiếu Mã vấn đề và `available:
  false` khi mẫu số bằng 0.
- Không ghi Excel gốc vào database. Excel input/output vẫn do luồng hiện tại
  quản lý trên work directory/SharePoint; database lưu snapshot từng dòng để
  phân tích và audit.

## Lưu trữ

Dùng cùng SQLite database hiện có tại `classification_jobs_db_path`
(`work/classification_jobs.db`). Database đã dùng WAL, busy timeout và foreign
keys; không tạo service database hay file SQLite thứ hai.

### `feedback_records`

Mỗi row phản hồi được đọc từ một file tương ứng với một record logic.

| Cột | Ý nghĩa |
|---|---|
| `feedback_id` | Integer primary key kỹ thuật |
| `source_file_key` | SharePoint file id đối với Watcher; SHA-256 nội dung input đối với upload thủ công |
| `source_file_name` | Tên file tại thời điểm nhận input, phục vụ truy vết |
| `source_row_number` | Số dòng Excel gốc |
| `last_job_id` | Job phân loại mới nhất đã cập nhật record |
| `raw_data_json` | Toàn bộ cột gốc của row, JSON UTF-8 |
| `content` | Nội dung phản hồi dùng để phân loại |
| `normalized_content` | Nội dung chuẩn hóa dùng cho báo cáo trùng lặp |
| `issue_code`, `issue_date`, `source`, `unit_name`, `business_status` | Metadata nghiệp vụ, đều nullable |
| `product`, `product_line`, `model`, `sentiment`, `brand`, `bm25_score` | Kết quả pipeline, nullable cho tới khi batch hoàn tất |
| `classification_state` | `pending`, `completed` hoặc `failed` |
| `is_active` | `true` khi row còn tồn tại trong phiên bản hiện tại của cùng file SharePoint |
| `created_at`, `updated_at`, `classified_at` | Mốc thời gian kỹ thuật UTC |

Unique key là `(source_file_key, source_row_number)`. Chạy lại đúng file cập
nhật record hiện hữu thay vì tăng số phản hồi. Job history vẫn nằm trong
`classification_jobs`.

Nếu cùng SharePoint file bị chỉnh sửa, record cùng dòng được cập nhật theo input
mới nhất. Sau khi đọc file thành công, các row cũ của file không còn xuất hiện
trong input được đặt `is_active = false`, không bị xóa. API analytics mặc định
chỉ tính row active. Đây là dashboard trạng thái hiện tại; lịch sử job vẫn cho
biết lần chạy trước. Lịch sử phiên bản từng row không nằm trong MVP.

Với upload thủ công, hash nội dung tạo một `source_file_key` mới khi file thay
đổi; mỗi lần upload như vậy là một tập dữ liệu đã nhận độc lập và vẫn active.

### `feedback_labels`

Mỗi nhãn active của một feedback là một row: `feedback_id`, `label`,
`major_group`, `created_at`. Unique key `(feedback_id, label)`.

`major_group` được snapshot khi lưu kết quả để thay đổi mapping nhãn trong
tương lai không viết lại báo cáo lịch sử.

`classification_job_results` hiện có được giữ nguyên cho WebSocket/progress;
analytics không đọc trực tiếp JSON payload đó.

## Luồng ghi dữ liệu

1. Job được tạo trước khi pipeline chạy, cho cả upload thủ công và Watcher.
2. Một shared input reader dùng cùng quy tắc tìm header/cột text của pipeline
   đọc trọn file trước. Nếu hợp lệ, repository tạo/upsert toàn bộ
   `feedback_records` trong một transaction, đặt `classification_state =
   pending`, giữ metadata không có là `NULL`, và với Watcher chỉ inactive các
   row cũ của chính file không có trong lần đọc này.
3. `PipelineRunner` bổ sung source row number vào mỗi item `new_results` của
   progress callback.
4. Sau mỗi batch, repository cập nhật record phù hợp với kết quả RAG/LLM,
   thay toàn bộ label cũ của record bằng label batch mới và đặt state
   `completed`, trong một transaction.
5. Nếu job bị hủy hoặc lỗi, các row chưa hoàn tất được đánh dấu `failed`; row
   từ các batch đã commit giữ kết quả `completed`.
6. Retry chạy lại cùng file thực hiện upsert và thay kết quả cũ, không nhân đôi
   dashboard count.

Watcher hiện tạo job sau khi pipeline thành công. Luồng này được chuyển sang
tạo job trước khi đọc input, sau đó dùng cùng repository và progress callback
với upload thủ công.

## Ánh xạ metadata input

Shared input reader dùng alias không phân biệt hoa/thường và dấu tiếng Việt:

| Trường | Alias mặc định |
|---|---|
| `issue_code` | `Mã vấn đề`, `Ma van de` |
| `issue_date` | `Ngày ghi nhận`, `Ngày`, `Date` |
| `source` | `Nguồn`, `Source` |
| `unit_name` | `Tên đơn vị`, `Đơn vị`, `Unit` |
| `business_status` | `Trạng thái`, `Status` |

Alias không có trong file không phải lỗi; trường tương ứng là `NULL`. Chỉ lỗi
khi không xác định được cột nội dung phản hồi, giữ nguyên hành vi pipeline.

## API analytics

Routes đặt dưới `/api/analytics`, yêu cầu người dùng đã đăng nhập. Các route chỉ
đọc dữ liệu ở scope backend này; API cập nhật trạng thái nghiệp vụ không thuộc
MVP.

| Route | Mục đích |
|---|---|
| `GET /overview` | KPI tổng quan, xử lý, coverage nhãn/sentiment/sản phẩm, đa nhãn, trùng lặp và so sánh kỳ |
| `GET /sources` | Phân bổ nguồn, có bucket `Chưa xác định` |
| `GET /units` | Phân bổ đơn vị, có bucket `Chưa xác định` |
| `GET /groups` | Nhóm vấn đề và tỷ trọng sentiment theo nhóm |
| `GET /products` | Sản phẩm × nhóm/nhãn, gồm nhóm chưa xác định sản phẩm |
| `GET /issues` | Bảng chi tiết có lọc/phân trang |
| `GET /data-quality` | Số record có/thiếu từng metadata và lý do KPI unavailable |

Các route nhận `from`, `to`, `compare_from`, `compare_to` theo ISO date. Nếu
không có range, API truy vấn toàn bộ dữ liệu lưu được. Response mọi KPI có
`available`, `value`, `denominator`, `excluded_missing_issue_code` và `reason`
khi không thể tính.

### Quy tắc KPI

- Tổng vấn đề, xử lý, nguồn, đơn vị và so sánh cùng kỳ: distinct
  non-empty `issue_code`.
- `Đã xử lý` là distinct `issue_code` có ít nhất một row trong phạm vi lọc với
  `business_status`, sau khi trim và không phân biệt hoa/thường, bằng `Đã xử lý`.
  `issue_code` thiếu trạng thái không được tính là đã xử lý.
- Phân bổ theo nguồn/đơn vị dùng distinct `issue_code` trong từng bucket. Một
  mã xuất hiện ở nhiều nguồn/đơn vị có thể thuộc nhiều bucket, vì vậy response
  trả `membership_count` và phần trăm bucket không bắt buộc cộng thành 100%.
- Multi-label: distinct issue code có từ hai label active trở lên.
- Hoàn thiện nhãn/sentiment/sản phẩm: distinct issue code thỏa điều kiện trên
  distinct issue code trong phạm vi lọc.
- Nhóm/sản phẩm: một vấn đề đa nhãn có thể xuất hiện ở nhiều nhóm; response ghi
  rõ `membership_count` để UI không diễn giải đây là tổng độc quyền.
- Trùng lặp: chuẩn hóa content bằng trim, lowercase và gộp whitespace; response
  trả cả duplicate rows, total records và tỷ lệ. Nếu KPI nghiệp vụ yêu cầu mẫu
  số mã vấn đề, API trả thêm tỷ lệ theo `issue_code` khi mẫu số có dữ liệu.
- Độ chính xác mô hình trả `available: false` cho tới khi hệ thống nhận được
  nhãn chuẩn do con người xác nhận.
- `GET /products` tách bốn nhãn chất lượng hiện có: `Báo lỗi`, `Báo CL tốt`,
  `Y/c cải tiến`, `Đề xuất SPM`; nhãn khác vẫn được trả trong phần phân bổ
  chung, không bị loại khỏi dữ liệu.

## Tính nhất quán và xử lý lỗi

- Repository dùng transaction cho upsert một batch và thay label để không có
  record nào ở trạng thái completed nhưng thiếu một phần nhãn.
- Nếu không thể lưu input trước pipeline: fail job trước khi gọi model.
- Lỗi ghi kết quả batch: làm job fail và không upload output thành công, tránh
  output có nhưng dashboard thiếu analytics.
- Input invalid/cột content không tồn tại: giữ cơ chế fail hiện tại, không tạo
  analytics record rỗng.
- Constraints và upsert khiến retry idempotent; WAL/busy timeout hiện có xử lý
  Web/Watcher ghi đồng thời.

## Kiểm thử

- Repository: metadata NULL, JSON raw row, upsert/retry, labels replacement,
  duplicate normalization và transaction rollback.
- Pipeline integration: upload thủ công persist trước khi chạy, callback cập
  nhật đúng row, failure/cancel không làm mất raw record.
- Watcher integration: tạo job trước pipeline, persist row level cho success
  và failure.
- API: các KPI có dữ liệu, data thiếu Mã vấn đề, bucket `Chưa xác định`, range
  so sánh, multi-label, duplicate và accuracy unavailable.
- Regression: test hiện có của `ClassificationJobStore`, metrics và frontend
  contract tiếp tục chạy.

## Ngoài phạm vi

- UI dashboard, biểu đồ và chỉnh trạng thái nghiệp vụ bằng UI.
- Backfill file lịch sử. Đây là utility riêng, chỉ chạy khi được yêu cầu và
  không điền metadata thiếu.
- Lưu phiên bản lịch sử của từng row khi một file nguồn bị chỉnh sửa.
