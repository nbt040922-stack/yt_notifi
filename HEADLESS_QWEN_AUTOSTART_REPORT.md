# HEADLESS QWEN AUTOSTART REPORT

## Kết quả

Qwen Worker đã được đưa vào stack production headless do `start_production.ps1` quản lý. Task Scheduler vẫn chỉ có một task `ContentOps Production`, gọi VBS ẩn hiện hữu.

## Vòng đời và an toàn

- Khởi chạy đúng lệnh `D:\Silence_cutter\.venv_asr_test\Scripts\python.exe -m qwen_worker.supervisor`, thư mục làm việc `D:\Silence_cutter`.
- Model cục bộ: `D:\Silence_cutter\local_models\Qwen2.5-VL-7B-Instruct-AWQ`; không tải hay cài lại model.
- Qwen chỉ lắng nghe `127.0.0.1:8792`; thử truy cập qua địa chỉ LAN bị chặn.
- Launcher chỉ chấp nhận `READY` khi đồng thời có `model_loaded=true` và `warmed_up=true`; timeout 120 giây.
- Các chuyển trạng thái có ý nghĩa được ghi log và lưu vào runtime; không poll-log liên tục.
- PID, thời điểm bắt đầu, health và port Qwen được lưu trong `state/production-runtime.json`.
- Stop/failure cleanup dùng đúng PID + thời điểm bắt đầu + marker lệnh; không quét/kill Python toàn cục.
- Kiểm tra port 8792 trước khi chạy ngăn nhận nhầm worker ngoài quyền sở hữu.
- `production_status.ps1` hiển thị trạng thái, model loaded, warm, model, device và vị trí log lỗi.
- Không mở cửa sổ console; stdout/stderr tập trung tại `logs/qwen-worker.*.log`.

## Nghiệm thu thật

- Task Scheduler action giữ nguyên: `wscript.exe "D:\yt_notifi\scripts\start_production_hidden.vbs"`.
- Bốn dịch vụ đạt health: YT_NOTIFI 8787, YTDOWNLOAD 8790, Silence Cutter 8791, Qwen 8792.
- Qwen cold start: load model 16,276 giây; warmup 2,286 giây; READY sau 18,570 giây.
- Toàn stack sẵn sàng sau 43,281 giây, gồm delay Task Scheduler 20 giây.
- GPU: NVIDIA GeForce RTX 5060 Ti 16.311 MiB; dùng khoảng 8.492–8.553 MiB khi Qwen thường trú.
- Hai yêu cầu mô phỏng `selector` và `semantic_cleaner` cùng dùng một server PID; `model_load_count` giữ nguyên 1, `request_count` tăng 0 → 2.
- Sau hơn 2 phút nhàn rỗi, phiên khởi động, PID, `model_load_count=1` và VRAM thường trú không đổi.
- Crash có kiểm soát tiến trình server: supervisor giữ nguyên PID 7692; server đổi PID 6932 → 9916; tự đi qua `LOADING_MODEL → WARMING_UP → READY` trong 17,600 giây.
- Yêu cầu sau phục hồi trả `RECOVERED`; health vẫn `READY`, loaded và warmed.
- Hai lần khởi động lỗi trong nghiệm thu (xung đột port cũ và thiếu cấu hình model) đều cleanup dịch vụ đã tạo; không còn tiến trình mồ côi. Tương thích runtime cũ thiếu trường Qwen cũng đã được bổ sung.
- Máy Windows dùng WDDM nên `nvidia-smi` không báo VRAM theo từng PID; số VRAM trên là tổng GPU tại thời điểm stack Qwen thường trú.

## Kiểm thử

- YT_NOTIFI: `python -m pytest -q` — 126 passed.
- Silence Cutter: test Qwen worker + Content Ops bridge — 14 passed.
- YTDOWNLOAD: `npm test` — 74 passed.
- YTDOWNLOAD: `npm run preflight` — đạt.
- PowerShell parser và `git diff --check` — đạt.

Không thay đổi pipeline xử lý video, selector, semantic cleanup, preview/render, downloader, NAS, Telegram hay cấu hình firewall hiện hữu.
