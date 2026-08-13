# BÁO CÁO BULK NOTIFY-ONLY CHANNELS

## Kiến trúc và dữ liệu

Dashboard có hai tab độc lập:

- **Silence Channels** giữ nguyên luồng Telegram → YTDOWNLOAD → Silence Cutter enhanced → NAS → cleanup.
- **Notify Channels** chỉ poll → dedupe → Telegram; không tạo `processing_jobs`, vì vậy không phát sinh download, Silence Cutter, NAS hay cleanup.

SQLite được migration không phá hủy bằng bảng mới:

```sql
CREATE TABLE notify_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    created_at TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);
```

`channels.json` và mô hình Silence channel không thay đổi.

## Resolver và bulk API

`POST /api/notify-channels/bulk` nhận tối đa 500 dòng. Resolver yt-dlp hiện hữu được dùng chung; chế độ bulk yêu cầu thêm title chính thức kể cả với URL `/channel/UC...`.

- Resolve đồng thời có giới hạn, mặc định 6 và cấu hình bằng `NOTIFY_RESOLVE_CONCURRENCY` (1–12).
- Dedupe trước resolve theo dòng đầu vào và sau resolve theo canonical `channel_id`.
- UNIQUE ở SQLite bảo vệ thêm lần cuối.
- Một dòng lỗi không hủy lô; phản hồi có tổng `added`, `existing`, `failed` và kết quả từng dòng.
- UI chia danh sách thành lô 20 URL, hiển thị tiến độ `X / tổng`, rồi tổng hợp kết quả đầy đủ.
- API PATCH/DELETE bật, tắt và xóa riêng Notify channel; không tác động Silence channel trùng ID.

## Polling, baseline và dedupe Telegram

Một `ChannelPoller` tải cả hai collection và dùng chung yt-dlp, semaphore, chu kỳ 10 giây cùng backoff hiện hữu. Các ID Silence được giữ trong tập route xử lý:

- ID thuộc Silence: Telegram và tạo processing job như cũ.
- ID chỉ thuộc Notify: Telegram, không tạo processing job.
- ID thuộc cả hai: được merge thành một lần poll và route theo Silence, nên chỉ một Telegram và một processing job.

Bảng `videos` hiện hữu là ledger video/notification dùng chung, với `video_id` là khóa duy nhất toàn cục. Đây là dedupe an toàn giữa hai collection và giữ Telegram exactly-once. Notify channel mới hoặc được bật lại sẽ reset poll baseline, trừ khi cùng ID đang được Silence theo dõi. Lần poll đầu ghi tối đa ba video hiện tại với `baseline=1`, `notification_attempts=0`; chỉ video mới về sau mới gửi Telegram.

Retry Telegram dùng nguyên ledger và cơ chế cũ. Xóa Notify channel không xóa lịch sử video.

## Nghiệm thu thật

Dashboard production được khởi động lại bằng Task Scheduler và kiểm tra qua trình duyệt:

- Hai tab hiển thị đúng, bố cục Notify dùng được trên giao diện hiện tại.
- Gửi 5 dòng: OpenAI, YouTube, OpenAI trùng, NASA và một URL video sai.
- Kết quả: 3 ADDED, 1 ALREADY_EXISTS, 1 FAILED.
- Tên chính thức và canonical ID được tự nhận đúng; tải lại trang vẫn giữ đủ ba bản ghi.
- Bật/tắt hoạt động.
- Poll baseline thật hoàn tất cho cả ba: `baseline=1`, `notification_attempts=0`, `notification_sent=0`.
- Số processing job của các Notify channel: 0.
- Ba kênh nghiệm thu đã được xóa sau kiểm tra để không tiếp tục theo dõi ngoài ý muốn. Silence channel production không bị thay đổi.

## Hiệu năng

Benchmark resolver thật với 10 kênh công khai, concurrency 6:

- Thành công: 10; thất bại: 0.
- Tổng thời gian: 11,85 giây.
- Thời gian resolve trung bình: 5,96 giây/kênh.
- Test 50 URL mô phỏng xác nhận concurrency thực tế không vượt giới hạn cấu hình.

## Kiểm thử

- Toàn bộ YT_NOTIFI: 136 passed, 1 cảnh báo deprecation hiện hữu.
- API: valid/mixed/invalid, duplicate input, hai alias cùng canonical ID, đã tồn tại, official title, canonical ID, enable/disable/delete, empty/large batch và bounded concurrency.
- Poller: baseline, không spam lịch sử, video mới exactly-once, restart ledger, disabled không poll, không processing/download, và cùng kênh ở hai collection không double-notify.
- Giao diện: hai tab, textarea bulk, progress, summary và per-entry result.
- `git diff --check`: đạt.
