# ⚡ Dịch vụ phân loại phản hồi DMS

Tài liệu tiếng Việt. Bản tiếng Anh: [README.md](README.md).

[![Python Version](https://img.shields.io/badge/Python-3.11%20%7C%203.12-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Docker Compose](https://img.shields.io/badge/Docker-compose-2496ED?style=flat-square&logo=docker&logoColor=white)](https://www.docker.com/)
[![License](https://img.shields.io/badge/license-MIT-green?style=flat-square)](LICENSE)
[![Tests Passed](https://img.shields.io/badge/tests-93%20passed-success?style=flat-square&logo=pytest&logoColor=white)]()

Một hệ thống phân loại phản hồi thị trường và khách hàng hybrid Machine Learning & LLM (Gemini) chuẩn doanh nghiệp. Dịch vụ tự động quét các file Excel mới từ Microsoft SharePoint, trích xuất thông tin sản phẩm và đối chiếu model bằng luồng RAG tùy chỉnh, phân loại 21 nhãn vấn đề phản hồi, gửi thông báo qua Teams/Email và cung cấp một giao diện Web Dashboard quản trị thời gian thực trực quan.

---

## 📌 Mục lục

* [Giới thiệu dự án](#-giới-thiệu-dự-án)
* [Công nghệ sử dụng](#-công-nghệ-sử-dụng)
* [Cấu trúc thư mục](#-cấu-trúc-thư-mục)
* [Danh mục nhãn phân loại (21 Phân loại con)](#-danh-mục-nhãn-phân-loại-21-phân-loại-con)
* [Cấu trúc Cột và Dữ liệu Excel](#-cấu-trúc-cột-và-dữ-liệu-excel)
* [Khởi động nhanh](#-khởi-động-nhanh)
  * [Điều kiện bắt buộc](#điều-kiện-bắt-buộc)
  * [Thiết lập môi trường local](#thiết-lập-môi-trường-local)
  * [Chạy bằng Docker Compose](#chạy-bằng-docker-compose)
* [Chạy thử cục bộ với dữ liệu mẫu](#-chạy-thử-cục-bộ-với-dữ-liệu-mẫu)
* [Thiết kế kỹ thuật & Kiến trúc](#-thiết-kế-kỹ-thuật--kiến-trúc)
  * [Phân loại Hybrid ML & LLM](#1-phân-loại-hybrid-ml--llm)
  * [Luồng đối chiếu RAG BM25 + LLM](#2-luồng-đối-chiếu-rag-bm25--llm)
  * [Đồng bộ trạng thái tự phục hồi (Self-Healing)](#3-đồng-bộ-trạng-thái-tự-phục-hồi-self-healing)
* [Kiểm thử & Bảo đảm chất lượng](#-kiểm-thử--bảo-đảm-chất-lượng)
* [Bảo mật và Làm sạch Dữ liệu](#-bảo-mật-và-làm-sạch-dữ-liệu)
* [Tài liệu vận hành chi tiết](#-tài-liệu-vận-hành-chi-tiết)

---

## 📖 Giới thiệu dự án

Xử lý phản hồi thị trường trên quy mô lớn gặp phải hai thách thức chính: duy trì độ chính xác cao đối với các thuật ngữ chuyên ngành (như lĩnh vực chiếu sáng, thiết bị điện) và tích hợp mượt mà với hệ thống lưu trữ doanh nghiệp như Microsoft SharePoint.

**Dịch vụ phân loại phản hồi DMS** giải quyết bài toán này bằng cách triển khai một container chạy nền (watcher) liên tục quét thư mục, chạy dữ liệu qua bộ phân loại kép ML/LLM, xuất kết quả ra file Excel đã được định dạng và gửi cảnh báo qua Email/Teams. Hệ thống cũng đi kèm một giao diện Web UI Dashboard (FastAPI backend + Vanilla JS frontend) để giám sát tài nguyên, chạy phân loại thủ công và quản lý file.

---

## 🛠️ Công nghệ sử dụng

Dưới đây là các công nghệ cốt lõi cấu thành nên dự án:

[![My Skills](https://skillicons.dev/icons?i=py,docker,fastapi,gcp,azure,git,vscode,githubactions,markdown,svg)](https://skillicons.dev)

* **Framework Backend:** FastAPI & Uvicorn (REST API không đồng bộ, WebSockets stream log thời gian thực).
* **Mô hình AI & LLM:** Google GenAI (Gemini 2.5 Flash Lite Vertex/API support), scikit-learn (TF-IDF + OvR Logistic Regression).
* **Xử lý dữ liệu & IR:** pandas, openpyxl, rank-bm25 (BM25 Okapi), rapidfuzz (so khớp khoảng cách Levenshtein dự phòng).
* **Ủy quyền & Tích hợp:** Microsoft MSAL (Microsoft Authentication Library để gửi yêu cầu Graph API).
* **DevOps:** Docker Multi-stage builds, Docker Compose.

---

## 📂 Cấu trúc thư mục

```text
DMS/
├── LICENSE                    # File giấy phép MIT
├── README.md                  # Tài liệu tiếng Anh
├── README.vi.md               # Tài liệu tiếng Việt
├── OPERATIONS.md              # Hướng dẫn deploy & vận hành chi tiết
├── OPERATIONS.vi.md           # Hướng dẫn vận hành (Tiếng Việt)
├── openspec/                  # Thư mục chứa đặc tả kiến trúc (31 spec BDD)
│   └── specs/
│       ├── issue-llm-classification/spec.md
│       ├── sharepoint-watcher/spec.md
│       └── ...
├── sample_data/               # Thư mục chứa dữ liệu mẫu để test offline
│   └── sample_feedback.xlsx   # 10 dòng phản hồi mẫu
└── service/                   # Thư mục gốc dịch vụ
    ├── Dockerfile             # Định nghĩa container production
    ├── docker-compose.yml     # Điều phối watcher & Web UI
    ├── pyproject.toml         # Cấu hình đóng gói PEP 518 và linting
    ├── requirements.txt       # Danh sách dependencies thư viện
    ├── Keyword/               # Hệ từ khóa và danh mục sản phẩm mẫu được commit
    ├── Model/                 # Các artifact model baseline (TF-IDF, LogReg)
    ├── src/
    │   └── dms/               # Package ứng dụng chính
    │       ├── pipeline/      # Đường ống xử lý AI cốt lõi
    │       │   ├── issue_classifier.py  # Phân loại LLM + post-processors
    │       │   ├── rag_product.py       # Trích xuất RAG BM25 + LLM
    │       │   └── runner.py            # Quản lý chạy file & ghi checkpoint
    │       ├── web/           # Backend FastAPI
    │       │   ├── api/       # API endpoints (Files, Settings, Classify)
    │       │   └── app.py     # Vòng đời ứng dụng FastAPI
    │       ├── watcher.py     # Poller SharePoint & tự động phục hồi state
    │       └── settings.py    # Cấu hình Pydantic-settings
    ├── static/                # Giao diện Web Dashboard (Vanilla JS SPA)
    └── tests/                 # Bộ kiểm thử Unit & Integration (93 test cases)
```

---

## 🏷️ Danh mục nhãn phân loại (21 Phân loại con)

Hệ thống phân loại phản hồi của thị trường thành **21 nhãn phân loại con** thuộc **7 nhóm nhãn lớn**:

| Nhóm lớn | Nhãn con | Mô tả chi tiết / Hướng dẫn phân loại |
| :--- | :--- | :--- |
| **Sản phẩm** | Báo lỗi | Sản phẩm bị lỗi vật lý, hỏng hóc kỹ thuật, cháy bóng, hỏng chấn lưu, nứt vỡ. |
| | Báo CL tốt | Khen ngợi chất lượng sản phẩm tốt, độ bền cao, ánh sáng ổn định, khách hàng ưng ý. |
| | Y/c cải tiến | Góp ý, phàn nàn về thiết kế, kích thước, độ dày mỏng của vỏ/thanh đồng, vỏ hộp. |
| | Đề xuất SPM | Đề xuất sản xuất mã sản phẩm mới, công suất mới chưa được Rạng Đông sản xuất. |
| **Yêu cầu công cụ BH** | Bảng giá, Catalogue | Yêu cầu gửi bảng giá, catalogue, tài liệu thông số kỹ thuật. |
| | Bảng biển | Đăng ký hoặc hỏi tiến độ lắp đặt biển hiệu quảng cáo, bảng quảng cáo đại lý. |
| | Kệ bóng, thử đèn,… | Yêu cầu kệ trưng bày, kệ test bóng, tủ bóng thử đèn. |
| | Khác | Yêu cầu các POSM khác như áo đồng phục, sổ tay, tờ rơi bán hàng. |
| **Giá, cơ chế RD** | Tốt/ ko tốt | Nhận xét về tính cạnh tranh của giá cả, mức chiết khấu, dễ/khó bán của Rạng Đông. |
| | Trả thưởng | Hỏi hoặc phản ánh về tiền thưởng, chương trình c2td, chương trình quay số. |
| | Đề xuất | Đề nghị thay đổi chính sách giá, chương trình khuyến mãi chung của công ty. |
| **Dịch vụ** | Bảo hành | Quy trình đổi trả hàng bảo hành, thời gian bảo hành, thái độ phục vụ hậu mãi. |
| | HTPP | Tranh chấp kênh phân phối, đại lý tràn vùng lấn vùng bán phá giá. |
| | Hàng hoá | Vấn đề kho vận, logistics: giao thiếu hàng, sai quy cách, đóng gói hỏng, giao chậm. |
| **Hàng giả** | Hàng giả | Nghi ngờ hàng giả, hàng nhái thương hiệu Rạng Đông trên thị trường. |
| **Website** | Website | Lỗi phần mềm/app: lỗi cổng đăng nhập portal, app DMS bị đơ, không lên đơn được. |
| **Đối thủ cạnh tranh** | Hãng | Ghi nhận tên của thương hiệu đối thủ cạnh tranh được nhắc tới trong câu. |
| | Hoạt động | Chương trình marketing, truyền thông, tặng quà, trưng bày của đối thủ. |
| | CTKM, giá, cơ chế | Khuyến mãi chiết khấu, giá bán của sản phẩm đối thủ cạnh tranh. |
| | TT SP | Mẫu mã sản phẩm mới, thông số kỹ thuật, catalogue của đối thủ. |
| **Tin trung lập** | Tin trung lập | Các câu trung tính, không chứa ý kiến khen chê hay yêu cầu cụ thể. |

---

## 📊 Cấu trúc Cột và Dữ liệu Excel

Hệ thống tự động phát hiện và bổ sung thông tin vào file Excel đầu vào:
1. **Phát hiện cột đầu vào:** Tự động nhận diện cột chứa văn bản phản hồi thông qua các tên cột phổ biến (như `Nội dung phản hồi`, `Nội dung`,...).
2. **Cấu trúc cột đầu ra (Enriched columns):**
   * **Thông tin sản phẩm (Chèn cạnh cột văn bản):**
     * `Sản phẩm`: Phân nhóm sản phẩm lớn (VD: Đèn LED).
     * `Dòng SP`: Dòng sản phẩm (VD: Bulb).
     * `Model`: Model chính xác trong catalog (VD: AT10 9W).
     * `Lớp` & `Điểm`: Đầu ra phân tích xác suất của mô hình ML baseline.
   * **Thông tin bổ sung (Phần đuôi):**
     * `Sentiment`: Nhận diện `Tích cực`, `Tiêu cực`, hoặc để trống cho trung lập.
     * `LLM_Extracted`: Cụm từ sản phẩm gốc trích xuất từ câu.
     * `BM25_Score`: Điểm độ tin cậy đối chiếu của BM25.
   * **Cột nhãn phân loại (Phần đuôi):** 21 cột tương ứng với các **Nhãn con** phía trên. Điền chữ `x` nếu nhãn được kích hoạt (hoặc ghi tên thương hiệu đối thủ tại cột `Hãng`).

---

## 🚀 Khởi động nhanh

### Điều kiện bắt buộc
* [Docker & Docker Compose](https://www.docker.com/) (khuyến nghị)
* Python 3.11+ (nếu chạy trực tiếp không qua Docker)

### Thiết lập môi trường local
1. Clone dự án:
   ```bash
   git clone https://github.com/ThanhDT127/dms-feedback-classification.git
   cd dms-feedback-classification/service
   ```
2. Tạo file cấu hình cục bộ:
   ```bash
   cp .env.example .env
   ```
3. Chỉnh sửa file `.env` điền đầy đủ thông tin SharePoint Drive, Azure AD OAuth2 hoặc API key Gemini.
4. Nếu chạy qua Vertex AI (khuyến nghị cho Production), đặt file service account key tại:
   ```text
   service/testvertex.json
   ```

### Chạy bằng Docker Compose
Khởi động watcher và Web UI chạy nền:
```bash
docker compose up -d
```
Xem trạng thái container:
```bash
docker compose ps
```
Xem log container trực tiếp:
```bash
docker compose logs -f
```
Giao diện Web UI sẽ khả dụng tại địa chỉ: **http://localhost:8501**

---

## 🧪 Chạy thử cục bộ với dữ liệu mẫu

Bạn có thể chạy thử quy trình phân loại offline mà không cần kết nối tới SharePoint:
1. Đảm bảo đã điền `GEMINI_API_KEY` (hoặc cấu hình `testvertex.json`) trong `.env`.
2. Truy cập Web Dashboard tại **http://localhost:8501**.
3. Đi tới tab **File Management**, upload tệp Excel mẫu có sẵn trong dự án:
   * [service/sample_data/sample_feedback.xlsx](sample_data/sample_feedback.xlsx)
4. Chuyển sang tab **Classify**, kích hoạt chạy thủ công (manual run) và giám sát thanh tiến trình xử lý thời gian thực.
5. Tải file Excel kết quả đã được phân loại về máy sau khi chạy xong.

---

## 📐 Thiết kế kỹ thuật & Kiến trúc

### 1. Phân loại Hybrid ML & LLM
Hệ thống sử dụng mô hình kết hợp (hybrid):
* **Giai đoạn 1 (ML Baseline):** Sử dụng các vectorizer TF-IDF (n-gram ký tự và từ) kết hợp bộ phân loại Logistic Regression cục bộ để dự đoán nhanh xác suất các nhãn.
* **Giai đoạn 2 (LLM Refinement):** Gửi phản hồi kèm theo các gợi ý nhãn baseline sang Gemini 2.5 Flash Lite. LLM sẽ giải quyết các ranh giới ngữ nghĩa mơ hồ (VD: *Báo lỗi* vs *Y/c cải tiến*) để xuất ra định dạng JSON cấu trúc.
* **Giai đoạn 3 (Post-Processing):** Code Python áp dụng các ràng buộc nghiệp vụ (ví dụ: xóa nhãn đối thủ nếu không có hãng đối thủ, xóa nhãn Trung lập nếu có nhãn khác) để đảm bảo dữ liệu đầu ra Excel chuẩn xác nhất.

### 2. Luồng đối chiếu RAG BM25 + LLM
Để ánh xạ chính xác tên viết tắt, từ lóng của đại lý vào catalog sản phẩm Rạng Đông:
1. **LLM Extraction:** Gemini trích xuất cụm tên + model sản phẩm thô từ phản hồi.
2. **Dual-Index BM25 Search:** Tìm kiếm song song trên 2 chỉ mục BM25 (một chỉ mục văn bản gốc, một chỉ mục không dấu qua `unidecode`).
3. **Keyword Fallback:** Nếu điểm BM25 quá thấp, hệ thống chuyển sang tra cứu biểu thức chính quy (regex) dựa trên hệ luật Level 2 và Level 3.

### 3. Đồng bộ trạng thái tự phục hồi (Self-Healing)
Để tránh xử lý lặp lại file trên SharePoint khi restart container hoặc chuyển VM:
* Khi khởi động, watcher quét thư mục `Output/` của SharePoint.
* Đối chiếu file thực tế trên SharePoint với danh sách đã xử lý cục bộ (`seen_files.json`) và tự động cập nhật các file bị thiếu để đồng bộ trạng thái tự động.

---

## 🧪 Kiểm thử & Bảo đảm chất lượng

Các test suite được viết bằng `pytest`. Toàn bộ các kết nối mạng bên ngoài và API Gemini đều được mock độc lập.

Cách chạy bộ test:
1. Di chuyển vào thư mục dịch vụ:
   ```bash
   cd service
   ```
2. Chạy pytest:
   ```bash
   python -m pytest
   ```
Bộ test gồm **93 test cases** giúp đảm bảo tính đúng đắn của logic watcher, validation cấu hình, phân tích Excel và bảo mật tệp.

---

## 🔒 Bảo mật và Làm sạch Dữ liệu

> [!IMPORTANT]
> Tất cả các ý kiến phản hồi của khách hàng, mã sản phẩm, danh sách nhà phân phối và thông tin xác thực GCP/Azure xuất hiện trong repository này đều là dữ liệu giả lập (mocked) hoặc đã được làm sạch hoàn toàn để tuân thủ chính sách bảo mật dữ liệu doanh nghiệp.

---

## 📖 Tài liệu vận hành chi tiết

* [OPERATIONS.vi.md](OPERATIONS.vi.md) - Hướng dẫn chi tiết cách triển khai production, cấu hình sync asset tự động, chạy script hồi phục dữ liệu cũ và xử lý sự cố.
* [service/README.md](service/README.md) - Tài liệu kiến trúc sâu hơn dành cho nhà phát triển, giải thích cơ chế Dependency Injection và thiết kế API.
