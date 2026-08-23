# CONTENTOPS FINAL LOCAL ARCHITECTURE REPORT

Ngày kiểm tra: 23/08/2026

## Cổng cố định

- YT_NOTIFI: `127.0.0.1:8787`
- Manual LAN API: `:8780` — chỉ phục hồi/thủ công
- YTDOWNLOAD: `127.0.0.1:8790`
- Silence Scheduler: `127.0.0.1:8791`
- Qwen Worker: `127.0.0.1:8792`

## Luồng tự động

YT_NOTIFI → YTDOWNLOAD `:8790` → file local đã hoàn tất → Silence Scheduler `:8791` → Qwen `:8792` → luồng downstream.

YT_NOTIFI không còn chuyển job tự động sang `:8780` và không gọi trực tiếp `:8792`. Trạng thái Qwen được đọc qua health của scheduler `:8791`.

## Luồng thủ công

Manual LAN API `:8780` gắn `origin=MANUAL_LAN`, dùng YTDOWNLOAD `:8790`, chờ đường dẫn local đã xác minh, rồi gửi vào cùng scheduler `:8791`. LAN API không gọi Qwen.

## Queue dùng chung

Scheduler lưu bền vững theo `handoff_id`, ghi `origin`, queue position và trạng thái active/waiting. Executor bị khóa ở một processing slot; FIFO được giữ theo thứ tự tạo. Job cũ khi khởi động lại giữ nguyên external ID và không được tạo bản sao.

## Kiểm thử

- YT_NOTIFI routing/control: `46 passed, 2 skipped`.
- Silence Scheduler/LAN routing: `6 passed` focused architecture tests.
- YTDOWNLOAD Node suite: `74 passed`.
- Python syntax và `git diff --check`: đạt ở cả hai repo.
- Full YT_NOTIFI suite: timeout trong môi trường hiện tại trước khi có kết quả hoàn chỉnh; không dùng kết quả đó để tuyên bố pass.
- Full Silence Cutter suite: một số test cũ phụ thuộc PIL/formatter môi trường hiện tại; focused architecture tests đạt.

## Live health

Tại thời điểm kiểm tra, `8787`, `8780`, `8790`, `8791`, `8792` đều đang LISTEN; scheduler báo `READY`, Qwen báo `READY`.

Acceptance AUTO + MANUAL thực tế chưa chạy để tránh tự tạo/tải video ngoài ý muốn trong môi trường đang có job người dùng. Mã và kiểm thử tích hợp focused đã sẵn sàng cho acceptance có kiểm soát.

## Kết luận

Kiến trúc local đã được chuyển sang mô hình AUTO/MANUAL hội tụ tại một scheduler. Live contention acceptance: `BLOCKED — cần chọn rõ một video hợp lệ để chạy thử thực tế`.
