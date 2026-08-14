# Báo cáo fallback an toàn sang ổ F

## Cấu hình

- Fallback mặc định: `F:\ContentOpsFallback`.
- Có thể đổi bằng `LOCAL_OUTPUT_FALLBACK_ROOT`; hệ thống không tự chọn ổ C hoặc D.
- `LOCAL_FALLBACK_MIN_FREE_GB` mặc định `20`. Dưới ngưỡng, job chờ với lỗi `LOCAL_FALLBACK_DISK_LOW`.
- Không tạo/ghi được fallback thì job chờ với lỗi `LOCAL_FALLBACK_UNAVAILABLE`; không chuyển sang ổ khác.

## Quyết định NAS hay fallback

Ngay trước lần POST đầu tiên sang Silence Cutter, YT_NOTIFI thử tạo/mở thư mục NAS đích và thực hiện write probe nhỏ:

- NAS ghi được: render trực tiếp vào `output_dir`; `nas_sync_state=NOT_REQUIRED`.
- NAS không ghi được: render vào `<fallback_root>/<owner_id>/<channel đã sanitize>/<job_id>`; `nas_sync_state=PENDING`.

Đường render được lưu một lần và tái sử dụng cho mọi retry/restart. Payload vẫn gửi `enhanced_content_selection=true`.

## Đường dẫn được lưu

- `output_dir` và `intended_output_dir`: NAS đích cuối cùng, bất biến theo snapshot job.
- `processing_output_dir`: nơi Silence Cutter thực sự render.
- Đổi owner hoặc `nas_folder` sau này không đổi đích của job cũ.
- Khi còn chờ sync, `processed_files_json` giữ đường dẫn local. Sau sync thành công, trường này và `processed_file_path` chuyển sang đường NAS chính thức, giữ nguyên filename từ Silence Cutter.

## Trạng thái đồng bộ

- `NOT_REQUIRED`: xử lý trực tiếp trên NAS.
- `PENDING`: xử lý local, chờ NAS.
- `SYNCING`: đang sao chép.
- `FAILED_RETRY`: lỗi tạm thời, sẽ thử lại.
- `DONE`: đã xác minh đầy đủ trên NAS.
- `CONFLICT`: file đích tồn tại nhưng khác kích thước; không ghi đè và không xóa local.

Worker chạy tuần tự, mỗi lần chỉ xử lý một job. Lịch retry: 30 giây, 1 phút, 2 phút, 5 phút, 10 phút, sau đó tiếp tục mỗi 10 phút. Trạng thái `PENDING`, `FAILED_RETRY` và `SYNCING` đều được phục hồi sau restart.

## Sao chép và xác minh

Mỗi file được sao chép vào `<filename>.syncing`, đóng và flush xuống đĩa, kiểm tra kích thước, rồi mới đổi tên thành filename cuối. File cuối chỉ được công nhận khi tồn tại, source lớn hơn 0 và kích thước hai bên bằng nhau. File đích cùng kích thước được coi là đã sync để retry idempotent. File khác kích thước chuyển `CONFLICT`, giữ nguyên fallback.

Sau khi toàn bộ file đạt, DB được cập nhật `DONE` và đường NAS trước khi xóa thư mục fallback. `fallback_cleanup_at` giúp restart tiếp tục dọn local nếu tiến trình dừng giữa hai bước.

## Cleanup

Cleanup download/processing workspace chỉ được chọn khi xử lý `COMPLETED` và NAS ở `DONE` hoặc `NOT_REQUIRED`. Job `PENDING`, `SYNCING`, `FAILED_RETRY`, `CONFLICT` giữ nguyên fallback, download workspace và processing workspace. Fallback root cũng được đưa vào danh sách đường dẫn được bảo vệ khỏi cleanup nhầm.

## Dashboard và retry thủ công

Dashboard hiển thị `NAS: DIRECT`, `LOCAL FALLBACK`, `PENDING SYNC`, `SYNCING`, `RETRYING`, `SYNCED` hoặc `CONFLICT`, cùng đường render thực tế. Job `PENDING`/`FAILED_RETRY` đã xử lý xong có nút `Retry NAS Sync`; worker tự động vẫn là cơ chế chính.

## Job lịch sử

SQLite sản xuất hiện có 1 job lịch sử `FAILED / NAS_UNAVAILABLE` (job `2`, video `6LGjWSNUzac`). Không tự sửa hoặc chạy lại job này để tránh migration phá hủy. Job mới dùng fallback an toàn.

## Kiểm thử và an toàn sản xuất

- Test dùng NAS, fallback, SQLite và cấu hình tạm; không ghi `F:\ContentOpsFallback` thật.
- Kiểm tra direct NAS, fallback, persist hai đường dẫn, xử lý local thành công, sync/verify, retry/restart, snapshot owner, idempotency, conflict, cleanup gating, ổ F lỗi/thiếu dung lượng và nhiều job cô lập.
- Toàn bộ hồi quy: `160 passed, 1 warning`.
- Không dừng/restart YT_NOTIFI, Task Scheduler, YTDOWNLOAD, Silence Cutter hoặc Qwen trong phase này.
