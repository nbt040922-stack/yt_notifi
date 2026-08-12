# Báo cáo Phase 4.1 — Local Channel Management Dashboard

## Kiến trúc

Dashboard là HTML/CSS/JavaScript thuần được FastAPI phục vụ tại `GET /`. Không có framework frontend, Node, build step, WebSub, Cloudflare hoặc endpoint công khai.

```text
Dashboard / API
       ↓
ChannelStore + atomic channels.json
       ↓
Poller reload mỗi chu kỳ 10 giây
       ↓
SQLite baseline + dedupe
       ↓
Telegram
```

Detector polling, yt-dlp probe, SQLite dedupe và Telegram lifecycle Phase 3.3 không bị viết lại.

## API

- `GET /api/status`: watcher, chu kỳ poll, yt-dlp, Telegram, số kênh bật, video mới gần nhất và lỗi config
- `GET /api/channels`: cấu hình kênh ghép với poll state
- `POST /api/channels`: nhận raw Channel ID hoặc URL `/channel/UC...`; mặc định bật
- `PATCH /api/channels/{channel_id}`: bật hoặc tắt
- `DELETE /api/channels/{channel_id}`: ngừng theo dõi, không xóa lịch sử SQLite

Lỗi có dạng thống nhất:

```json
{
  "error": "CHANNEL_ALREADY_EXISTS",
  "message": "Kênh này đã có trong danh sách theo dõi."
}
```

Các mã gồm `INVALID_CHANNEL_ID`, `CHANNEL_ALREADY_EXISTS`, `CHANNEL_NOT_FOUND`, `INVALID_REQUEST`, `CONFIG_INVALID`.

## ChannelStore và atomic write

`app/channel_store.py` sở hữu toàn bộ load, validate, add, update, remove và generation. Read-modify-write nằm trong một `threading.Lock`, phù hợp với single-process launcher hiện tại.

Save dùng file `channels.json.tmp`, UTF-8, JSON indent xác định, `flush`, `fsync`, rồi `os.replace`. File tạm được dọn nếu thao tác lỗi. Hai request đồng thời không mất update.

Nếu JSON hỏng hoặc có Channel ID trùng, mutation bị từ chối. File gốc không bị ghi đè hoặc tự thay bằng danh sách rỗng.

## Hot reload

Poller gọi channel loader đầu mỗi chu kỳ. Danh sách nhỏ nên không cần filesystem watcher. Kênh thêm/bật xuất hiện trong chu kỳ kế tiếp; kênh tắt/xóa không vào các chu kỳ sau.

Nếu config lỗi trong lúc chạy, poller ghi `CHANNEL_CONFIG_FAILED` và giữ danh sách hợp lệ gần nhất thay vì crash. Dashboard báo lỗi config và từ chối mutation cho tới khi file được sửa.

Probe đang chạy có thể hoàn tất sau khi kênh vừa bị tắt/xóa. Đây là race được chấp nhận; chu kỳ sau dùng config mới và dừng poll kênh đó.

## Baseline khi thêm và bật lại

Thêm kênh hoặc chuyển `disabled` sang `enabled` gọi `reset_poll_baseline(channel_id)`. Hàm chỉ đặt poll state về chưa khởi tạo; không xóa video, notification hoặc dedupe history.

Poll thành công đầu tiên sau đó:

- ghi video chưa thấy thành baseline
- giữ video đã biết là duplicate
- không gửi Telegram cho video xuất hiện trong thời gian kênh bị tắt
- chuyển poll state lại initialized

Xóa rồi thêm lại cũng áp dụng baseline mới, nên không tạo backlog spam.

## Dashboard UX

Trang cục bộ hiển thị:

- watcher đang chạy
- poll interval, yt-dlp, Telegram và số kênh bật
- tên, Channel ID, trạng thái, poll/success gần nhất, video ID gần nhất, số lỗi
- form thêm kênh không reload trang
- nút bật/tắt cập nhật bằng fetch
- xác nhận trước khi xóa và nhắc lịch sử được giữ
- thông báo thành công/lỗi ngắn
- layout desktop và mobile đơn giản

Handle URL không được hỗ trợ vì không thể resolve tin cậy nếu không thêm API key hoặc scraping. URL `/channel/UC...` và raw ID được hỗ trợ đầy đủ. Tên thiếu dùng fallback `Channel UCxxxxxx...`.

## Automated tests

- Lệnh: `python -m pytest -q`
- Kết quả: **55 passed, 1 warning**
- Warning duy nhất: deprecation từ Starlette TestClient/httpx
- Python compile: PASS
- PowerShell syntax: PASS
- Bao phủ GET, add ID/URL, validation, duplicate, fallback name, enable, disable, remove, giữ SQLite, re-add baseline, atomic write, concurrent mutation, malformed JSON, hot reload add/disable, baseline không notify, re-enable không backlog, remove khi probe đang chạy, dashboard load, secret safety, polling/Telegram/launcher regressions và không tái sinh WebSub/Cloudflare

## Manual validation

Đã chạy `start.bat` thật:

```text
yt-dlp        OK
Watcher       OK
Polling       10 seconds
Dashboard     http://127.0.0.1:8787/
Status        RUNNING
```

Kiểm tra bằng trình duyệt cục bộ:

- Dashboard tải đúng, bố cục sạch; không có console error
- Global status: 10s, yt-dlp READY, Telegram CONFIGURED
- Kênh và poll state thật hiển thị đúng
- Form mở/đóng không reload; duplicate hiển thị lỗi tại form
- Tắt kênh cập nhật ngay thành Disabled và enabled count = 0
- Một probe in-flight hoàn tất; hai chu kỳ sau không đổi last poll
- Bật lại cập nhật thành Waiting for first poll
- Poll kế tiếp ghi `POLL_BASELINE ... videos=2`, không có Telegram send mới, rồi trạng thái Healthy
- Watcher PID không đổi trong toàn bộ thao tác
- `scripts/status.ps1` vẫn hoạt động
- Cấu hình người dùng được khôi phục đúng sau kiểm thử

Add/remove thành công được kiểm tra tự động trên config và database tạm để không chèn kênh thử vào dữ liệu thật của người dùng.

## Live validation

Chưa thực hiện publish video public mới trong lượt này. Vì vậy chưa xác nhận trọn chuỗi dashboard add, baseline, upload, Telegram đúng một lần, disable/upload và re-enable trên video thật. Không giả lập PASS.

## Giới hạn đã biết

- Dashboard chỉ an toàn mặc định khi watcher bind `127.0.0.1`; không có authentication cho remote exposure.
- Lock là process-local, phù hợp single-instance launcher. Không hỗ trợ nhiều process cùng sửa một config.
- Dashboard hiển thị video ID gần nhất; chưa thêm migration title vì không cần cho quản lý kênh.
- Thay đổi file JSON trực tiếp vẫn được hot reload, nhưng dashboard là đường mutation an toàn được khuyến nghị.

PHASE 4.1 IMPLEMENTATION COMPLETE — MANUAL VALIDATION REQUIRED
