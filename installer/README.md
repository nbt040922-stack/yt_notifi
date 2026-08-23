# Bộ cài YT_NOTIFI cho Windows

Chạy `YT_NOTIFI_Setup.exe` và làm theo trình cài đặt. Không cần cài Python, Node.js hay chạy lệnh.

Bộ cài này cài chung YT_NOTIFI và YTDOWNLOAD. YTDOWNLOAD chạy bridge nội bộ tại `127.0.0.1:8790`; launcher sẽ kiểm tra bridge khỏe trước khi khởi động YT_NOTIFI.

Sau khi cài, YT_NOTIFI chạy nền cùng Windows và mở trang cấu hình tại:
`http://127.0.0.1:8787/setup`

Nhập Telegram Bot Token và Chat ID, sau đó bấm **Lưu và gửi tin nhắn kiểm tra**. Token không xuất hiện trong log. Dữ liệu người dùng nằm tại `%LOCALAPPDATA%\YT_NOTIFI` và được giữ lại khi cập nhật hoặc gỡ cài đặt.

Silence Cutter là thành phần tùy chọn, cài riêng. Nút kiểm tra kết nối chỉ báo trạng thái bridge hiện có; bộ cài không đóng gói Silence Cutter.

Log chạy nền: `%LOCALAPPDATA%\YT_NOTIFI\logs\launcher.log` và `yt_notifi.log`.
