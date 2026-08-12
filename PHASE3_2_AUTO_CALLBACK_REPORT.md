# Báo cáo Auto Callback Phase 3.2

## Kiến trúc

Launcher tạo token ngẫu nhiên trong memory trước khi khởi động watcher. Watcher kế thừa token qua process environment. Sau khi cloudflared cung cấp Quick Tunnel URL, launcher gọi endpoint nội bộ:

```text
POST http://127.0.0.1:8787/internal/runtime-callback
X-YT-Notifi-Runtime-Token: <runtime token>
{"public_origin":"https://example.trycloudflare.com"}
```

Backend cập nhật `ActiveCallback`, sau đó gọi subscription manager Phase 2 hiện có. Không restart uvicorn, không sửa `.env`, không tạo subscription manager thứ hai.

## Callback precedence

`ActiveCallback` là nguồn duy nhất cho callback đang hoạt động:

1. Runtime Quick Tunnel origin do launcher xác thực
2. Static `PUBLIC_CALLBACK_URL` trong `.env`

`WEBHOOK_PATH` chỉ nối một lần tại resolver. Named Tunnel, reverse proxy và static domain cũ vẫn hoạt động nếu không có runtime callback.

## Security model

- Chỉ endpoint localhost được dùng bởi launcher
- Kiểm tra caller loopback
- Token 256-bit ngẫu nhiên, so sánh constant-time bằng `hmac.compare_digest`
- Token chỉ nằm trong process environment; không ghi `runtime.json`, log hoặc status
- Origin bắt buộc HTTPS, không path/query/fragment/credentials/localhost
- URL hoặc payload webhook không thể điều khiển callback
- Request công khai đi xuyên cloudflared có thể tới app từ loopback, nên token là lớp xác thực bắt buộc; không dựa riêng vào source IP

## Callback change và subscription refresh

Origin mới được áp dụng ngay, rồi `ensure_subscriptions()` nhận callback mới. State machine cũ nhận ra `callback_changed`, đặt từng kênh đang bật thành `REQUESTED`, gửi hub request và chờ GET challenge chuyển `ACTIVE`.

Origin giống runtime hiện tại trả `unchanged`; không gửi lại subscription request. Kênh disabled không xuất hiện trong danh sách refresh. Một kênh lỗi được state machine ghi failure/backoff; kênh khác vẫn tiếp tục.

Polling không phụ thuộc refresh WebSub. Hub lỗi không restart watcher/tunnel và không dừng yt-dlp fallback.

## Quick Tunnel restart

Cloudflared restart tối đa một lần theo Phase 3.1. URL mới chạy lại cùng `Apply-RuntimeCallback()`, tăng generation, cập nhật state rồi refresh WebSub. URL giống giữ idempotent và không resubscribe.

## Runtime state

`state/runtime.json` bổ sung:

```json
{
  "tunnel_url": "https://example.trycloudflare.com",
  "callback_origin": "https://example.trycloudflare.com",
  "callback_url": "https://example.trycloudflare.com/youtube/websub",
  "callback_updated_at": "UTC ISO-8601",
  "callback_generation": 1
}
```

File ghi atomic bằng temporary file. Backend không tự nạp runtime file cũ; chỉ endpoint có token của launcher đang chạy mới đặt runtime callback. Runtime stale vì vậy không được tin cậy. Shutdown xóa runtime state, không unsubscribe và không sửa `.env`.

## Status

`scripts/status.ps1` chỉ dùng runtime callback khi cả launcher ownership và cloudflared ownership còn hợp lệ. Output thêm Cloudflare online/offline, runtime callback active/stale, WebSub callback và `CALLBACK CURRENT/STALE` cho từng subscription đang bật. Subscription cũ của kênh đã tắt không làm sai tổng trạng thái. Nếu WebSub chưa active nhưng yt-dlp còn chạy, status báo `DEGRADED — polling fallback active`.

## Automated tests

- Lệnh: `python -m pytest -q`
- Kết quả: **94 passed, 1 warning**
- Test Phase 3.2 + launcher callback: **40 passed, 1 warning**
- Không gọi YouTube hoặc Cloudflare thật
- Bao phủ: runtime precedence, static fallback, malformed origin, path/query/credentials, token/IP rejection, callback idempotency, changed URL refresh, nhiều kênh, disabled channel, failure isolation, callback mismatch, stale runtime rejection, path nối một lần, state không chứa secret, tunnel restart và launcher request token
- Toàn bộ test Phase 1/2.1/3.1 vẫn đạt

## Manual validation

Đã kiểm chứng trên Windows bằng đúng launcher một lệnh:

- Watcher, polling và Cloudflare Quick Tunnel khởi động thành công
- URL tunnel đầu tiên được phát hiện tự động; callback được ghi vào runtime với `callback_generation = 1`
- Endpoint nội bộ cập nhật callback và YouTube hub nhận yêu cầu đăng ký (`SUBSCRIBE_HUB_ACCEPTED`)
- Chạy launcher lần hai bị chặn với exit code `2`; không tạo bộ tiến trình trùng
- Chủ động dừng cloudflared để thử recovery: launcher restart đúng một lần, nhận hostname khác, tăng `callback_generation = 2` và hub nhận yêu cầu mới
- Local health và public health đều `PASS` sau khi hostname mới truyền DNS
- Polling tiếp tục chạy và nhận diện video cũ là duplicate trong lúc WebSub chờ xác minh
- `stop_all.ps1` dừng đúng launcher/watcher/cloudflared, xóa runtime state và giải phóng cổng 8787
- `.env` không bị sửa; không chạy `subscribe.ps1`; runtime state không chứa token

Trong quá trình live test đã phát hiện `app.status` còn kiểm tra public health bằng callback tĩnh. Đã sửa để ưu tiên `YT_NOTIFI_RUNTIME_CALLBACK`, đồng nhất với callback runtime đang hoạt động, và thêm regression test.

## Live WebSub verification

Luồng tự động đã đi tới hub thật và hub trả `202 Accepted` cho cả URL ban đầu lẫn URL sau restart. Tuy nhiên GET challenge từ YouTube chưa tới trong cửa sổ kiểm thử; subscription vẫn ở `REQUESTED` với `CALLBACK CURRENT`. Vì vậy chưa tuyên bố live verification end-to-end thành công. Polling fallback vẫn `RUNNING` và status báo degraded đúng thiết kế.

## Known limitations

- Quick Tunnel vẫn là hostname tạm thời; launcher tự xử lý thay đổi trong phiên và một lần restart cloudflared.
- Endpoint refresh chờ hub request có timeout; polling vẫn hoạt động nếu hub chậm/lỗi.
- Automatic callback chỉ có khi dùng one-click launcher. Static/manual recovery vẫn dùng `.env` và `subscribe.ps1`.

## Lệnh manual validation

```powershell
cd D:\yt_notifi
.\start.bat
.\scripts\status.ps1
```

Kỳ vọng: callback trong runtime khớp subscription callback; không sửa `.env`, không chạy `subscribe.ps1`; GET challenge chuyển subscription sang `ACTIVE`.

PHASE 3.2 IMPLEMENTATION COMPLETE — LIVE VALIDATION REQUIRED
