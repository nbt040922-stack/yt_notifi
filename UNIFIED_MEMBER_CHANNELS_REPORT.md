# Báo cáo hợp nhất kênh theo member

## Kiến trúc giao diện cuối

Dashboard chỉ còn bốn tab member. Tab Notify Channels và toàn bộ mã giao diện phụ thuộc collection
Notify đã được gỡ. Mỗi tab có form thêm hàng loạt URL; mọi kênh được tạo với `owner_id` của tab
đang mở và `cut_enabled=false`.

Mỗi thẻ kênh main hiển thị và cho đổi trực tiếp:

- `Cắt tool: OFF/ON`
- Bật/tắt polling
- Xóa kênh

## Ngữ nghĩa cut_enabled

`cut_enabled` được lưu trong từng record `channels.json`.

- OFF: poll, dedupe, gửi Telegram đúng một lần, không tạo processing job.
- ON: tạo processing job hiện có, qua YTDOWNLOAD, Silence Cutter enhanced, NAS/fallback và cleanup.

Owner và output directory được snapshot khi job được tạo. Toggle hoặc đổi owner sau đó chỉ ảnh
hưởng video tương lai. Silence Engine OFF vẫn cho tải xong rồi chờ `PROCESS_PENDING`; khi READY,
job tiếp tục với cùng handoff ID và `enhanced_content_selection=true`.

## Migration main channel

Main channel cũ chưa có `cut_enabled` được đọc và ghi lại với `cut_enabled=true`. Đây là lựa chọn
bảo toàn hành vi tự động xử lý lịch sử. Kênh main thêm mới luôn mặc định OFF.

Không chuyển main channel sang SQLite. `channel_id` tiếp tục duy nhất toàn cục; bulk add trùng
kênh trả thông báo member đang sở hữu và không tự di chuyển kênh.

## Migration legacy Notify

Khi production khởi động, migration đọc bảng `notify_channels` nhưng không xóa hoặc sửa bảng:

- Có owner hợp lệ và chưa tồn tại main: nhập đúng owner/cut mode.
- Đã tồn tại main: main là nguồn chuẩn, không ghi đè owner hoặc cut mode.
- Không có owner: giữ nguyên trong SQLite, không đoán member_1, đồng thời hiển thị cảnh báo.

Khảo sát production trước triển khai: **33** dòng legacy; **20** trùng main, **0** đủ owner để
nhập, **13** chưa xác định owner. Vì vậy restart sẽ không tự định tuyến bất kỳ legacy channel nào
sang sai người. Ba endpoint GET/PATCH/DELETE legacy được giữ tạm để rollback; runtime và dashboard
không dùng chúng. Endpoint bulk legacy đã được thay bằng `/api/channels/bulk`.

## Hồi quy

- `python -m pytest -q`: **167 passed**.
- `python -m compileall -q app`: đạt.
- `git diff --check`: đạt.

Kiểm thử dùng config, SQLite, NAS và bridge tạm. Không sửa production data hoặc restart dịch vụ
trong quá trình viết mã và chạy test.
