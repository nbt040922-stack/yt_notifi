# Báo cáo bộ phát hiện hybrid Phase 2.1

## Kiến trúc

WebSub vẫn là đường push chính. Poller yt-dlp là fallback. Hai nguồn tạo cùng `VideoEvent` và gọi chung `handle_detected_video()`. Hàm chung sở hữu insert/dedupe, quyết định `NEW_VIDEO` và Telegram lifecycle. Không có hệ thống thông báo thứ hai.

## Lệnh yt-dlp

```text
yt-dlp --flat-playlist --playlist-end 3 --dump-single-json --no-warnings --skip-download https://www.youtube.com/channel/<CHANNEL_ID>/videos
```

Lệnh chỉ lấy tối đa ba entry công khai mới nhất. Không tải media, thumbnail, comment hoặc stream. Binary được tìm qua `YTDLP_PATH`, `tools/yt-dlp.exe`, `tools/yt-dlp`, rồi `PATH`. Không tự tải binary.

## Polling và baseline

- Interval mặc định: 10 giây
- Concurrency mặc định: 3 channel probe
- Không chồng poll cycle cùng kênh
- Shutdown bằng FastAPI lifespan cancellation
- Poll đầu của từng kênh lưu mọi entry hiện có dưới dạng baseline, không gửi Telegram
- Poll sau xử lý nhiều entry theo thứ tự cũ đến mới
- Baseline tồn tại qua restart

## Dedupe và migration DB

Bảng `videos` được thêm `detection_source` và `baseline`; dữ liệu Phase 1/2 giữ nguyên. Bảng mới `channel_poll_state` lưu trạng thái khởi tạo, lần poll gần nhất, lần thành công gần nhất, lỗi gần nhất, số lỗi liên tiếp, video mới nhất và thời điểm retry.

WebSub đến trước thì poll thấy duplicate. Poll đến trước thì WebSub thấy duplicate. Một `video_id` chỉ có một Telegram lifecycle.

## Lỗi và backoff

Một probe timeout hoặc lỗi chỉ cập nhật trạng thái kênh đó. Kênh khác vẫn chạy. Backoff: 10, 20, 30, tối đa 60 giây; lần thành công kế tiếp reset số lỗi. Thiếu yt-dlp chỉ vô hiệu polling, không ảnh hưởng WebSub.

Log trạng thái gồm `POLL_BASELINE`, `POLL_NEW_VIDEO`, `POLL_DUPLICATE`, `POLL_FAILED`, `POLL_RECOVERED`; poll không đổi chỉ dùng DEBUG để tránh spam.

## Telegram và latency

Thông báo giữ định dạng ngắn, thêm `Detected via: POLL` hoặc `Detected via: WEBSUB`. Timestamp yt-dlp chỉ dùng khi có `timestamp`/`release_timestamp` đáng tin cậy. Không có timestamp thì bỏ Published và Latency; không bịa dữ liệu.

## Kiểm thử tự động

- Lệnh: `python -m pytest -q`
- Kết quả: **54 passed**
- Mạng: không dùng; subprocess yt-dlp được mock
- Bao phủ: toàn bộ Phase 1/2, URL channel, command yt-dlp, JSON parsing, baseline, không notify baseline, nhiều video mới, hai thứ tự WebSub/poll, restart, thiếu binary, timeout, cô lập kênh lỗi, backoff/reset, shutdown, disabled channel, status không lộ secret, nguồn phát hiện và Telegram dedupe.

## Live test thực tế

- Local `/health` trên watcher đang chạy: **PASS** (`status=ok`, `service=YT_NOTIFI`). Tiến trình hiện có không bị dừng hoặc khởi động lại.
- Không quan sát upload public mới end-to-end trong phiên triển khai này.
- Không ghi nhận nguồn phát hiện thật hoặc latency thật.
- `tools/yt-dlp.exe`: chưa có tại thời điểm kiểm tra.
- Trạng thái: **LIVE VALIDATION REQUIRED**.

## Giới hạn

- Mục tiêu khoảng 15 giây tính từ lúc video xuất hiện công khai; không tính từ nút Publish nếu YouTube chưa công khai video.
- Poller chỉ thấy nội dung xuất hiện trên trang `/channel/<ID>/videos` mà yt-dlp đọc được.
- Retry Telegram có thể kéo dài một probe đang xử lý, nhưng concurrency giới hạn giữ kênh khác hoạt động.

## Lệnh live validation

Đặt yt-dlp tại `tools/yt-dlp.exe` hoặc cấu hình `YTDLP_PATH`. Sau đó:

```powershell
.\scripts\run.ps1
.\scripts\status.ps1
.\scripts\poll_once.ps1
```

Xác nhận poll đầu hiển thị `BASELINE`. Sau đó upload một video public mới và theo dõi:

```powershell
Get-Content .\logs\yt_notifi.log -Wait
.\scripts\status.ps1
```

Yêu cầu đạt: log có `POLL_NEW_VIDEO` hoặc WebSub `NEW_VIDEO`, Telegram nhận đúng một thông báo, poll lặp không gửi lại.

PHASE 2.1 IMPLEMENTATION COMPLETE — LIVE VALIDATION REQUIRED
