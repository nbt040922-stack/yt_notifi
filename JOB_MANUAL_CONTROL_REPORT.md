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

## Clear Completed

- Dashboard có một thao tác bulk **Xóa job hoàn thành**, gọi duy nhất `POST /api/jobs/clear-completed` và làm mới danh sách ngay sau khi xóa.
- Backend chọn và xóa trong một transaction SQLite; nếu một lần xóa lỗi, toàn bộ transaction được rollback.
- Chỉ `DONE`/`COMPLETED` có `process_state=DONE`, `nas_sync_state=DONE|NOT_REQUIRED`, `cleanup_state=CLEANED`, source đã cleanup và không còn lịch retry mới đủ điều kiện.
- Với local fallback, bắt buộc NAS đã giao xong, `fallback_cleanup_at` đã ghi và thư mục fallback không còn tồn tại.
- Mọi đường dẫn trong `processed_files_json` được kiểm tra còn là file không rỗng trước khi xóa lịch sử.
- Clear chỉ xóa hàng trong `processing_jobs`; không gọi cleanup và không xóa output NAS/PART, source khác hay file của member/channel khác.
- `FAILED`, `CANCELLED`, job đang hoạt động, NAS pending/syncing/retry/conflict và job còn cleanup dependency đều được giữ để tiếp tục xử lý hoặc retry.
- `FAILED` và `CANCELLED` có nút **Xóa** riêng. `DELETE /api/jobs/{job_id}` chỉ xóa metadata của đúng job sau xác nhận; file local/NAS và ledger video vẫn được giữ nguyên.
- Job đang hoạt động hoặc `DONE`/`COMPLETED` không thể đi qua API xóa lỗi/hủy và nhận `JOB_NOT_CLEARABLE`; job hoàn thành tiếp tục dùng bulk Clear Completed với các guard giao hàng/cleanup đầy đủ.
- Bảng `videos`, lịch sử Telegram exactly-once và `channel_poll_state` không bị đụng tới, nên video cũ không được phát hiện/thông báo lại sau Clear hoặc restart.
- `GET /api/jobs` mặc định trả 200 job mới nhất; tham số `limit` cho phép giảm số lượng và được chặn tối đa 500.
- Không có auto-retention, Clear Selected hoặc xóa riêng FAILED/CANCELLED trong giai đoạn này.

## Kiểm thử

- Bộ Clear/Remove History cô lập: **29 passed**.
- Toàn bộ hồi quy YT_NOTIFI: **218 passed, 1 warning**.
- Warning duy nhất là cảnh báo deprecation sẵn có của Starlette/httpx TestClient.
- Kiểm thử dùng SQLite, bridge và đường dẫn tạm; không restart production, không hủy job thật và không chạm NAS thật.
