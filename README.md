# YT_NOTIFI

Dịch vụ Windows theo dõi video YouTube mới bằng `yt-dlp`, chống trùng bằng SQLite và gửi Telegram đúng một lần. Không cần YouTube API key, WebSub, Cloudflare hay VPS.

## Kiến trúc

```text
YouTube channel
    ↓
yt-dlp poll mỗi 10 giây
    ↓
SQLite baseline + dedupe
    ↓
Telegram
```

Lần quan sát đầu tạo baseline, không gửi video cũ. Video mới xuất hiện sau baseline được lưu trước khi gửi Telegram; các lần poll sau nhận diện duplicate và không gửi lại.

## Cài đặt

Yêu cầu: Windows, Python 3.11+ và `yt-dlp`.

```powershell
cd D:\yt_notifi
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Cấu hình `.env`:

```env
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
HOST=127.0.0.1
PORT=8787
YTDLP_PATH=
POLL_INTERVAL_SECONDS=10
POLL_MAX_CONCURRENCY=3
```

Đặt `yt-dlp.exe` trong `tools\`, khai báo `YTDLP_PATH`, hoặc thêm `yt-dlp` vào `PATH`. Thiếu `yt-dlp` là lỗi khởi động.

## Kênh theo dõi

Sửa `config/channels.json`:

```json
[
  {
    "channel_id": "UCxxxxxxxxxxxxxxxxxxxxxx",
    "name": "Tên kênh",
    "enabled": true
  }
]
```

Chỉ kênh `enabled: true` được poll. Channel ID phải bắt đầu bằng `UC` và dài 24 ký tự.

## Chạy

Double-click:

```text
start.bat
```

Launcher tự kiểm tra `.venv`, yêu cầu `yt-dlp`, khởi động watcher, chờ `/health`, giữ một instance và giám sát tiến trình. Không cần thao tác khác.

Kết quả bình thường:

```text
yt-dlp        OK
Watcher       OK

Polling       10 seconds
Status        RUNNING
```

## Trạng thái và dừng

```powershell
.\scripts\status.ps1
.\scripts\stop_all.ps1
```

`stop_all.ps1` xác minh PID, thời điểm bắt đầu và command line trước khi dừng. Script không giết Python hay PowerShell không thuộc YT_NOTIFI.

Runtime state tại `state/runtime.json` chỉ chứa launcher/watcher PID và timestamp. SQLite tại `state/yt_notifi.db` giữ baseline, video, trạng thái Telegram và poll retry qua lần khởi động lại.

## Kiểm tra riêng

```powershell
.\scripts\poll_once.ps1
.\scripts\test_telegram.ps1
.\.venv\Scripts\python.exe -m pytest -q
```

Log mới nằm trong `logs/yt_notifi.log` và tập trung vào `POLL_BASELINE`, `POLL_NEW_VIDEO`, `POLL_FAILED`, `POLL_RECOVERED`, `TELEGRAM_SENT`, `TELEGRAM_FAILED`. `POLL_DUPLICATE` ở mức DEBUG để tránh spam.

## Lưu ý

- Độ trễ tính từ lúc video quan sát được công khai, thường gần chu kỳ poll; YouTube có thể trì hoãn hiển thị sau lúc bấm publish.
- Telegram lỗi tạm thời được retry có giới hạn. SQLite ngăn cùng `video_id` tạo thông báo mới lần hai.
- Bảng WebSub cũ trong database được giữ nguyên để tránh migration phá dữ liệu, nhưng runtime không còn đọc hoặc ghi bảng đó.
