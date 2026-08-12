# Báo cáo YT_NOTIFI → Silence Cutter

## Kiến trúc

```text
processing_jobs.DOWNLOADED
  → ProcessHandoffWorker nền
  → HTTP 127.0.0.1:8791
  → Silence Cutter Content Ops bridge
  → production.process_video()
  → file tạm trên output_dir
  → atomic rename thành file NAS cuối
```

Hai repository không import source của nhau. YT_NOTIFI chỉ gọi HTTP và không chứa VAD, cutting, renderer hoặc FFmpeg logic.

## File thay đổi

- `.env.example`, `app/config.py`: thêm `SILENCE_CUTTER_BRIDGE_URL`
- `app/process_worker.py`: submit, poll, state mapping và backoff
- `app/state.py`: migration/persistence processing state
- `app/main.py`: chạy processing worker nền riêng
- `app/dashboard.html`: thêm cột Processing
- `tests/test_process_worker.py`: worker, recovery, migration và regression

`config/channels.json` là thay đổi riêng của người dùng, không thuộc commit.

## State và persistence

| Silence Cutter | YT_NOTIFI |
|---|---|
| `QUEUED` | `PROCESS_PENDING` |
| `PROCESSING`, `FINALIZING` | `PROCESSING` |
| `DONE` | `COMPLETED` |
| `FAILED` | `FAILED` |

Bảng `processing_jobs` được mở rộng không phá dữ liệu cũ với `process_external_id`, `process_state`, `process_progress`, `processed_file_path`, `process_error`, `process_attempts`, `next_process_attempt_at`.

Worker chỉ POST khi chưa có external ID. Sau khi đã nhận ID, mọi tick và restart chỉ GET. Bridge offline giữ `PROCESS_PENDING` với backoff 5, 10, 20, 30, tối đa 60 giây. Lỗi terminal lưu vào `process_error`.

## Exact path và NAS

Request dùng nguyên `downloaded_file_path` và `output_dir` từ SQLite. Worker không quét work directory, không đoán source filename và không tính lại channel folder. Khi bridge trả `DONE`, `processed_file_path` được lưu nguyên giá trị exact final path.

NAS thật chưa được kiểm thử vì `NAS_OUTPUT_ROOT` hiện chưa cấu hình. Không dùng local fallback thay NAS. Manual integration dùng `D:\Silence_Output_Test` theo yêu cầu.

## Tests

- Full YT_NOTIFI regression: **101 passed, 1 warning**
- Test mới: **10 passed**
- Bao phủ submit một lần, external ID persistence, duplicate tick, bridge offline/recovery, state mapping, exact DONE path, terminal failure, restart GET, migration rows cũ và polling/Telegram không bị chặn.

## Manual local validation

Video công khai `jNQXAC9IVRw` được tải thành `D:\ContentOps_Work_Test\9001\source.mp4` rồi chuyển qua HTTP bridge thật.

- YT_NOTIFI: `DOWNLOADED → PROCESS_PENDING → PROCESSING → COMPLETED`
- Silence Cutter: `QUEUED → PROCESSING → FINALIZING → DONE`
- Exact final path: `D:\Silence_Output_Test\Me at the zoo.mp4`
- Input: 19,014 giây; output: 5,947 giây; loại 13,1 giây
- Source vẫn tồn tại
- Một final MP4, không có `.processing.mp4`, không duplicate

Manual CLI `python -m production` ngoài Content Ops cũng tạo output 5,947 giây và loại 13,1 giây, xác nhận flow bình thường không đổi.

## Phạm vi khóa

Polling, baseline, dedupe, Telegram exactly-once, channel management, NAS routing, YTDOWNLOAD handoff và download path persistence không đổi. Không thêm GPT/Qwen, splitting, upload, source cleanup hoặc cleanup NAS.
