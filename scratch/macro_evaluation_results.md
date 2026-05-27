# BÁO CÁO ĐÁNH GIÁ ĐA NHÃN CHI TIẾT (LABEL-WISE EVALUATION)
Quy mô dữ liệu gộp: 839 dòng phản hồi từ 5 file.

## I. Chỉ số Trung bình Không thiên lệch (Unbiased Macro-averages)
> [!IMPORTANT]
> Điểm **Macro-average** tính toán bằng cách lấy trung bình cộng độc lập của từng nhãn, giúp loại bỏ hoàn toàn bias (thiên lệch) của các nhãn chiếm đa số và đánh giá chính xác chất lượng phân loại trên các nhãn hiếm/mất cân bằng dữ liệu.
- **Macro-average Accuracy (Độ chính xác trung bình từng nhãn của Bản cũ):** 94.77%
- **Macro-average F1-Score (Điểm F1 trung bình của Bản cũ):** 42.91%
- **Macro-average Precision (Độ xác thực trung bình của Bản cũ):** 51.73%
- **Macro-average Recall (Độ phủ trung bình của Bản cũ):** 41.42%

## II. Bảng chỉ số chi tiết cho từng cột nhãn (Label-wise Metrics)
Bảng dưới đây sắp xếp các nhãn theo thứ tự xuất hiện, thể hiện rõ nhãn nào bản cũ phân loại tốt và nhãn nào bị sai lệch nhiều:
| Nhãn | Độ chính xác (Accuracy) | Precision | Recall (Độ phủ) | F1-Score | Số mẫu thực tế (Support) | Đánh giá chất lượng của Bản cũ |
|---|---|---|---|---|---|---|
| Báo lỗi | 88.9% | 77.6% | 71.4% | 74.4% | 189 | Khá ⚠️ |
| Báo CL tốt | 97.5% | 75.0% | 80.0% | 77.4% | 45 | Khá ⚠️ |
| Y/c cải tiến | 88.2% | 70.8% | 52.4% | 60.2% | 143 | Kém (Sai lệch nhiều) ❌ |
| Đề xuất SPM | 94.8% | 75.7% | 66.2% | 70.7% | 80 | Khá ⚠️ |
| Bảng giá, Catalogue | 99.4% | 0.0% | 0.0% | 0.0% | 1 | Sai hoàn toàn / Bỏ sót ❌❌ |
| Bảng biển | 97.9% | 75.0% | 65.6% | 70.0% | 32 | Khá ⚠️ |
| Kệ bóng, thử đèn,… | 96.5% | 28.6% | 47.1% | 35.6% | 17 | Kém (Sai lệch nhiều) ❌ |
| Khác | 99.9% | 50.0% | 100.0% | 66.7% | 1 | Kém (Sai lệch nhiều) ❌ |
| Tốt/ ko tốt | 91.3% | 60.3% | 50.0% | 54.7% | 88 | Kém (Sai lệch nhiều) ❌ |
| Trả thưởng | 97.5% | 21.1% | 40.0% | 27.6% | 10 | Kém (Sai lệch nhiều) ❌ |
| Đề xuất | 97.0% | 21.4% | 17.6% | 19.4% | 17 | Kém (Sai lệch nhiều) ❌ |
| Bảo hành | 97.9% | 78.3% | 58.1% | 66.7% | 31 | Kém (Sai lệch nhiều) ❌ |
| HTPP | 97.9% | 69.2% | 39.1% | 50.0% | 23 | Kém (Sai lệch nhiều) ❌ |
| Hàng hoá | 97.3% | 75.0% | 38.7% | 51.1% | 31 | Kém (Sai lệch nhiều) ❌ |
| Hàng giả | 99.9% | 0.0% | 0.0% | 0.0% | 1 | Sai hoàn toàn / Bỏ sót ❌❌ |
| Website | 99.6% | 0.0% | 0.0% | 0.0% | 1 | Sai hoàn toàn / Bỏ sót ❌❌ |
| Hãng | 84.3% | 91.4% | 46.5% | 61.6% | 228 | Kém (Sai lệch nhiều) ❌ |
| Hoạt động | 94.0% | 53.6% | 28.8% | 37.5% | 52 | Kém (Sai lệch nhiều) ❌ |
| CTKM, giá, cơ chế | 85.9% | 79.6% | 28.7% | 42.2% | 150 | Kém (Sai lệch nhiều) ❌ |
| TT SP | 90.5% | 60.0% | 3.7% | 7.0% | 81 | Kém (Sai lệch nhiều) ❌ |
| Tin trung lập | 94.0% | 23.8% | 35.7% | 28.6% | 28 | Kém (Sai lệch nhiều) ❌ |