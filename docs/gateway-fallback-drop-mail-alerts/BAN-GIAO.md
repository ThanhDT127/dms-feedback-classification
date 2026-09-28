# Bàn giao — gateway-fallback-drop-mail-alerts

## Việc phải làm tay trên máy chạy production

`.env` thật không theo git, nên change này không đụng được vào nó. Trước lần chạy kế tiếp,
xoá 6 dòng sau khỏi `.env` trên máy production:

```
MAIL_TRANSPORT=...
SMTP_HOST=...
SMTP_PORT=...
SMTP_USERNAME=...
SMTP_USE_TLS=...
SMTP_PASSWORD=...
```

Để lại cũng không gây lỗi — `config.py` không còn đọc chúng nữa — nhưng `SMTP_PASSWORD` là
app password thật, không có lý do gì để nó nằm lại trên đĩa.

## Đổi thay người vận hành cần biết

Sự cố Gateway **không còn tự gửi thư**. Từ nay muốn biết phải đọc nhật ký:

```
Tìm trong log xoay vòng của pipeline (RotatingFileHandler, 10MB × 5 file):

  [FALLBACK] DOI DUONG: Gateway mat ket noi 3 lan lien tiep -> goi thang nha cung cap
  [FALLBACK] cau loi cuoi cung tu Gateway: <câu lỗi thật>
  [FALLBACK] luot 1 di duong thang (ly do: Gateway mat ket noi)
  [FALLBACK] luot 2 di duong thang ...
  [FALLBACK] VE DUONG CU: Gateway song lai sau 74 giay, 12 luot da di duong thang
```

Lọc nhanh: `grep "\[FALLBACK\]" <file log>`

**Bẫy khi đọc con số cuối:** `12 luot` là số **dồn từ đầu tiến trình**, không phải của riêng
sự cố vừa rồi. Sự cố thứ hai trở đi sẽ cộng dồn cả sự cố trước. Chỉ `74 giay` là đo đúng
riêng sự cố đó. Xem Open Questions trong `design.md`.
