# Báo cáo Phase 3.3 — Polling-only Runtime

## Kiến trúc cuối

```text
YouTube channel
    ↓
yt-dlp poll mỗi 10 giây
    ↓
SQLite baseline + dedupe
    ↓
Telegram đúng một lần
```

Polling là detector duy nhất. FastAPI chỉ còn `GET /health`; không còn bề mặt HTTP công khai hay endpoint điều khiển nội bộ.

## Thành phần đã xóa

- WebSub GET verification và POST Atom webhook
- Parser Atom, allowlist webhook và background subscription renewal
- Hub request, lease, retry/backoff và trạng thái `REQUESTED`/`ACTIVE`
- Runtime callback resolver, token nội bộ và `/internal/runtime-callback`
- Cloudflare Quick Tunnel cùng launcher supervision/restart
- Callback/tunnel fields trong `state/runtime.json`
- Script tunnel, public callback test, subscribe và simulate webhook
- Cấu hình `PUBLIC_CALLBACK_URL`, `WEBHOOK_PATH`, `CLOUDFLARED_PATH`, `LAUNCHER_RUNTIME_TOKEN`
- Test WebSub/Cloudflare/callback đã lỗi thời

Mã dùng chung cho SQLite dedupe và Telegram được chuyển từ module webhook sang `app/detector.py`; không đổi thuật toán detector polling.

## Thành phần được giữ

- Chu kỳ poll mặc định 10 giây và tối đa concurrency hiện có
- Baseline lần quan sát đầu, không gửi video cũ
- SQLite dedupe theo `video_id`
- Telegram lifecycle, retry có giới hạn và trạng thái gửi bền qua restart
- Backoff từng kênh: 10, 20, 30, tối đa 60 giây; thành công reset lỗi
- `config/channels.json` và lọc `enabled`
- Named mutex chống chạy trùng
- Xác minh PID, start time, command line và shutdown có giới hạn
- Local health và trạng thái polling

`POLL_DUPLICATE` đổi từ INFO sang DEBUG để giảm log lặp. Dedupe không đổi.

## Cấu hình

`.env.example` chỉ còn Telegram, local host/port, yt-dlp và polling. `yt-dlp` được tìm theo thứ tự:

1. `YTDLP_PATH`
2. `tools\yt-dlp.exe`
3. `PATH`

Thiếu `yt-dlp` làm launcher và backend startup fail. Không còn chế độ WebSub-only degraded.

## Launcher và runtime state

`start.bat` chạy một luồng:

```text
kiểm tra .venv
kiểm tra yt-dlp
khởi động watcher
chờ local /health
giám sát watcher
```

Runtime state chỉ chứa:

```json
{
  "launcher_pid": 1234,
  "launcher_started_at": "UTC ISO-8601",
  "watcher_pid": 2345,
  "watcher_started_at": "UTC ISO-8601",
  "started_at": "UTC ISO-8601"
}
```

`stop_all.ps1` chỉ dừng launcher và watcher khớp ownership; không tìm hoặc giết mọi Python/PowerShell.

## Tương thích database

Không reset hoặc xóa database. Bảng `videos` và `channel_poll_state` được giữ nguyên. Bảng subscription cũ, nếu có, nằm nguyên trong file để tránh migration phá dữ liệu nhưng runtime không tạo, đọc hoặc ghi bảng đó. Database Phase 1 cũ vẫn mở được và dữ liệu video/Telegram được nâng cấp an toàn như trước.

## Automated tests

- Lệnh: `python -m pytest -q`
- Kết quả: **41 passed, 1 warning**
- Warning duy nhất: deprecation từ Starlette TestClient/httpx
- Python compile: PASS
- PowerShell syntax: PASS
- Bao phủ baseline, video mới, nhiều video, duplicate, restart, disabled channel, timeout, failure isolation, backoff/reset, shutdown, yt-dlp bắt buộc, mutex, health success/timeout/early exit, runtime state tối giản, process ownership, Telegram retry/state, không route WebSub, không callback config, database có bảng legacy và status không lộ secret

## Manual validation

Đã dừng phiên Phase 3.2 cũ bằng ownership state, gồm watcher và cloudflared cũ. Sau đó chạy launcher Phase 3.3:

```text
yt-dlp        OK
Watcher       OK
Polling       10 seconds
Status        RUNNING
```

Kết quả:

- `GET /health`: PASS
- Poll kênh thật: PASS; latest video được quan sát, failures = 0
- `scripts/status.ps1`: chỉ hiển thị local health, polling, Telegram, kênh, launcher và watcher
- Lần chạy launcher thứ hai: bị chặn, exit code 2
- Không có process cloudflared
- Runtime JSON chỉ có launcher/watcher/timestamps
- Không tunnel URL, callback update, subscribe request hay log WebSub mới
- `stop_all.ps1`: dừng sạch, xóa runtime, giải phóng cổng 8787

## Live regression

Luồng polling và Telegram đã được người dùng kiểm chứng bằng upload public thật trước Phase 3.3. Trong lượt triển khai này không có upload mới sau khi đơn giản hóa, nên chưa tuyên bố live regression end-to-end mới. Manual check xác nhận poller thật vẫn chạy, giữ baseline/dedupe hiện có và không phát sinh duplicate.

## Giới hạn đã biết

- Độ trễ phụ thuộc chu kỳ poll và thời điểm YouTube làm video quan sát được công khai.
- `yt-dlp` là dependency bắt buộc và phải được người dùng cập nhật khi YouTube thay đổi.
- Bảng subscription legacy có thể còn chiếm ít dung lượng nhưng được giữ để tránh migration phá hoại không cần thiết.

PHASE 3.3 IMPLEMENTATION COMPLETE — LIVE VALIDATION REQUIRED
