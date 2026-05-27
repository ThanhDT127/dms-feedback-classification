# BÁO CÁO PHÂN TÍCH SO SÁNH BA PHIÊN BẢN (OLD vs V3 vs V4)
Quy mô dữ liệu đối chiếu: 387 dòng phản hồi gộp từ 5 file Excel.

## I. Độ chính xác Tuyệt đối (Exact Match Accuracy)
> **Exact Match Accuracy** là tiêu chuẩn nghiêm ngặt nhất: một dòng được coi là đúng khi và chỉ khi **khớp chính xác 100% cả tập nhãn (labels) và cảm xúc (sentiment)** so với Ground Truth.

### 1. Bảng so sánh tổng hợp gộp cả 5 file
| Phiên bản | Độ chính xác Tuyệt đối (Exact Match) | Độ chính xác gán Nhãn (Label Exact Match) | Độ chính xác Cảm xúc (Sentiment Match) |
|---|:---:|:---:|:---:|
| **Bản cũ (Old Production)** | 28.94% | 31.27% | 75.45% |
| **Bản mới V3 (V3)** | 48.06% | 53.49% | 79.07% |
| **Bản hiện tại V4 (V4)** | 49.10% | 52.45% | 82.95% |

### 2. Chi tiết độ chính xác theo từng file Excel
| Tên file Excel | Số dòng | Bản cũ (Old) | Bản mới (V3) | Bản hiện tại (V4) | Tăng trưởng V4 vs V3 | Tăng trưởng V4 vs Old |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| DMS-14102025.xlsx | 1 | 100.0% | 100.0% | 100.0% | **+0.0%** | **+0.0%** |
| DMS-1510-1710.xlsx | 14 | 28.6% | 57.1% | 78.6% | **+21.4%** | **+50.0%** |
| DMS-1810-1910.xlsx | 8 | 25.0% | 87.5% | 87.5% | **+0.0%** | **+62.5%** |
| DMS-2010-2210.xlsx | 364 | 28.8% | 46.7% | 47.0% | **+0.3%** | **+18.1%** |

## II. Phân tích Chỉ số không thiên lệch (Macro-average Metrics)
> Điểm **Macro-average** tính bằng trung bình cộng độc lập các nhãn, phản ánh khách quan năng lực nhận diện các nhãn hiếm mà không bị loãng bởi các nhãn chiếm đa số.

| Chỉ số Macro-average | Bản cũ (Old) | Bản mới (V3) | Bản hiện tại (V4) | Cải thiện V4 vs V3 |
|---|:---:|:---:|:---:|:---:|
| **Macro Accuracy** | 93.71% | 96.21% | 96.22% | **+0.01%** |
| **Macro Precision** | 53.65% | 67.88% | 67.43% | **+-0.46%** |
| **Macro Recall** | 44.62% | 77.93% | 79.01% | **+1.08%** |
| **Macro F1-Score** | 39.36% | 70.05% | 70.01% | **+-0.04%** |

## III. Bảng so sánh chi tiết từng Cột Nhãn (Label-wise F1-Score Comparison)
Bảng dưới đây so sánh điểm **F1-Score (%)** của cả 3 phiên bản trên toàn bộ 21 cột nhãn nghiệp vụ:
| Tên nhãn nghiệp vụ | Số mẫu thực tế (Support) | Bản cũ (Old) F1 | Bản mới (V3) F1 | Bản hiện tại (V4) F1 | Kết quả tối ưu ở V4 |
|---|:---:|:---:|:---:|:---:|---|
| Báo lỗi | 76 | 69.9% | 81.9% | 83.0% | 🔥 Tăng +1.1% |
| Báo CL tốt | 17 | 66.7% | 73.2% | 81.1% | 🔥 Tăng +7.9% |
| Y/c cải tiến | 27 | 40.0% | 56.3% | 58.4% | 🔥 Tăng +2.1% |
| Đề xuất SPM | 25 | 54.5% | 78.7% | 82.8% | 🔥 Tăng +4.1% |
| Bảng giá, Catalogue | 0 | 0.0% | 0.0% | 0.0% | — |
| Bảng biển | 20 | 66.7% | 100.0% | 100.0% | ✨ Hoàn hảo |
| Kệ bóng, thử đèn,… | 3 | 28.6% | 66.7% | 60.0% | 📉 Giảm -6.7% |
| Khác | 0 | 0.0% | 0.0% | 0.0% | — |
| Tốt/ ko tốt | 21 | 45.9% | 60.0% | 71.7% | 🔥 Tăng +11.7% |
| Trả thưởng | 3 | 21.1% | 75.0% | 66.7% | 📉 Giảm -8.3% |
| Đề xuất | 21 | 26.7% | 45.7% | 45.7% | Không đổi |
| Bảo hành | 16 | 83.9% | 93.8% | 90.9% | 📉 Giảm -2.8% |
| HTPP | 0 | 0.0% | 0.0% | 0.0% | — |
| Hàng hoá | 15 | 58.3% | 75.0% | 70.6% | 📉 Giảm -4.4% |
| Hàng giả | 0 | 0.0% | 0.0% | 0.0% | — |
| Website | 0 | 0.0% | 0.0% | 0.0% | — |
| Hãng | 103 | 12.5% | 87.7% | 90.0% | 🔥 Tăng +2.4% |
| Hoạt động | 30 | 18.2% | 79.3% | 72.1% | 📉 Giảm -7.2% |
| CTKM, giá, cơ chế | 43 | 16.7% | 60.0% | 61.3% | 🔥 Tăng +1.3% |
| TT SP | 22 | 0.0% | 51.2% | 54.9% | 🔥 Tăng +3.7% |
| Tin trung lập | 77 | 20.2% | 36.4% | 30.9% | 📉 Giảm -5.4% |

## IV. Minh chứng Thực tế về việc Khắc phục 4 Lỗi Hệ thống của V3 ở bản V4
Dưới đây là các ví dụ thực tế được bóc tách từ 5 file chạy phân loại để minh họa trực quan:

### 1. Khắc phục lỗi 'Competitor Override' (Hủy bỏ bộ lọc cứng thương hiệu đối thủ)
> **Lỗi của V3:** Khi khách hàng phàn nàn/khen sản phẩm Rạng Đông nhưng có so sánh với đối thủ, V3 chỉ gán các nhãn đối thủ mà xóa hoàn toàn nhãn chính của RĐ.
> **Giải pháp V4:** Giữ lại đầy đủ các nhãn nghiệp vụ cho cả hai thương hiệu.

* **Ví dụ 1:** "*Ổ cắm chịu tải Rạng Đông so với sản phẩm cùng loại của sopoka thì phần lõi sứ nhìn thô không chắc chắn bằng của sopoka, khi đấu nối dây vào ổ thì sản phẩm sopoka tiện lợi hơn*"
  - **Bản cũ (Old):** `['Báo lỗi', 'Y/c cải tiến']` | Sent: `Tiêu cực`
  - **Bản mới V3:** `['Hãng', 'TT SP']` | Sent: `Tiêu cực`
  - **Bản hiện tại V4:** `['Y/c cải tiến', 'Hãng', 'TT SP']` | Sent: `Tiêu cực`
  - **Ground Truth:** `['Hãng', 'TT SP']` | Sent: ``

* **Ví dụ 2:** "*attpmat rạng đông mẫu mã đẹp ,chắc chắn, giá thành cao hơn sino, mong cty ra thêm chương trình*"
  - **Bản cũ (Old):** `['Báo CL tốt', 'Đề xuất SPM', 'Tốt/ ko tốt']` | Sent: `Tiêu cực`
  - **Bản mới V3:** `['Hãng', 'CTKM, giá, cơ chế']` | Sent: `Tiêu cực`
  - **Bản hiện tại V4:** `['Báo CL tốt', 'Tốt/ ko tốt', 'Đề xuất', 'Hãng', 'CTKM, giá, cơ chế']` | Sent: `Tiêu cực`
  - **Ground Truth:** `['Báo CL tốt', 'Tốt/ ko tốt', 'Đề xuất']` | Sent: `Tiêu cực`

* **Ví dụ 3:** "*Lá đồng OC 10 nên làm dầy dặn hơn. So với sản phẩm tương tự của các hãng Omisu, sopoka,…, thì lá đồng của OC 10 Rạng Đông mỏng hơn .*"
  - **Bản cũ (Old):** `['Y/c cải tiến']` | Sent: `Tiêu cực`
  - **Bản mới V3:** `['Y/c cải tiến']` | Sent: `Tiêu cực`
  - **Bản hiện tại V4:** `['Y/c cải tiến', 'Hãng', 'TT SP']` | Sent: `Tiêu cực`
  - **Ground Truth:** `['Hãng', 'TT SP']` | Sent: ``

### 2. Phân định rõ ràng Báo lỗi vs Yêu cầu cải tiến (Mở rộng Báo lỗi chất lượng vật lý)
> **Lỗi của V3:** Xem tất cả phàn nàn nhẹ là 'Báo lỗi' hư hỏng vật lý hoặc ngược lại, V2 lại gộp hết thành Cải tiến.
> **Giải pháp V4:** Ranh giới rõ ràng. Lỗi hư hỏng/kém chất lượng (không sáng, đơ, hỏng...) = Báo lỗi. Yêu cầu đổi thiết kế vỏ hộp, thêm dây... = Y/c cải tiến.

* **Ví dụ 1:** "*Bulb a120n1/30w trắng lỗi kg sáng sau gần 1 năm sử dụng
241109120013A*"
  - **Bản cũ (Old):** `[]` | Sent: `Tiêu cực`
  - **Bản mới V3:** `['Báo lỗi']` | Sent: `Tiêu cực`
  - **Bản hiện tại V4:** `['Báo lỗi']` | Sent: `Tiêu cực`
  - **Ground Truth:** `['Báo lỗi']` | Sent: `Tiêu cực`

* **Ví dụ 2:** "*Bulb a120n1/30w trắng lỗi kg sáng sau gần 1 năm sử dụng
241109120013A*"
  - **Bản cũ (Old):** `[]` | Sent: `Tiêu cực`
  - **Bản mới V3:** `['Báo lỗi']` | Sent: `Tiêu cực`
  - **Bản hiện tại V4:** `['Báo lỗi']` | Sent: `Tiêu cực`
  - **Ground Truth:** `['Báo lỗi']` | Sent: `Tiêu cực`

* **Ví dụ 3:** "*Vbm rd 03 lô sx 240412120003A lỗi lưới không nổ điện *"
  - **Bản cũ (Old):** `[]` | Sent: `Tiêu cực`
  - **Bản mới V3:** `['Báo lỗi']` | Sent: `Tiêu cực`
  - **Bản hiện tại V4:** `['Báo lỗi']` | Sent: `Tiêu cực`
  - **Ground Truth:** `['Báo lỗi']` | Sent: `Tiêu cực`

### 3. Khắc phục lỗi bẫy sai chính tả (Spell Guard)
> **Lỗi của V3:** Bị lừa bởi lỗi sai chính tả đồng âm tiếng Việt, ví dụ: 'chưa tin thưởng' (tin tưởng) bị gán nhãn 'Trả thưởng'.
> **Giải pháp V4:** Spell Guard thông minh và các rule phủ định giúp bỏ qua các từ viết sai chính tả hoặc không mang nghĩa khuyến mãi thực tế.

* **Ví dụ 1:** "*aptomat Rạng Đông  mới được 1 năm thợ vẫn chưa tin thưởng , nhu cầu sử dụng của người dân thì chỉ chon pana, rất khó vào, chủ yếu bán lẻ,*"
  - **Bản cũ (Old):** `['Tốt/ ko tốt']` | Sent: `Tiêu cực`
  - **Bản mới V3:** `['Tốt/ ko tốt', 'Trả thưởng']` | Sent: `Tiêu cực`
  - **Bản hiện tại V4:** `['Tốt/ ko tốt', 'Hãng', 'TT SP']` | Sent: `Tiêu cực`
  - **Ground Truth:** `['Tốt/ ko tốt']` | Sent: `Tiêu cực`

### 4. Chuẩn hóa cảm xúc trung tính (Neutral Sentiment)
> **Lỗi của V3:** Gán cảm xúc tiêu cực quá đà cho các đóng góp mang tính trung lập, đề xuất khách quan.
> **Giải pháp V4:** Trả lại cảm xúc trống `""` (trung tính) cho các câu hỏi thông tin, đề xuất hoặc tin thị trường đối thủ.

* **Ví dụ 1:** "*Tấm pin NLMT cp03 đợt này làm nhỏ hơn các hãng*"
  - **Bản cũ (Old):** `['Y/c cải tiến']` | Sent: `Tiêu cực`
  - **Bản mới V3:** `['Y/c cải tiến']` | Sent: `Tiêu cực`
  - **Bản hiện tại V4:** `['Y/c cải tiến']` | Sent: ``
  - **Ground Truth:** `['Tin trung lập']` | Sent: ``

* **Ví dụ 2:** "*Aptomat cần cải tiến bao bì, *"
  - **Bản cũ (Old):** `[]` | Sent: ``
  - **Bản mới V3:** `['Y/c cải tiến']` | Sent: `Tiêu cực`
  - **Bản hiện tại V4:** `['Y/c cải tiến']` | Sent: ``
  - **Ground Truth:** `['Y/c cải tiến']` | Sent: ``

* **Ví dụ 3:** "*At58 in thêm logo rạng đông *"
  - **Bản cũ (Old):** `[]` | Sent: ``
  - **Bản mới V3:** `['Y/c cải tiến']` | Sent: `Tiêu cực`
  - **Bản hiện tại V4:** `['Y/c cải tiến']` | Sent: ``
  - **Ground Truth:** `['Y/c cải tiến']` | Sent: ``