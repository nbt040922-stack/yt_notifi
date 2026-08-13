# Báo cáo chỉnh sửa thành viên trên Dashboard

## Giao diện

- Mỗi tab Silence có nút **Sửa** riêng.
- Hộp thoại cho phép thay đổi độc lập tên hiển thị và tên thư mục NAS.
- Lưu thành công sẽ đóng hộp thoại, tải lại danh sách thành viên và cập nhật ngay nhãn tab, tên chủ sở hữu trên thẻ kênh/job.
- Lỗi API được hiển thị trực tiếp trong hộp thoại.
- Giao diện Notify Channels không thay đổi.

## API

`PATCH /api/team-members/{member_id}` nhận một hoặc cả hai trường:

```json
{
  "display_name": "Nhan",
  "nas_folder": "Nhan"
}
```

- `display_name`: sau khi bỏ khoảng trắng phải dài 1–50 ký tự.
- `nas_folder`: sau khi bỏ khoảng trắng phải dài 1–80 ký tự và là một thành phần thư mục an toàn; dấu phân cách, đường dẫn tuyệt đối, UNC và ổ đĩa đều bị từ chối.
- Payload không chấp nhận `id`; `member_1` đến `member_4` là các ID cố định.

## Lưu cấu hình

`config/team_members.json` tiếp tục là nguồn dữ liệu duy nhất. Bản cập nhật được ghi vào tệp tạm cùng thư mục, đồng bộ xuống đĩa, đọc và kiểm tra lại đầy đủ bốn thành viên, rồi mới thay thế tệp chính bằng thao tác nguyên tử. Nếu ghi/thay thế thất bại, tệp chính không đổi và tệp tạm được dọn.

## Quyền sở hữu và định tuyến NAS

- Đổi tên hoặc thư mục NAS không sửa `channel.owner_id`.
- Job đã tồn tại tiếp tục dùng nguyên `owner_id` và `output_dir` đã chụp.
- Job mới dùng `nas_folder` mới từ cấu hình đang hoạt động.
- Không tạo, đổi tên hoặc di chuyển thư mục/dữ liệu NAS trong thay đổi này. Nếu thư mục mới chưa tồn tại, kiểm tra NAS nghiêm ngặt hiện có sẽ báo lỗi như trước.

## Kiểm thử

- Kiểm thử riêng cho chỉnh sửa thành viên và định tuyến: `23 passed`.
- Toàn bộ bộ kiểm thử YT_NOTIFI: `160 passed, 1 warning`.
- Tất cả kiểm thử dùng cấu hình, SQLite và cây NAS tạm; không gọi dashboard sản xuất.
- Không dừng hoặc khởi động lại bất kỳ dịch vụ sản xuất nào.
