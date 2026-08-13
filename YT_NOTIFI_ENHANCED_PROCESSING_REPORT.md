# BÁO CÁO ENHANCED SILENCE CUTTER CHO YT_NOTIFI

## Thay đổi

Mọi handoff xử lý mới từ YT_NOTIFI sang Silence Cutter luôn gửi:

```json
{
  "handoff_id": "<processing_job_id>",
  "source_file": "<downloaded_file_path>",
  "channel_name": "<channel_name>",
  "output_dir": "<output_dir>",
  "video_id": "<video_id>",
  "video_title": "<video_title>",
  "enhanced_content_selection": true
}
```

Giá trị này là hằng `true` ngay tại một điểm POST chung của `ProcessHandoffWorker`. Không thêm trạng thái joined, chế độ kênh, cấu hình dashboard, cột SQLite hay dữ liệu per-job.

## Retry và restart

- Handoff lỗi trước khi nhận `external_id` vẫn về `PROCESS_PENDING` theo backoff cũ.
- Lần thử lại, kể cả qua một instance worker mới sau restart, gửi lại cùng `handoff_id` và `enhanced_content_selection=true`.
- Handoff đã có `external_id` tiếp tục resume bằng GET trạng thái, không POST trùng. Cơ chế idempotency không đổi.
- Không cần persist cờ enhanced vì mọi job YT_NOTIFI đều dùng cùng giá trị `true`.

## Kết quả đầu ra và cleanup

Không thay đổi hợp đồng `processed_files`. YT_NOTIFI vẫn chấp nhận mọi danh sách không rỗng gồm các file tồn tại, không hardcode số lượng phần; lưu toàn bộ vào `processed_files_json` và đặt `processed_file_path` bằng phần tử đầu tiên. Luồng xác minh output và cleanup workspace giữ nguyên.

## Kiểm thử

- Kiểm thử Process Worker + Cleanup Worker: 22 passed.
- Toàn bộ YT_NOTIFI: 126 passed, 1 cảnh báo deprecation hiện hữu.
- Silence Cutter Content Ops bridge: 9 passed.
- `git diff --check`: đạt.

Không thay đổi polling, Telegram, YTDOWNLOAD, NAS, cleanup, Qwen Worker, Silence Cutter, formatter, schema SQLite hoặc dashboard.
