# Báo cáo Fully Headless Windows Startup

Ngày acceptance: 2026-08-13

## Task Scheduler cuối cùng

Task `ContentOps Production` vẫn dùng trigger At log on của user hiện tại, interactive logon, không lưu mật khẩu, `StartWhenAvailable`, cùng working directory `D:\yt_notifi`.

Action mới:

- Execute: `wscript.exe`
- Arguments: `"D:\yt_notifi\scripts\start_production_hidden.vbs"`

Đã chạy install hai lần; Task Scheduler chỉ có đúng một task cùng tên. Scheduler không cấu hình delay riêng.

## VBS wrapper

`scripts/start_production_hidden.vbs` tìm `start_production.ps1` tương đối từ chính thư mục script rồi chạy:

`powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "...\start_production.ps1" -StartupDelaySeconds 20`

VBS dùng `shell.Run command, 0, False`: window style ẩn và không chờ tiến trình trả về. Delay 20 giây chỉ tồn tại tại lời gọi này, không bị cộng đôi. Wrapper không chứa token, cookie hoặc secret.

Manual `start_production.bat` vẫn giữ nguyên để hiển thị chẩn đoán. Khi stack ẩn đang chạy, manual launcher trả `Content Ops production is already running.`; ba cổng vẫn chỉ có một listener.

## Tiến trình con và log

Master launcher tiếp tục dùng `-WindowStyle Hidden` cho Electron, Silence Cutter Python và YT_NOTIFI Python, không dùng `-NoNewWindow`, đồng thời giữ redirect stdout/stderr tới:

- `logs/production-launcher.log`
- `logs/yt_notifi.stdout.log`, `logs/yt_notifi.stderr.log`
- `logs/ytdownload.stdout.log`, `logs/ytdownload.stderr.log`
- `logs/silence.stdout.log`, `logs/silence.stderr.log`

Runtime ownership trong `state/production-runtime.json`, mutex, status và stop script không thay đổi.

## Electron headless

Khi `CONTENTOPS_HEADLESS=1`, YTDOWNLOAD không gọi `createWindow()` hoặc `createTray()`. Vì vậy không tạo BrowserWindow rồi mới ẩn; không có renderer process. Lỗi kiểm tra binary trong headless được ghi log thay vì bật dialog. DownloadManager và ContentOps bridge không thay đổi.

## Acceptance chạy thật qua Task Scheduler

Đã dừng production hoàn toàn, xác nhận ba cổng đóng, rồi kích hoạt chính task `ContentOps Production`. Không chạy master thủ công.

Kết quả sau delay 20 giây:

- Task wrapper kết thúc về trạng thái Ready; PowerShell master tiếp tục chạy nền.
- Tất cả process thuộc cây launcher có `MainWindowHandle=0`, title rỗng.
- Số process Electron `--type=renderer`: `0`.
- Không có Electron BrowserWindow/Tray.
- `127.0.0.1:8790/health`: PASS.
- `127.0.0.1:8791/health`: PASS.
- `127.0.0.1:8787/health`: PASS.
- `http://192.168.88.19:8787/health`: PASS.
- Status script báo cả ba dịch vụ RUNNING và NAS reachable.

Không thể chứng minh bằng mã rằng không có flash dưới một frame, nhưng đường autostart không còn khởi chạy trực tiếp console executable: Task Scheduler → `wscript.exe` → VBS window style 0 → PowerShell `-WindowStyle Hidden` → child `-WindowStyle Hidden`.

## Regression

- YT_NOTIFI full suite: `123 passed, 1 warning`.
- YTDOWNLOAD: `74 passed`; preflight PASS; `node --check` PASS.
- Silence Cutter Content Ops bridge: `7 passed`.
- `git diff --check`: PASS ở cả ba repository.

Không thay đổi polling, Telegram, download, Silence Cutter, formatter, part splitting, NAS routing, cleanup, LAN dashboard, ports hoặc firewall.

## Login/reboot vật lý

Acceptance bằng chính scheduled task đã PASS hoàn toàn. Chưa reboot/sign out Windows trong phiên Codex để tránh đóng ứng dụng hoặc tài liệu chưa lưu. Task At log on đã được cài đúng và sẵn sàng cho lần đăng nhập kế tiếp.
