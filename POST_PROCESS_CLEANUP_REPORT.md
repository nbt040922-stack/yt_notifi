# Báo cáo POST-PROCESS CLEANUP

Ngày acceptance: 2026-08-12

## Phạm vi triển khai

YT_NOTIFI có thêm một cleanup worker tuần tự, xử lý tối đa một job mỗi lượt. Worker chỉ truy vấn job `COMPLETED` chưa `CLEANED`; không nằm trong detector, poller, downloader hay Silence Cutter.

Các cột SQLite được bổ sung không phá dữ liệu cũ:

- `cleanup_state`
- `cleanup_error`
- `cleanup_at`
- `source_deleted`
- `cleanup_bytes_freed`
- `cleanup_attempts`
- `next_cleanup_attempt_at`

Retry dùng backoff `30, 60, 120, 300` giây. `CONTENTOPS_CLEANUP_DRY_RUN` mặc định `true`; sau acceptance thật, `.env` vận hành đã được đặt thành `false`.

## Điều kiện và an toàn đường dẫn

Cleanup chỉ chạy khi:

- `status=COMPLETED` và `process_state=DONE`;
- `processed_files_json` là danh sách không rỗng;
- mọi output tồn tại, là file thường, khác rỗng và nằm dưới chính `job.output_dir`;
- `ffprobe` đọc được container, tìm thấy video stream và duration dương.

Workspace được dựng duy nhất từ `PROCESSING_WORK_ROOT/<job-id>` rồi resolve. Worker chặn xóa khi root thiếu/không tuyệt đối, root hoặc workspace là drive root, workspace bằng root, parent/tên component không khớp job ID, hoặc workspace giao với `NAS_OUTPUT_ROOT`/`job.output_dir`. Worker chỉ gọi xóa đệ quy sau toàn bộ bước kiểm tra. Job lỗi/không hoàn tất không được chọn.

## Silence Cutter và YTDOWNLOAD

- Giữ nguyên `workspace/contentops-process-jobs.json` của Silence Cutter để restart vẫn nhớ handoff DONE. Thư mục report Content Ops hiện chỉ có 3.922 byte; không có media nặng cần xóa.
- Giữ nguyên `contentops-handoffs.json` của YTDOWNLOAD. Media nguồn nằm trong workspace do YT_NOTIFI sở hữu; runtime/binary và state nhẹ của YTDOWNLOAD không bị xóa.
- Không sửa mã nguồn của YTDOWNLOAD hoặc Silence Cutter.

## Dry-run thật

- Job: `1`
- Workspace ứng viên: `D:\ContentOps_Work\1`
- Dung lượng đo đệ quy: `206.769.467` byte
- Ba part NAS đều tồn tại, khác rỗng và qua `ffprobe`.
- Kết quả: `cleanup_state=PENDING`, `source_deleted=0`; workspace vẫn tồn tại.

## Cleanup thật

- Đã xóa đúng `D:\ContentOps_Work\1`.
- Dung lượng trước: `206.769.467` byte.
- Dung lượng sau: `0` byte (workspace không còn).
- `cleanup_bytes_freed=206769467`.
- DB: `status=COMPLETED`, `process_state=DONE`, `cleanup_state=CLEANED`, `source_deleted=1`, `cleanup_error=NULL`.

NAS giữ nguyên và được probe lại sau cleanup:

- `\\192.168.1.18\Team 1\ContentOps\TN004UK - Nhật\PART_1.mp4` — `97.615.616` byte
- `\\192.168.1.18\Team 1\ContentOps\TN004UK - Nhật\PART_2.mp4` — `139.261.494` byte
- `\\192.168.1.18\Team 1\ContentOps\TN004UK - Nhật\PART_3.mp4` — `113.693.200` byte

Không file NAS nào bị xóa hoặc đổi kích thước.

## Restart và idempotency

Đã restart YT_NOTIFI, YTDOWNLOAD và Silence Cutter bridge. Cả ba health endpoint đều đạt. Sau restart:

- job vẫn `COMPLETED/CLEANED`, không có cleanup error;
- workspace vẫn không tồn tại;
- ba part NAS vẫn tồn tại và qua `ffprobe`;
- hàng đợi download/process đều có `0` job đến hạn;
- YTDOWNLOAD vẫn trả handoff `contentops-1` ở trạng thái `DONE`;
- Silence Cutter vẫn trả handoff `contentops-process-1` ở trạng thái `DONE`;
- không tải lại, không render lại, không tạo part trùng.

`downloaded_file_path` của job CLEANED được phép trỏ tới file đã xóa vì download/process worker chỉ chọn trạng thái chưa hoàn tất.

## Kiểm thử

- YT_NOTIFI toàn bộ: `113 passed, 1 warning`.
- Cleanup + download/process worker mục tiêu: `35 passed`.
- YTDOWNLOAD: `72 passed`; preflight đạt; `node --check` đạt.
- Silence Cutter Content Ops bridge: `6 passed`.
- `git diff --check`: đạt ở cả ba dự án.
