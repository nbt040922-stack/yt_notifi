# Báo cáo Queued Job → YTDOWNLOAD Handoff

## Kiến trúc

```text
YT_NOTIFI detector
  → SQLite processing_jobs
  → DownloadHandoffWorker nền
  → HTTP 127.0.0.1
  → YTDOWNLOAD ContentOpsBridge
  → DownloadManager.enqueue()
  → yt-dlp/metadata/retry/merge/verify hiện có
```

Hai repo không import source của nhau và không được merge. YT_NOTIFI không gọi yt-dlp để tải video.

## File thay đổi

- `.env.example`: bridge URL và processing work root
- `app/config.py`: nạp cấu hình mới
- `app/download_worker.py`: submit, poll state, backoff và recovery
- `app/state.py`: migration/download state persistence
- `app/main.py`: chạy worker nền tách khỏi polling
- `app/dashboard.html`: thêm cột Download
- `tests/test_download_worker.py`: contract, mapping, retry, restart và regression

`config/channels.json` là thay đổi riêng của người dùng, không thuộc commit.

## Cấu hình

```env
YTDOWNLOAD_BRIDGE_URL=http://127.0.0.1:8790
PROCESSING_WORK_ROOT=D:\ContentOps_Work
```

Bridge URL chỉ được chấp nhận khi dùng HTTP loopback (`127.0.0.1`, `localhost` hoặc `::1`). Work directory mỗi job là `PROCESSING_WORK_ROOT/<processing_job_id>`.

NAS `output_dir` vẫn được gửi dưới tên `final_output_dir` để giữ contract nhưng không dùng làm nơi tải trong phase này.

## Database migration

Bảng `processing_jobs` được mở rộng không phá dữ liệu cũ:

- `download_external_id`
- `download_state`
- `download_progress`
- `downloaded_file_path`
- `download_error`
- `updated_at`
- `download_attempts`
- `next_download_attempt_at`

Rows cũ được giữ nguyên; `updated_at` được backfill từ `created_at`.

## Worker và idempotency

Job `QUEUED` chưa có external ID được POST với `handoff_id = processing_jobs.id`. Sau khi nhận external ID, mọi tick/restart chỉ GET trạng thái đó.

Nếu response POST bị mất, worker gửi lại cùng `handoff_id`; YTDOWNLOAD trả mapping cũ thay vì enqueue lần hai. Worker chạy bằng task nền riêng nên polling/Telegram không bị chặn.

Bridge offline hoặc lỗi 5xx giữ job ở `DOWNLOAD_PENDING` với backoff `5, 10, 20, 30, 60` giây, tối đa 60 giây. Lỗi terminal từ YTDOWNLOAD chuyển `FAILED` và không tạo retry engine mới.

## State mapping

| YTDOWNLOAD | YT_NOTIFI |
|---|---|
| `QUEUED`, `METADATA` | `DOWNLOAD_PENDING` |
| `DOWNLOADING`, `MERGING`, `VERIFYING` | `DOWNLOADING` |
| `DONE` | `DOWNLOADED` |
| `FAILED`, `CANCELLED` | `FAILED` |

`download_state` và `download_error` giữ chi tiết phía downloader. Khi `DONE`, worker lưu nguyên `downloaded_file_path` do DownloadManager trả về; không quét thư mục để đoán filename.

## Restart recovery

- YT_NOTIFI restart: job có `download_external_id` tiếp tục GET; không POST lại.
- Response POST bị mất: retry cùng handoff ID, an toàn nhờ bridge idempotency.
- YTDOWNLOAD hiện dùng queue session-only. Bridge giữ mapping riêng và re-enqueue active Content Ops request qua chính DownloadManager khi YTDOWNLOAD restart; external ID ổn định.
- Không xây state machine tải hoặc resume byte-range thứ hai.

## Tests

- `python -m pytest -q`: **91 passed, 1 warning**
- Python compile: PASS
- `git diff --check`: PASS
- Bao phủ submit một lần, external ID persistence, duplicate tick, bridge offline/recovery, toàn bộ state mapping, exact DONE path, terminal failure, polling/Telegram không bị chặn, restart tracking và migration rows cũ.

## Manual end-to-end

Video công khai: `jNQXAC9IVRw` (Me at the zoo).

- Work root: `D:\ContentOps_Work_Test`
- NAS mapping test: `D:\YT_NOTIFI_NAS_FINAL_TEST\Test Channel`
- YT_NOTIFI: `DOWNLOAD_PENDING → DOWNLOADING → DOWNLOADED`
- YTDOWNLOAD log: `METADATA → DOWNLOADING → VERIFYING → DONE`
- Exact path: `D:\ContentOps_Work_Test\1\Me at the zoo.mp4`
- File tồn tại và được DownloadManager verify
- Chỉ 1 MP4 trong work root
- 0 MP4 trong NAS mapping
- Mở lại StateStore/worker không tạo download thứ hai
- POST lặp cùng handoff ID trả cùng `contentops-1`
- Dashboard hiện `DOWNLOADED`, `DONE 100%`, đúng NAS output; không có console error

Artifact test, DB test và bridge mapping test đã được xóa sau xác minh để lần chạy sau không re-enqueue video thử.

## Phạm vi khóa

Polling, baseline, video dedupe, Telegram exactly-once, Add Channel resolver, hot reload và NAS routing không đổi. Không tích hợp Silence Cutter, FFmpeg processing trong YT_NOTIFI, NAS final writing hoặc xóa source.

Phase kết thúc tại source video `DOWNLOADED`, sẵn sàng cho processor sau.
