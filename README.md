# longbui

## IPC Scan – Số hóa biểu mẫu IPC giấy

Web app giúp IPC (nhân viên kiểm soát trong quá trình sản xuất) đưa dữ liệu từ biểu mẫu giấy
(form in sẵn, điền tay) vào database:

```
Chụp ảnh điện thoại / Scan PDF  →  AI (Claude) đọc chữ viết tay  →  IPC đối chiếu ảnh gốc, sửa, duyệt  →  Database (SQLite)  →  Xuất Excel/CSV (→ Lark Base)
```

**Giai đoạn 1 (đã có):** upload, trích xuất AI, duyệt/sửa, audit trail, xuất Excel/CSV.
**Giai đoạn 2 (sau):** đồng bộ tự động sang Lark Base qua Open API (cột `lark_record_id`, `synced_at` đã có sẵn trong database).

### Tính năng

- **Upload từ điện thoại** (nút "Chụp ảnh" mở camera) hoặc **PDF nhiều trang từ máy scan** – mỗi trang là 1 bản ghi.
- **Tự nhận diện loại biểu mẫu** hoặc chọn trước.
- AI trả về dữ liệu đúng cấu trúc của từng biểu mẫu, kèm **mức tin cậy từng ô**.
- Màn hình duyệt: ảnh gốc (phóng to được) bên cạnh dữ liệu.
  - Ô **tô vàng**: AI đọc không chắc chắn → cần đối chiếu kỹ.
  - Ô **viền đỏ**: ngoài tiêu chuẩn (min/max khai báo trong template); **viền xanh**: đạt.
  - Kết luận Đạt/Không đạt tự tính lại ngay khi sửa.
- **Toàn vẹn dữ liệu (ALCOA+):** lưu file gốc, giá trị AI đọc và giá trị sau sửa riêng biệt; mọi thay đổi ghi vào lịch sử
  (ai sửa, lúc nào, giá trị cũ → mới, lý do). Sửa bản ghi đã duyệt bắt buộc ghi lý do và phải duyệt lại.
- **Xuất Excel** 2 sheet `Header` (1 dòng/biểu mẫu) và `Chi tiet` (1 dòng/lần đo) – import thẳng vào 2 bảng Lark Base; hoặc CSV phẳng.

### Cài đặt & chạy

Yêu cầu: Python 3.10+.

```bash
pip install -r requirements.txt
cp .env.example .env            # điền ANTHROPIC_API_KEY và IPC_SECRET_KEY

# tạo tài khoản (nhập PIN 4-8 số)
python -m app.manage add-user ipc01 "Nguyễn Văn A"
python -m app.manage add-user qa01 "Trần Thị B" --role qa

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Mở `http://<ip-máy-chủ>:8000` trên điện thoại (cùng mạng nội bộ). Khi triển khai chính thức nên đặt sau
reverse proxy có HTTPS (nginx/Caddy) và đổi `IPC_SECRET_KEY`.

### Thêm / sửa biểu mẫu

Mỗi loại biểu mẫu là 1 file YAML trong `templates/` – thêm file mới là app nhận ngay, không cần sửa code.
Xem 2 file mẫu: `ipc_weight_check.yaml` (cân khối lượng) và `ipc_inprocess_check.yaml` (pH, độ nhớt, cảm quan).

```yaml
code: IPC-WC                       # mã biểu mẫu (duy nhất)
name: Kiểm tra khối lượng đóng gói
description: >                     # mô tả bố cục form – giúp AI nhận diện và đọc đúng
  Phần đầu có thông tin sản phẩm, số lô... Bảng dưới ghi khối lượng 10 mẫu/lần.
header_fields:                     # các ô thông tin chung
  - {key: batch_no, label: Số lô, type: text}
table:                             # bảng kết quả, mỗi dòng 1 lần đo
  label: Kết quả cân
  columns:
    - {key: net_weight, label: Khối lượng tịnh, type: number, unit: g, min: 49.0, max: 51.5}
```

- `type`: `text` | `number` | `date` | `time`. `min`/`max` chỉ dùng cho `number`.
- Nhãn có dấu phẩy phải đặt trong ngoặc kép: `label: "Cảm quan (màu, mùi)"`.
- Kiểm tra file: `python -m app.manage list-templates`.

### Mẹo để AI đọc chính xác

- Chụp thẳng, đủ sáng, thấy đủ 4 góc; tránh bóng tay/bóng đèn.
- Máy scan: 200–300 dpi, ảnh màu hoặc xám.
- Viết `description` của template càng giống form thật càng tốt (vị trí các ô, số dòng của bảng, ký hiệu hay dùng như "Đ"/"KĐ").
- Về lâu dài có thể cải tiến form giấy: ô chữ số tách rời, ghi rõ đơn vị, in mã QR chứa mã form.

### Chi phí AI

Mặc định dùng `claude-opus-5-5` (chính xác nhất cho chữ viết tay). Mỗi trang ~1 lần gọi API; nếu chọn "Tự nhận diện" thì thêm 1 lần gọi ngắn.
Có thể đổi `CLAUDE_MODEL=claude-sonnet-5-5` hoặc `claude-haiku-5-5` trong `.env` để giảm chi phí – nên thử trên vài chục form thật
và so sánh tỷ lệ ô phải sửa trước khi quyết định.

### Cấu trúc mã nguồn

```
app/
  main.py              API (FastAPI) + phục vụ giao diện
  extractor.py         gọi Claude API, sinh JSON schema từ template
  templates_loader.py  đọc template YAML, so sánh tiêu chuẩn min/max
  pdf_utils.py         tách PDF thành trang, xoay/thu nhỏ ảnh
  db.py                SQLite: users, documents, field_edits (audit), events
  exporter.py          xuất Excel / CSV
  manage.py            lệnh quản trị (tạo user, kiểm tra template)
  static/              giao diện web (index.html, review.html)
templates/             biểu mẫu YAML
data/                  database + file gốc (tự tạo, không commit)
tests/                 pytest (AI được giả lập, không tốn phí)
```

### Chạy test

```bash
pip install -r requirements-dev.txt
pytest
```
