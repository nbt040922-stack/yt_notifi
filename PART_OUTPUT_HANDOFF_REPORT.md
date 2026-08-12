# Báo cáo handoff danh sách part

Ngày kiểm tra: 2026-08-12

## Thay đổi

- Thêm cột SQLite `processing_jobs.processed_files_json` bằng migration bổ sung cột, không phá dữ liệu cũ.
- Khi bridge trả `DONE`, worker chỉ chuyển job sang `COMPLETED` nếu `processed_files` không rỗng và mọi đường dẫn đều là file tồn tại.
- Lưu nguyên danh sách bridge trả về vào `processed_files_json`.
- Giữ `processed_file_path` là phần tử đầu tiên để tương thích ngược.
- Không đổi detector, tải xuống, ánh xạ kênh hoặc hợp đồng handoff hiện hữu ngoài trường danh sách kết quả mới.

## Acceptance thật

Job `1`, external ID ổn định `contentops-process-1`:

- Trạng thái YT_NOTIFI: `COMPLETED`
- Trạng thái bridge: `DONE`
- Tiến độ: `100`
- `processed_file_path`: `\\192.168.1.18\Team 1\ContentOps\TN004UK - Nhật\PART_1.mp4`
- `processed_files_json` chứa chính xác:
  - `\\192.168.1.18\Team 1\ContentOps\TN004UK - Nhật\PART_1.mp4`
  - `\\192.168.1.18\Team 1\ContentOps\TN004UK - Nhật\PART_2.mp4`
  - `\\192.168.1.18\Team 1\ContentOps\TN004UK - Nhật\PART_3.mp4`

Ba file đều tồn tại và đã được Silence Cutter probe thành công. Trên NAS không có part trùng, `.PART_*.mp4` hay `.processing`. Retry cùng handoff ID không tạo record/job mới.

## Regression

- Toàn bộ YT_NOTIFI: `102 passed, 1 warning`.
- Có test riêng xác nhận lưu đúng nhiều part.
- Có test riêng xác nhận thiếu bất kỳ file trả về nào thì job chuyển `FAILED` với `MISSING_PROCESSED_FILES`, không chuyển nhầm sang `COMPLETED`.
