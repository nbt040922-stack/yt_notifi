# Báo cáo đổi tên hiển thị kênh

## Giao diện

Mỗi channel card có nút bút chì `✎` cạnh tên. Nút mở dialog nhỏ chỉ gồm trường `Tên kênh`,
`Lưu`, `Hủy` và vùng hiển thị lỗi API. Lưu thành công đóng dialog, gọi lại dữ liệu dashboard
không reload trang và giữ nguyên member tab đang mở.

Bulk add không thay đổi: URL vẫn được resolve thành canonical `channel_id` và tên YouTube chính
thức; người dùng chỉ đổi tên riêng sau khi thêm nếu cần.

## API

Endpoint hiện có được mở rộng:

```text
PATCH /api/channels/{channel_id}
{"name": "CNBC News - Nhật"}
```

Tên được trim, cho phép Unicode/Vietnamese, phải khác rỗng và dài tối đa 100 ký tự. Duplicate
display name được phép vì định danh duy nhất vẫn là `channel_id`. ChannelStore tiếp tục ghi
`channels.json` bằng temporary file, flush/fsync và atomic replace.

## Dữ liệu bất biến

Rename chỉ thay `Channel.name`. Nó không đổi `channel_id`, `owner_id`, `cut_enabled`, trạng thái
poll/baseline hoặc video dedupe. Không tạo channel mới và không chỉnh processing job đang có.

## NAS và Telegram

Job cũ giữ nguyên `channel_name` và `output_dir` đã snapshot. Video tương lai dùng tên mới để tạo
folder channel đã sanitize trong đúng member NAS; không tự di chuyển thư mục cũ. Telegram tương
lai dùng tên mới, còn thông báo đã gửi không bị ảnh hưởng.

## Kiểm thử

- `python -m pytest -q`: **171 passed**.
- `python -m compileall -q app`: đạt.
- Kiểm tra cú pháp JavaScript dashboard bằng `node --check`: đạt.
- `git diff --check`: đạt.

Kiểm thử dùng config, SQLite và NAS tạm; không sửa production data trong quá trình phát triển.
