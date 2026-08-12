# Báo cáo One-click Launcher Phase 3.1

## Kiến trúc

`start.bat` là entrypoint double-click tối thiểu, gọi `scripts/start_all.ps1`. Launcher bọc hệ thống Phase 1/2.1 hiện có; không sửa WebSub, polling, baseline, SQLite dedupe hoặc Telegram lifecycle.

## Startup flow

1. Resolve project root từ vị trí script, không phụ thuộc current directory
2. Load `.env` vào process launcher, không in giá trị
3. Bắt buộc `.venv\Scripts\python.exe`; thiếu thì dừng với hướng dẫn setup
4. Tìm yt-dlp qua `YTDLP_PATH`, `tools\yt-dlp.exe`, `PATH`; thiếu chỉ chạy degraded WebSub mode
5. Tìm cloudflared qua `CLOUDFLARED_PATH`, `tools\cloudflared.exe`, `PATH`; thiếu là fatal
6. Giữ Windows named mutex
7. Khởi động uvicorn child, redirect stdout/stderr, lưu PID
8. Chờ local health tối đa 30 giây và xác minh child chưa thoát
9. Khởi động Quick Tunnel, capture log, trích URL `trycloudflare.com`
10. Ghi runtime state và giữ launcher sống để giám sát

Cloudflared không bao giờ khởi động trước khi watcher health đạt.

## Process ownership và chống duplicate

Named mutex `Local\YT_NOTIFI_LAUNCHER` đảm bảo chỉ một launcher trong Windows session. Launcher thứ hai in `YT_NOTIFI is already running`, không dừng instance khỏe.

`state/runtime.json` không tự được tin cậy. Trước khi báo RUNNING hoặc stop PID, script so sánh:

- PID
- process start time
- command line marker riêng cho launcher, watcher hoặc cloudflared

PID stale/reused không bị dừng.

## Runtime schema

```json
{
  "launcher_pid": 1234,
  "launcher_started_at": "UTC ISO-8601",
  "watcher_pid": 2345,
  "watcher_started_at": "UTC ISO-8601",
  "cloudflared_pid": 3456,
  "cloudflared_started_at": "UTC ISO-8601",
  "started_at": "UTC ISO-8601",
  "tunnel_url": "https://example.trycloudflare.com"
}
```

File được ghi qua temporary file rồi thay thế, bị `.gitignore` loại bỏ.

## Supervision và shutdown

- Watcher thoát bất ngờ: launcher báo lỗi, dừng tunnel, exit non-zero
- Cloudflared thoát: restart tối đa một lần; lần hai là fatal
- Ctrl+C/launcher shutdown: dừng cloudflared trước, watcher sau
- Termination có timeout 5 giây trước force-kill
- `scripts/stop_all.ps1` chỉ nhắm process khớp ownership metadata
- Shutdown xóa `runtime.json`; log lifecycle nằm trong `logs/launcher.log`
- Detector stdout/stderr và cloudflared output ghi file riêng, không spam terminal launcher
- Telegram token/chat ID được redact nếu vô tình xuất hiện trong launcher log

## Status

`scripts/status.ps1` giữ toàn bộ status detector cũ và thêm:

- Launcher `RUNNING`, `STOPPED` hoặc `STALE`
- Watcher PID và trạng thái
- Tunnel PID và trạng thái
- Tunnel URL

## Kiểm thử tự động

- Lệnh: `python -m pytest -q`
- Kết quả: **68 passed**
- Launcher tests: **14 passed**
- Không dùng mạng
- Bao phủ: root resolution, venv missing, yt-dlp degraded, cloudflared fatal, mutex duplicate, stale PID, health success/timeout/early exit, tunnel URL, runtime write/remove, owned PID validation và log redaction
- Toàn bộ test Phase 1/2.1 vẫn đạt

## Manual validation

Chưa chạy launcher end-to-end vì cổng `127.0.0.1:8787` đang được watcher người dùng sử dụng và tunnel live đang hoạt động. Không dừng service đang chạy ngoài launcher.

Trạng thái: **MANUAL VALIDATION REQUIRED**.

Sau khi đóng các terminal watcher/cloudflared cũ:

```powershell
cd D:\yt_notifi
.\start.bat
```

Từ terminal thứ hai:

```powershell
.\scripts\status.ps1
.\start.bat
.\scripts\stop_all.ps1
```

Kỳ vọng: status báo đúng PID/URL; lần start thứ hai báo đang chạy; stop chỉ dừng process thuộc launcher và xóa runtime.

## Giới hạn

- Quick Tunnel URL chỉ được hiển thị, chưa tự ghi `.env`; đây là phạm vi Phase 3.2.
- Đóng cưỡng bức toàn bộ Windows session có thể bỏ qua log shutdown cuối, nhưng child dùng cùng console và `stop_all.ps1` vẫn có ownership metadata khi runtime còn tồn tại.
- Không có dashboard hoặc system tray.

PHASE 3.1 IMPLEMENTATION COMPLETE — MANUAL VALIDATION REQUIRED
