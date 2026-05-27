# BÁO CÁO THẨM ĐỊNH CHẤT LƯỢNG PHÂN LOẠI PHẢN HỒI DMS
**File dữ liệu thực tế:** DMS-2010-2210.xlsx (364 dòng phản hồi thực tế)
**Thời gian thực hiện:** 2026-05-25
**Chuyên viên thực hiện:** Thẩm định viên dữ liệu cao cấp DMS

## 1. THỐNG KÊ CHẤT LƯỢNG PHÂN LOẠI THỰC CHẤT
Dựa trên việc rà soát kỹ lưỡng 100% (364/364) các câu phản hồi thực tế trong file `all_DMS-2010-2210.txt`, đối chiếu Ground Truth (nhãn đúng thực tế dựa trên nghĩa tiếng Việt kinh doanh), kết quả đạt được như sau:

| Chỉ số đánh giá | Bản cũ (Old) | Bản mới (V3) | Nhận xét thực tế |
| :--- | :---: | :---: | :--- |
| **Số dòng đúng thực tế** | 149 | 247 | Bản mới V3 cải thiện vượt bậc về chất lượng |
| **Tỷ lệ chính xác (%)** | 40.93% | 67.86% | **V3 tăng trưởng +26.92%** so với Bản cũ |
| **Số dòng sai lệch** | 215 | 117 | Bản cũ sai lệch quá nhiều ở các nhãn cốt lõi |

## 2. PHÂN TÍCH CHUYÊN SÂU CÁC LỖI HỆ THỐNG PHÁT HIỆN

### A. CÁC LỖI HỆ THỐNG NẶNG CỦA BẢN CŨ (OLD)
Bản cũ (Old) có tỷ lệ chính xác cực kỳ thấp (40.93%) do mắc các lỗi hệ thống mang tính nghiêm trọng:
1. **Lỗi "Tin trung lập" bừa bãi**: Hàng loạt câu phản hồi báo lỗi nghiêm trọng, phàn nàn giá cả hay thông tin đối thủ cạnh tranh đều bị gán bừa bãi vào nhãn `Tin trung lập` (ví dụ: các dòng báo lỗi ruột phích, lỗi lưới vợt muỗi, giao hàng chậm, bão bay biển bảng...). Điều này làm vô hiệu hóa giá trị của dữ liệu.
2. **Sai lệch Sentiment cực kỳ ngớ ngẩn (Khen thành chê, Tích cực thành Tiêu cực)**: Khen giá AT58 hợp lý, đèn học dễ bán... nhưng Old lại gán sentiment `Tiêu cực` (dòng 113, 122). Ngược lại, chê khó bán thì gán `Tích cực` (dòng 349).
3. **Lỗi nhận diện sai nhãn nghiêm trọng do bắt nhầm từ khóa**: Gán nhầm từ khóa "máng vs tube" thành `Tin trung lập`, hay gán nhầm sản phẩm đối thủ Sopoka thành nhãn `Bảng giá, Catalogue` (dòng 56) và `Bảo hành` (dòng 254).

### B. CÁC LỖI HỆ THỐNG CỦA BẢN MỚI (V3) VÀ ĐỀ XUẤT CẢI TIẾN
Mặc dù Bản mới V3 đạt tỷ lệ chính xác rất cao (67.86%), thẩm định viên vẫn phát hiện **4 lỗi hệ thống** cần khắc phục để hoàn thiện pipeline:

1. **Lỗi "Override" bởi thực thể cạnh tranh (Competitor Override)**
   - *Mô tả*: Khi câu phản hồi xuất hiện tên của đối thủ cạnh tranh (ví dụ: Sino, Sopoka, Asia, Pana), V3 có xu hướng gán thuần nhãn đối thủ `['Hãng', 'CTKM, giá, cơ chế']` hoặc `['Hãng', 'TT SP']` mà bỏ qua hoàn toàn nhãn phản ánh trực tiếp chất lượng/nghiệp vụ của Rạng Đông (RĐ).
   - *Bằng chứng tiêu biểu*:
     - **Row 134**: "*attpmat rạng đông mẫu mã đẹp, chắc chắn, giá thành cao hơn sino, mong cty ra thêm chương trình*" -> V3 gán `['Hãng', 'CTKM, giá, cơ chế']` (Sai hoàn toàn nghiệp vụ chính là khen chất lượng RĐ chắc chắn và đề xuất khuyến mại). Old đúng hơn khi gán đầy đủ.
     - **Row 148**: "*Tủ điện và aptomat các loại, giá cao khó cạnh tranh với các loại trên thị trường, đặc biệt là sino*" -> V3 gán `['Hãng', 'CTKM, giá, cơ chế']` | `Tiêu cực` (Bỏ qua hoàn toàn phản hồi chê giá át RĐ cao khó bán - nhãn `Tốt/ ko tốt`).
     - **Row 226**: "*aptomat gia dụng giá thành hơi cao vào thị trường khó, sino 30k*" -> V3 gán `['Hãng', 'CTKM, giá, cơ chế']` (Tương tự, bỏ qua giá át RĐ cao).
     - **Row 328**: "*Vợt rạng đông 02 vẩn hay lỗi khách vẩn thích vợt ASIA hơn ít lỗi*" -> V3 gán `['Hãng', 'TT SP']` (Bỏ qua việc khách báo lỗi vợt RĐ lỗi nhiều).
   - *Giải pháp*: Cải tiến prompt của pipeline LLM để luôn rà soát tách biệt 2 luồng: Phản hồi về sản phẩm RĐ và Phản hồi về đối thủ cạnh tranh, tránh để thực thể đối thủ ghi đè lên nghiệp vụ RĐ.

2. **Lỗi bất nhất (Inconsistency) giữa các câu tương đồng**
   - *Mô tả*: Các câu có cấu trúc hoặc nội dung gần như giống hệt nhau nhưng lại bị phân loại ra kết quả khác nhau.
   - *Bằng chứng tiêu biểu*:
     - **Row 59 vs Row 64**: Cùng khen ổ cắm Lioa đa dạng mẫu mã giá rẻ... nhưng Row 59 gán `Sentiment: Tiêu cực`, Row 64 gán `Sentiment: Tích cực`.
     - **Row 63 vs Row 98**: Cùng nói về ổ tải Sopoka có mẫu mới kèm công tắc đèn báo. Row 63 gán `['Đề xuất SPM']` | `Tiêu cực` (Sai hoàn toàn), Row 98 gán đúng `['Hãng', 'TT SP']` | `—`.
   - *Giải pháp*: Chuẩn hóa tiền xử lý văn bản, tăng tính ổn định của LLM bằng cách giảm temperature xuống 0.0 (hoặc tối ưu hóa tham số cấu hình) và chuẩn hóa prompt mẫu.

3. **Lỗi nhầm lẫn từ đồng âm/chính tả (Homophone Confusion)**
   - *Bằng chứng tiêu biểu*:
     - **Row 223**: "*aptomat Rạng Đông mới được 1 năm thợ vẫn chưa tin thưởng , nhu cầu sử dụng của người dân thì chỉ chon pana...*" -> Từ "*tin thưởng*" (khách viết sai chính tả của từ *tin tưởng*) bị V3 nhận diện nhầm thành nhãn `Trả thưởng` (Reward).
   - *Giải pháp*: Bổ sung module chuẩn hóa chính tả tiếng Việt (Spell Checker) trước khi đưa vào pipeline phân loại của LLM.

4. **Lỗi "Tiêu cực hóa" thái độ trung lập (Over-sensitive Negative)**
   - *Mô tả*: V3 có xu hướng gán cảm xúc `Tiêu cực` cho các câu xin hỗ trợ, đề xuất bình thường.
   - *Bằng chứng tiêu biểu*:
     - **Row 26**: "*Cty có thể cho ch cái biển đc k*" -> V3 gán sentiment `Tiêu cực` (Đây chỉ là câu xin hỗ trợ biển quảng cáo trung lập).
   - *Giải pháp*: Căn chỉnh lại định nghĩa sentiment trong prompt: Các yêu cầu xin hỗ trợ hoặc đóng góp ý kiến mang tính xây dựng không được coi là tiêu cực trừ khi có từ ngữ thể hiện sự bức xúc, phàn nàn rõ rệt.

---
## 3. PHỤ LỤC: DANH SÁCH BẰNG CHỨNG CỤ THỂ (MỘT SỐ DÒNG TIÊU BIỂU)
*(Vui lòng xem chi tiết trong file `evaluated_results.json`)*
