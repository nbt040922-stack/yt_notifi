# Báo cáo khóa chỉnh sửa thành viên trên Dashboard

## Trạng thái cuối

- Tên thành viên đã được cấu hình xong.
- Nút **Sửa** và hộp thoại chỉnh sửa đã bị gỡ khỏi Dashboard.
- API cập nhật thành viên đã bị gỡ; `PATCH /api/team-members/{member_id}` trả `404 Not Found`.
- Giao diện Notify Channels không thay đổi.

## Cấu hình

`config/team_members.json` tiếp tục là nguồn dữ liệu duy nhất. Chỉ quản trị viên có quyền sửa tệp cục bộ mới có thể đổi tên hoặc thư mục NAS. `member_1` đến `member_4` vẫn là các ID cố định.

## Quyền sở hữu và định tuyến NAS

- Đổi tên hoặc thư mục NAS không sửa `channel.owner_id`.
- Job đã tồn tại tiếp tục dùng nguyên `owner_id` và `output_dir` đã chụp.
- Job mới dùng `nas_folder` mới từ cấu hình đang hoạt động.
- Không tạo, đổi tên hoặc di chuyển thư mục/dữ liệu NAS trong thay đổi này. Nếu thư mục mới chưa tồn tại, kiểm tra NAS nghiêm ngặt hiện có sẽ báo lỗi như trước.

## Kiểm thử

- Kiểm tra `GET /api/team-members` vẫn trả bốn thành viên.
- Kiểm tra API `PATCH` không còn tồn tại.
- Kiểm tra Dashboard không còn nút hoặc hộp thoại sửa thành viên.
