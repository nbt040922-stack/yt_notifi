# Báo cáo điều khiển job thủ công

## Phạm vi

- Thêm `POST /api/jobs/{job_id}/cancel` và `POST /api/jobs/{job_id}/retry`.
- Thêm nút **Hủy** và **Thử lại** theo trạng thái trên từng job card.
- Giữ nguyên nút retry NAS hiện có, Telegram, polling, bridge, cleanup và chính sách fallback.

## Ngữ nghĩa hủy

- Job đang chờ hoặc đang chạy được chuyển bền vững sang `CANCELLED`.
- SQLite lưu `cancel_requested`, `cancelled_at` và `cancel_reason=USER_REQUEST`.
- Hủy lần hai là idempotent và trả lại cùng job đã hủy.
- Job `FAILED`, `COMPLETED` hoặc `DONE` không bị hủy nhầm; API trả `JOB_NOT_CANCELLABLE`.
- Hai bridge hiện không có API hủy ContentOps. YT_NOTIFI không giết `yt-dlp`, Python, Qwen hay FFmpeg dùng chung; cờ hủy chặn mọi lần ghi/trạng thái tiếp theo tại ranh giới SQLite.
- Nếu worker trả kết quả sau khi người dùng hủy, kết quả muộn không thể đổi `CANCELLED` thành thành công và file dở dang không được công bố như output hoàn tất.
- Cancellation không tự xóa source/output nên không phá cleanup gate hoặc file một công đoạn đang dùng.

## Ngữ nghĩa thử lại

- Chỉ `FAILED` và `CANCELLED` được thử lại; NAS `FAILED_RETRY`/`CONFLICT` được đưa về `PENDING` mà không render lại.
- Job đang chạy trả `JOB_ALREADY_RUNNING`; job hoàn tất bình thường trả `JOB_NOT_RETRYABLE`.
- Mỗi lần retry tay tăng `manual_retry_count` và ghi `last_manual_retry_at`.
- Các bộ đếm retry tự động cũ được giữ nguyên để không mất lịch sử.
- Mỗi lần retry có handoff xác định và bền vững: lần đầu dùng `{job_id}`, retry thứ N dùng `{job_id}-retry-{N}`. Restart/auto-retry trong cùng lần thử luôn dùng lại đúng handoff này, còn bridge không trả lại bản ghi lỗi của lần trước.

## Tiếp tục theo công đoạn

- Source đã tải phải có `download_state=DONE`, tồn tại và có kích thước lớn hơn 0 mới được tái sử dụng.
- Source hợp lệ: tiếp tục từ `PROCESS_PENDING`, giữ nguyên download mapping và không tải lại.
- Source thiếu/không hợp lệ: quay về `QUEUED`, tạo lại download handoff ở lần retry hiện tại.
- Processing đã xong nhưng NAS lỗi: chỉ xếp lại NAS sync; không gọi Silence Cutter.
- Silence Engine OFF: job ở `PROCESS_PENDING` với `SILENCE_ENGINE_DISABLED`, không chuyển thành `FAILED`; khi engine sẵn sàng worker tiếp tục bình thường.
- `processing_output_dir` và `nas_sync_state` hiện có được giữ khi retry, nên route `F:\ContentOpsFallback` tiếp tục được tái sử dụng.

## Snapshot và exactly-once

Retry cập nhật chính bản ghi job, không tạo job phát hiện mới và không đọc lại cấu hình member/channel. Vì vậy các snapshot sau được giữ nguyên:

- `video_id`, `source_channel_id`, `video_title`, `channel_name`
- `owner_id`, `output_dir`, `intended_output_dir`, `processing_output_dir`
- chính sách enhanced luôn là `true` trong payload Silence Cutter

Luồng retry không gọi detector hoặc Telegram. Bản ghi notification và số lần gửi không thay đổi, giữ đúng semantics thông báo video mới đúng một lần.

## Restart và đua trạng thái

- Cờ hủy và metadata nằm trong SQLite, nên job đã hủy không tự chạy lại sau restart.
- Retry xóa cờ hủy trong cùng transaction và khôi phục đúng công đoạn.
- `BEGIN IMMEDIATE` tuần tự hóa Cancel/Retry; update của worker có guard `cancel_requested=0` và không ghi đè `CANCELLED`.
- Nếu worker hoàn tất trước, Cancel thấy trạng thái terminal và trả conflict. Nếu Cancel thắng, kết quả worker đến muộn bị bỏ qua.
- Retry kép chỉ kích hoạt một lần; lần sau trả `JOB_ALREADY_RUNNING`.

## Kiểm thử

- Bộ điều khiển cô lập: **18 passed**.
- Toàn bộ hồi quy YT_NOTIFI: **189 passed, 1 warning**.
- Warning duy nhất là cảnh báo deprecation sẵn có của Starlette/httpx TestClient.
- Kiểm thử dùng SQLite, bridge và đường dẫn tạm; không restart production, không hủy job thật và không chạm NAS thật.
