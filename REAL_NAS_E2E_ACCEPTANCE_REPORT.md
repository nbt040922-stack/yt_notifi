# Báo cáo nghiệm thu E2E NAS thật

## Tổng quan cấu hình runtime

- Telegram: đã cấu hình; token và chat ID không được ghi vào báo cáo
- Telegram chat: API xác nhận loại `group`
- NAS root: `\\192.168.1.18\Team 1\ContentOps`
- Download bridge: `http://127.0.0.1:8790`
- Work root: `D:\ContentOps_Work`
- Process bridge: `http://127.0.0.1:8791`
- YT_NOTIFI: `http://127.0.0.1:8787`

Cả ba health endpoint đều đạt trước và sau acceptance.

## Kiểm tra NAS

Windows user hiện tại truy cập được share thật `\\192.168.1.18\Team 1`. Thư mục `ContentOps` được tạo theo xác nhận của người dùng.

- Root tồn tại và đọc được
- Tạo thư mục `_contentops_write_test`: đạt
- Tạo và đọc lại file tạm: đạt
- Xóa file và thư mục tạm: đạt
- Không dùng local fallback

Kênh thử đang bật: `TN004UK - Nhật`.

Destination chính xác:

```text
\\192.168.1.18\Team 1\ContentOps\TN004UK - Nhật
```

## Baseline và video acceptance

Video mồi `DX_N3oOLoF8` (`PART 1`) được ghi nhận với `baseline=1`, không gửi Telegram và không tạo job.

Video acceptance:

- Video ID: `6TppNK3Xaag`
- Tiêu đề: `clean master`
- Detection: `2026-08-12T16:11:40.261992+00:00`
- Telegram: thành công trong cùng detector cycle; DB ghi `notification_sent=1`, `notification_attempts=1`, không có delivery error
- Processing job tạo: `2026-08-12T16:11:40.272953+00:00`

Sau các chu kỳ poll lặp và toàn bộ restart, notification attempts vẫn bằng 1. Telegram exactly-once đạt.

## Download

YTDOWNLOAD record:

- External ID: `contentops-1`
- Bắt đầu record: `2026-08-12T16:11:41.465Z`
- Hoàn tất: `2026-08-12T16:12:06.613Z`
- State: `DONE`
- Exact source: `D:\ContentOps_Work\1\clean master.mp4`
- Size: `206.769.467` bytes
- Duration/ffprobe: `851,141` giây

`processing_jobs.downloaded_file_path` khớp tuyệt đối với path bridge. File tồn tại, khác 0 byte và probe được. Work directory chỉ có một MP4; source không bị di chuyển hoặc xóa.

## Silence Cutter và NAS finalization

Silence Cutter record:

- External ID: `contentops-process-1`
- Bắt đầu record: `2026-08-12T16:12:08.033957+00:00`
- Hoàn tất: `2026-08-12T16:14:09.870175+00:00`
- State: `DONE`
- Exact final: `\\192.168.1.18\Team 1\ContentOps\TN004UK - Nhật\clean master.mp4`
- Size: `428.565.831` bytes
- Duration/ffprobe: `831,034` giây

Bridge nhận exact downloaded source path và gọi production core hiện có. Content Boundary Detector, tight2, Silero, SenseVoice, KEEP/CUT, formatter, crop, pitch và render strategy không bị thay đổi.

Kết quả NAS:

- `processed_file_path` trong SQLite khớp exact NAS final path
- Final MP4 tồn tại, khác 0 byte và probe được
- Source và final duration khác phù hợp với processing hiện tại
- Một matching final MP4
- Không có `.processing.mp4`
- Không có duplicate
- Không có local final fallback

## Restart và idempotency

Sau khi `COMPLETED`:

- Restart YT_NOTIFI: job vẫn `COMPLETED`
- Restart YTDOWNLOAD: `contentops-1` vẫn `DONE`
- Restart Silence Cutter bridge: `contentops-process-1` vẫn `DONE`
- YT_NOTIFI job count: 1
- YTDOWNLOAD handoff count cho job: 1
- Silence Cutter handoff count cho job: 1
- Source MP4 count trong work dir: 1
- Matching NAS final count: 1
- Telegram attempts: 1

Không tạo download, render, NAS file hoặc Telegram message thứ hai.

## Regression

- YT_NOTIFI: `101 passed`, 1 deprecation warning từ Starlette/httpx
- YTDOWNLOAD: `72 passed`, 0 failed
- YTDOWNLOAD preflight: yt-dlp, Deno, FFmpeg đạt
- Silence Cutter: `292 passed`, `1 skipped`, `93 subtests passed`

## Cảnh báo

- SQLite hiện không có cột timestamp riêng cho Telegram; thời điểm gửi chỉ xác nhận thuộc detector cycle ngay sau detection. Exactly-once được chứng minh bằng sent flag, attempts và Telegram group API.
- NAS disconnect test không chạy để tránh tác động share thật; automated bridge regression đã bao phủ `NAS_UNAVAILABLE` và không fallback local.
- Không có code fix hoặc tính năng mới trong acceptance này.

REAL NAS E2E — PASS
