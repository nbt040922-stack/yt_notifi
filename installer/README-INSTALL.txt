CÀI ĐẶT NHANH

1. Giải nén toàn bộ thư mục ContentOpsClient.
2. Chạy ContentOps_Client_Setup.exe bằng quyền Administrator.
3. Làm theo hướng dẫn và nhập cấu hình cá nhân.
4. Chờ trạng thái READY.
5. Không xóa thư mục cài đặt khi chương trình đang hoạt động.

YÊU CẦU

- Windows 64-bit và kết nối Internet.
- Quyền Administrator để cài vào C:\Program Files, tạo tác vụ tự khởi động và ghi log.
- Hai cổng nội bộ còn trống: 127.0.0.1:8787 và 127.0.0.1:8790.
- Không cần cài thêm công cụ lập trình, bộ biên dịch, trình tải video hay bộ xử lý media. Các thành phần chạy của ContentOps đã được đóng gói trong gói phát hành.

CÀI LẦN ĐẦU

Nhấp đúp ContentOps_Client_Setup.exe và làm theo màn hình. Đây là cách cài đặt chính thức.

Chỉ dùng script bên dưới như phương án khôi phục/quản trị khi người quản trị yêu cầu:

powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Setup-ContentOpsClient.ps1

Không tắt bảo vệ PowerShell trên toàn bộ máy. Khi hoàn tất, bộ cài hiển thị kết quả kiểm tra và khởi động các dịch vụ.

INSTALLER SẼ TỰ LÀM GÌ

- Kiểm tra Windows 64-bit, thành phần chạy đã đóng gói, cổng và quyền ghi thư mục.
- Chép ứng dụng YT_NOTIFI và YTDOWNLOAD đã đóng gói vào thư mục cài đặt.
- Chép các công cụ tải video và xử lý media đi kèm vào thư mục riêng của YT_NOTIFI.
- Tạo thư mục cấu hình, trạng thái, log và runtime.
- Tạo file cấu hình cục bộ nếu file chưa tồn tại.
- Tạo tác vụ tự khởi động và watchdog cùng Windows.
- Khởi động hai ứng dụng và kiểm tra cổng/health để xác định READY.

CẤU HÌNH CÁ NHÂN

Ứng dụng và runtime nằm tại:
C:\Program Files\ContentOps\Client

File cấu hình mutable là:
C:\ProgramData\ContentOps\Client\config\.env

Điền các mục sau nếu người quản trị yêu cầu:

- TELEGRAM_BOT_TOKEN: token bot Telegram.
- TELEGRAM_CHAT_ID: ID nhóm hoặc cuộc trò chuyện nhận thông báo.
- NAS_OUTPUT_ROOT: thư mục NAS dùng chung, nếu có.
- LOCAL_OUTPUT_FALLBACK_ROOT: thư mục dự phòng trên máy, nếu có.
- YTDOWNLOAD_BRIDGE_URL và SILENCE_CUTTER_BRIDGE_URL chỉ đổi theo hướng dẫn của người quản trị.

Kênh YouTube được thêm và quản lý trong giao diện YT_NOTIFI. Không gửi token, mật khẩu hoặc toàn bộ file .env qua nhóm chat. Nếu giao diện yêu cầu thư mục đầu ra, chọn thư mục có quyền ghi và còn đủ dung lượng.

KHỞI ĐỘNG VÀ KIỂM TRA

- YT_NOTIFI: http://127.0.0.1:8787
- YTDOWNLOAD: http://127.0.0.1:8790

Mở địa chỉ YT_NOTIFI bằng trình duyệt. Cài đặt thành công khi trang mở được và Status-ContentOpsClient.ps1 báo overall là READY.

TỰ KHỞI ĐỘNG CÙNG WINDOWS

Setup tạo ba Scheduled Task chạy khi đăng nhập:

- ContentOps Client - YT_NOTIFI
- ContentOps Client - YTDOWNLOAD
- ContentOps Client - Watchdog

Watchdog kiểm tra hai cổng nội bộ và khởi động lại thành phần bị dừng. Các tác vụ chạy ẩn, không mở cửa sổ làm việc.

ĐIỀU KHIỂN THỦ CÔNG

Tại thư mục gói, chạy bằng “Run with PowerShell”:

- Start-ContentOpsClient.ps1: khởi động.
- Stop-ContentOpsClient.ps1: dừng các tiến trình do ContentOps tạo.
- Restart-ContentOpsClient.ps1: dừng rồi khởi động lại.
- Status-ContentOpsClient.ps1: xem trạng thái, phiên bản, cổng, PID và health.

CẬP NHẬT

1. Tải bản phát hành mới và giải nén vào thư mục tạm.
2. Chạy Update-ContentOpsClient.ps1 bằng quyền Administrator.
3. Chờ kết quả READY rồi mở lại YT_NOTIFI.

Bản cập nhật thay ứng dụng và runtime trong C:\Program Files\ContentOps\Client bằng bản mới, không dùng kho mã nguồn trên máy người dùng. Các thư mục C:\ProgramData\ContentOps\Client\config, state và logs được giữ lại. Hãy sao lưu file .env trước khi cập nhật.

GỠ CÀI ĐẶT

Chạy Uninstall-ContentOpsClient.ps1 bằng quyền Administrator. Lệnh mặc định dừng dịch vụ, xóa Scheduled Task và xóa C:\Program Files\ContentOps\Client nhưng giữ config, state và logs.

Muốn xóa sạch mọi dữ liệu, chạy:

powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Uninstall-ContentOpsClient.ps1 -FullClean

FullClean xóa không thể khôi phục; hãy sao lưu trước.

KHI CÓ LỖI

1. Chạy Status-ContentOpsClient.ps1 và lưu toàn bộ kết quả.
2. Nếu overall là DOWN hoặc DEGRADED, chạy Restart-ContentOpsClient.ps1 một lần.
3. Log nằm tại C:\ProgramData\ContentOps\Client\logs (yt_notifi.stdout.log, yt_notifi.stderr.log, ytdownload.stdout.log, ytdownload.stderr.log).
4. Gửi người quản trị ảnh màn hình lỗi, kết quả Status, thời điểm xảy ra và tên log liên quan.
5. Che token Telegram, mật khẩu và token kết nối trước khi gửi. Không tự xóa state hoặc database.

KIỂM TRA GÓI

Gói phát hành gồm README-INSTALL.txt, ContentOps_Client_Setup.exe (nếu bản phát hành đã tạo), Setup/Update/Uninstall, bốn wrapper Start/Stop/Restart/Status, client-manifest.json, build-identity.json, thư mục scripts, yt_notifi và ytdownload. Máy người dùng không cần môi trường phát triển; việc đóng gói được thực hiện trước trên máy build.
