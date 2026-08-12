# Báo cáo Windows Auto Start và LAN Dashboard

Ngày triển khai: 2026-08-13

## Kiến trúc cuối

Một entrypoint duy nhất `start_production.bat` gọi `scripts/start_production.ps1`. Master launcher giữ mutex `Local\CONTENTOPS_PRODUCTION_LAUNCHER`, ghi PID/thời điểm bắt đầu vào `state/production-runtime.json`, và chỉ dừng cây tiến trình khớp PID, start time và command marker đã sở hữu.

Thứ tự chạy và health bắt buộc:

1. `D:\YTDOWNLOAD\node_modules\electron\dist\electron.exe .` ở chế độ `CONTENTOPS_HEADLESS=1`; chờ `http://127.0.0.1:8790/health`.
2. `D:\Silence_cutter\.venv_asr_test\Scripts\python.exe contentops_process_bridge.py`; chờ `http://127.0.0.1:8791/health`.
3. `D:\yt_notifi\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8787`; chờ `http://127.0.0.1:8787/health`.

Launcher chỉ báo `PRODUCTION READY` sau khi cả ba health đạt. Cổng phải trống trước khi spawn, vì vậy health của tiến trình cũ không thể bị nhận nhầm. Thử nhánh lỗi khi `8791` bị chiếm đã dừng đúng YTDOWNLOAD vừa sở hữu, không chạy YT_NOTIFI và không để runtime rác. Lần chạy master thứ hai trả `Content Ops production is already running.` và không spawn thêm dịch vụ.

Log được tách thành:

- `logs/production-launcher.log`
- `logs/yt_notifi.stdout.log`, `logs/yt_notifi.stderr.log`
- `logs/ytdownload.stdout.log`, `logs/ytdownload.stderr.log`
- `logs/silence.stdout.log`, `logs/silence.stderr.log`

## Binding và LAN

Production `.env` dùng:

- `YT_NOTIFI_BIND_HOST=0.0.0.0`
- `YT_NOTIFI_PORT=8787`

IP private lấy từ adapter đang Up, ưu tiên default route và bỏ qua tên VPN/virtual/loopback. IP phát hiện: `192.168.88.19`.

Socket thực tế:

- `0.0.0.0:8787` — YT_NOTIFI dashboard/API.
- `127.0.0.1:8790` — YTDOWNLOAD bridge.
- `127.0.0.1:8791` — Silence Cutter bridge.

Qua địa chỉ LAN `http://192.168.88.19:8787/` trên network stack thật của máy chủ:

- health và dashboard trả HTTP 200;
- resolve `https://www.youtube.com/@YouTube` thành `UCBR8-60-B28hp2BmDPdntcQ`;
- thêm kênh tạm đạt 201;
- disable, enable, delete đều đạt 200;
- dữ liệu API xác nhận kênh tạm đã bị xóa;
- kết nối LAN tới `8790` và `8791` đều bị từ chối.

ChannelStore hiện hữu được giữ nguyên: một lock bảo vệ toàn bộ read-modify-write, ghi file tạm, `fsync`, rồi `os.replace`. Test ghi đồng thời và duplicate hiện hữu tiếp tục đạt. Không có database kênh thứ hai và không cần CORS vì UI/API cùng origin.

## Firewall

Rule đã cài hai lần để kiểm tra idempotency và chỉ còn đúng một rule:

- Tên: `YT_NOTIFI Dashboard LAN`
- Direction: Inbound
- Action: Allow
- Profile: Private
- Protocol/port: TCP 8787
- RemoteAddress: LocalSubnet

Không có rule mở `8790` hoặc `8791`. Adapter default-route `Ethernet 2` đã chuyển từ Public sang Private để rule có hiệu lực trên LAN tin cậy. Không cấu hình tunnel, Cloudflare, port forwarding hoặc public internet.

## Task Scheduler

Script install/uninstall chỉ quản lý task `ContentOps Production`. Install chạy hai lần và Task Scheduler còn đúng một task:

- Trigger: At log on, user hiện tại `nbt04`.
- Logon type: Interactive; không lưu mật khẩu trong dự án.
- Action: `powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "D:\yt_notifi\scripts\start_production.ps1" -StartupDelaySeconds 20`.
- Working directory: `D:\yt_notifi`.

Đã mô phỏng logon bằng cách dừng toàn bộ master, gọi chính task (không chạy launcher thủ công), chờ delay 20 giây. Task khởi động đúng một stack; cả ba health PASS và runtime ghi đúng ba tiến trình sở hữu.

## Stop và status

`stop_production.ps1` dừng theo thứ tự YT_NOTIFI → Silence Cutter → YTDOWNLOAD, sau đó launcher; chỉ PID/start time/marker trong production runtime được phép dừng. Thử nghiệm thật đã dừng sạch cả ba cổng. `production_status.ps1` báo trạng thái ba dịch vụ, URL local/LAN và NAS mà không in secret.

## Pipeline và restart safety

Sau khi master và Task Scheduler khởi động stack:

- NAS báo reachable;
- job acceptance trước vẫn `COMPLETED/CLEANED`;
- output part trên NAS vẫn tồn tại;
- không có job download/process đến hạn;
- không tải lại, render lại hoặc cleanup lặp.

Không thay đổi detector, Telegram, downloader, Silence Cutter, formatter, part, NAS routing hay cleanup. Chưa phát video công khai mới chỉ để test launcher; full regression và trạng thái real job được dùng làm safe pipeline regression.

## Kiểm thử

- YT_NOTIFI: `122 passed, 1 warning`.
- YTDOWNLOAD: `73 passed`; preflight PASS; `node --check` PASS.
- Silence Cutter Content Ops bridge: `7 passed`.
- PowerShell parser: mọi script mới có 0 lỗi cú pháp.
- `git diff --check`: PASS ở cả ba repository.

## Acceptance còn cần thao tác vật lý

- Task-trigger acceptance đã PASS nhưng chưa reboot Windows thật trong phiên Codex này, để tránh đóng ứng dụng/tài liệu chưa lưu của người dùng mà không có xác nhận ngay trước khi reboot.
- LAN network-stack acceptance đã PASS; xác nhận cuối từ một điện thoại/máy khác trên cùng LAN vẫn cần người dùng mở `http://192.168.88.19:8787/`.
