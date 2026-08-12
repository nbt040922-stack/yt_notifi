# YT_NOTIFI — Bộ phát hiện hybrid giai đoạn 2.1

Dịch vụ chạy cục bộ, phát hiện video mới bằng YouTube WebSub và polling dự phòng qua yt-dlp. Cả hai đường dùng chung SQLite dedupe và Telegram lifecycle. Không cần YouTube API key, VPS hoặc dịch vụ trả phí.

Giai đoạn 2.1 không tải media và chưa tích hợp Silence Cutter.

## Khởi động một lần bấm

Sau khi hoàn tất `.env`, `.venv` và đặt `cloudflared.exe`, double-click:

```text
start.bat
```

Launcher tự:

1. Kiểm tra `.venv\Scripts\python.exe`, yt-dlp và cloudflared
2. Chặn launcher thứ hai bằng Windows named mutex
3. Khởi động watcher và chờ local `/health`
4. Chỉ sau khi watcher khỏe mới khởi động Cloudflare Quick Tunnel
5. Hiển thị URL `https://*.trycloudflare.com`
6. Giám sát hai process; tunnel chỉ tự restart tối đa một lần
7. Dừng tunnel rồi watcher khi nhấn Ctrl+C

Phase 3.1 không tự sửa `PUBLIC_CALLBACK_URL`. Sau khi Quick Tunnel tạo URL mới, vẫn cần cập nhật `.env` và subscribe theo quy trình hiện tại.

Kiểm tra runtime từ terminal khác:

```powershell
.\scripts\status.ps1
```

Dừng đúng process do launcher sở hữu:

```powershell
.\scripts\stop_all.ps1
```

PID, start time và tunnel URL nằm trong `state/runtime.json`. File này không được commit. `stop_all.ps1` xác minh PID, start time và command line; không giết mọi `python.exe` hoặc `cloudflared.exe` trên máy.

## Yêu cầu

- Windows 11
- Python 3.11+
- Telegram bot token và chat ID nhận thông báo
- URL callback HTTPS công khai khi kết nối YouTube WebSub, ví dụ Cloudflare Tunnel
- `yt-dlp` cho polling dự phòng

## Cài đặt

Chạy trong PowerShell tại thư mục dự án:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Mở `.env` và điền:

```env
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
PUBLIC_CALLBACK_URL=https://your-public-tunnel-host.example
WEBHOOK_PATH=/youtube/websub
HOST=127.0.0.1
PORT=8787
YTDLP_PATH=
POLL_INTERVAL_SECONDS=10
POLL_MAX_CONCURRENCY=3
```

Không thêm đường dẫn webhook vào `PUBLIC_CALLBACK_URL`; dịch vụ tự nối `WEBHOOK_PATH`. Git bỏ qua `.env`.

Thêm kênh vào `config/channels.json`:

```json
[
  {
    "channel_id": "UC_x5XG1OV2P6uZZ5FSM9Ttw",
    "name": "Kênh ví dụ",
    "enabled": true
  }
]
```

Chỉ cần channel ID. Không dùng YouTube Data API key.

## Polling yt-dlp dự phòng

Watcher tìm yt-dlp theo thứ tự:

1. `YTDLP_PATH`
2. `tools/yt-dlp.exe` hoặc `tools/yt-dlp`
3. `PATH`

Không tìm thấy yt-dlp: WebSub vẫn chạy; poller báo `MISSING`, không crash loop. Dự án không tự tải binary.

Mỗi probe chỉ đọc ba video công khai mới nhất, không tải video, thumbnail, comment hoặc stream:

```text
yt-dlp --flat-playlist --playlist-end 3 --dump-single-json --no-warnings --skip-download https://www.youtube.com/channel/<CHANNEL_ID>/videos
```

Chạy một vòng chẩn đoán:

```powershell
.\scripts\poll_once.ps1
```

Lần quan sát đầu của mỗi kênh tạo `BASELINE`. Video đang tồn tại được lưu để chống trùng nhưng không gửi Telegram. Chỉ video xuất hiện sau baseline mới được phân loại `NEW`.

Mặc định poll mỗi 10 giây, tối đa ba channel probe đồng thời. Một kênh lỗi không chặn kênh khác. Backoff lỗi: 10, 20, 30, tối đa 60 giây; thành công đặt lại về chu kỳ bình thường.

## Kiểm tra Telegram

```powershell
.\scripts\test_telegram.ps1
```

Chat đã cấu hình phải nhận được `YT_NOTIFI Telegram test OK`.

## Chạy cục bộ

```powershell
.\scripts\run.ps1
```

Kiểm tra dịch vụ tại `http://127.0.0.1:8787/health`.

Mở cửa sổ PowerShell thứ hai để mô phỏng sự kiện YouTube:

```powershell
.\scripts\simulate_event.ps1
.\scripts\simulate_event.ps1
```

Lần đầu ghi log `NEW_VIDEO` và gửi Telegram. Lần hai ghi `DUPLICATE_VIDEO`, không gửi lại. Chỉ xóa `state/yt_notifi.db` khi chủ động muốn xóa toàn bộ lịch sử chống trùng.

## Cloudflare Quick Tunnel miễn phí

Khởi động watcher trước, sau đó mở cửa sổ PowerShell thứ hai:

```powershell
.\scripts\start_tunnel.ps1
```

Script kiểm tra local health và tìm `cloudflared.exe` theo thứ tự: `CLOUDFLARED_PATH`, `tools/cloudflared.exe`, `PATH`. Script không tự tải file nhị phân.

Sao chép URL `https://*.trycloudflare.com` do Cloudflare tạo vào `.env`. Không thêm `/youtube/websub`:

```env
PUBLIC_CALLBACK_URL=https://generated-host.trycloudflare.com
```

Kiểm tra hai đường kết nối, Telegram, subscription và trạng thái:

```powershell
.\scripts\test_public_callback.ps1
.\scripts\test_telegram.ps1
.\scripts\subscribe.ps1
.\scripts\status.ps1
```

Kết quả subscription phân biệt rõ hub chấp nhận request và callback đã xác minh. Trạng thái chỉ thành `ACTIVE` sau khi GET challenge của YouTube tới watcher.

Hostname Quick Tunnel đổi sau khi tunnel khởi động lại. Cập nhật `.env`, khởi động lại watcher, kiểm tra public callback rồi subscribe lại. Watcher phát hiện callback đổi và coi trạng thái cũ là hết hiệu lực.

## Named Tunnel ổn định — tùy chọn

Cloudflare account và domain do Cloudflare quản lý cho phép dùng hostname ổn định, nhưng không bắt buộc. Sau khi cài `cloudflared`:

```powershell
cloudflared tunnel login
cloudflared tunnel create yt-notifi
cloudflared tunnel route dns yt-notifi webhook.example.com
cloudflared tunnel run --url http://127.0.0.1:8787 yt-notifi
```

Đặt `PUBLIC_CALLBACK_URL=https://webhook.example.com`. Làm theo hướng dẫn credentials do Cloudflare tạo. Không commit file credentials.

## Subscribe các kênh đang bật

Đưa cổng 8787 ra HTTPS tunnel, đặt origin công khai vào `PUBLIC_CALLBACK_URL`, rồi chạy:

```powershell
.\scripts\subscribe.ps1
```

Lệnh từ chối callback dùng localhost, HTTP, đường dẫn, URL lỗi hoặc public health không đạt. YouTube xác minh callback qua `GET /youtube/websub`; sau đó SQLite mới ghi `ACTIVE` và tính thời điểm hết hạn theo UTC.

Dịch vụ tự gia hạn khi lease còn 25%, dùng retry backoff có giới hạn và không gia hạn subscription `ACTIVE` còn hiệu lực sau mỗi lần khởi động lại.

## Kiểm thử tự động

Các test không gọi Telegram hoặc YouTube thật:

```powershell
python -m pytest -q
```

Log được ghi ra console và `logs/yt_notifi.log`. Token, chat ID và secret không xuất hiện trong log hoặc status endpoint.

## Hành vi quan trọng

- Atom XML là dữ liệu không tin cậy. DTD/entity, XML lỗi, payload quá lớn và ID sai đều bị từ chối.
- Chỉ topic đã cấu hình và bật mới vượt qua WebSub GET verification.
- Chỉ POST từ kênh đã bật mới vào SQLite và Telegram.
- SQLite quyết định `video_id` mới theo cơ chế atomic; webhook lặp hoặc khởi động lại không gửi lại thông báo.
- WebSub và poll gọi chung `handle_detected_video()`; đường đến trước gửi, đường đến sau thành duplicate.
- Baseline và trạng thái poll từng kênh tồn tại qua restart.
- Lỗi Telegram được ghi với `notification_sent = 0`, không làm sập webhook.
- Lỗi Telegram tạm thời retry tối đa ba lần. Thiếu cấu hình, HTTP 401 và 403 dừng ngay. Video đã gửi thành công không gửi lại.
- Mỗi video lưu thời điểm phát hiện và độ trễ hợp lệ, không âm.
- Tác vụ gia hạn nội bộ dừng sạch khi FastAPI tắt.

## Kiểm tra upload thật có kiểm soát — tùy chọn

Thêm kênh YouTube do bạn quản lý vào `config/channels.json`, khởi động lại watcher, subscribe và xác nhận `ACTIVE` bằng `scripts/status.ps1`.

Tải một video public hoặc unlisted. WebSub POST thật phải tạo một hàng SQLite và một thông báo Telegram. Webhook gửi lại vẫn bị chống trùng. Không coi fixture mô phỏng là kết quả upload thật.
