# Báo cáo Notify Channel Cut Tool Toggle

## Migration cơ sở dữ liệu

`notify_channels` được mở rộng tại chỗ bằng hai cột:

- `cut_enabled INTEGER NOT NULL DEFAULT 0`
- `owner_id TEXT NULL`

Không rebuild bảng. Mọi dòng cũ và kênh thêm mới đều mặc định `cut_enabled=false`,
`owner_id=null`, nên sau triển khai không kênh Notify hiện hữu nào tự tải hoặc xử lý video.

## Toggle và chọn owner

Mỗi thẻ Notify hiển thị trực tiếp `Cắt tool: OFF/ON` và member nhận output. API PATCH hỗ trợ
`enabled`, `cut_enabled` và `owner_id`, từ chối owner ngoài bốn member cấu hình, đồng thời từ
chối bật cut khi chưa chọn owner. Khi tắt, owner được giữ để bật lại nhanh. Thay owner chỉ ảnh
hưởng video phát hiện sau đó.

## Luồng OFF

Kênh vẫn được poll trong poller duy nhất, dùng dedupe và gửi Telegram đúng một lần. Không tạo
processing job, vì vậy không tạo handoff tải xuống hay Silence Cutter.

## Luồng ON

Kênh được thêm vào tập channel cần processing của poller hiện có. Khi phát hiện video mới,
hệ thống gọi cùng `create_processing_job()` mà bốn tab Silence đang dùng. Job lập tức lưu
`owner_id` và `output_dir`; việc đổi toggle hoặc owner sau đó không sửa job cũ. Download worker,
Silence handoff với `enhanced_content_selection=true`, cleanup và Jobs view đều được tái sử dụng.

## Silence Engine và NAS fallback

Job Notify cut-enabled có cùng schema và worker với job Silence thông thường. Khi Silence Engine
OFF, video vẫn tải xong rồi nằm `PROCESS_PENDING` không tăng lỗi; khi READY, cùng `handoff_id`
được gửi tiếp. Định tuyến dùng helper team-member hiện có (`owner -> nas_folder -> channel`) và
cùng Process/NAS Sync worker, nên chính sách `F:\ContentOpsFallback` và cleanup không bị tách nhánh.

## Hồi quy

- `python -m pytest -q`: **171 passed**.
- `python -m compileall -q app`: đạt.
- `git diff --check`: đạt.

Kiểm thử dùng SQLite, thư mục NAS, bridge và engine giả trong thư mục tạm. Không sửa production
DB, không chạm NAS thật, không dừng hoặc restart Qwen/Silence/YTDOWNLOAD/YT_NOTIFI.
