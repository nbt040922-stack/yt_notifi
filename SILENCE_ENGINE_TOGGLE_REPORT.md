# Báo cáo nút bật/tắt Silence Engine

## Kết quả

Dashboard có nút điều khiển Silence Engine toàn cục. Trạng thái được lưu nguyên tử tại
`state/processing-control.json`; hệ thống cũ chưa có tệp này được mặc định là bật.

- `ON`: chỉ báo sẵn sàng khi Qwen trả `READY`, `model_loaded=true` và `warmed_up=true`.
- `OFF`: ngừng nhận handoff xử lý mới, đợi handoff đang chạy kết thúc rồi dừng đúng tiến trình
  `qwen_worker.supervisor` do hệ thống sở hữu.
- `ERROR`: hiển thị lỗi và cho phép bấm lại để thử khởi động.
- Dashboard hiển thị số video đã tải xong đang chờ xử lý.

## Hành vi hàng đợi và khởi động lại

Video vẫn được phát hiện, gửi Telegram và chuyển sang YTDOWNLOAD khi engine tắt. Sau khi tải
xong, job nằm ở `PROCESS_PENDING`, không tăng số lần thử và không bị đánh dấu lỗi vĩnh viễn.
Khi bật lại, job tiếp tục theo thứ tự hiện có và giữ nguyên `handoff_id`.

Launcher đọc trạng thái đã lưu. Nếu engine đang tắt, launcher vẫn chạy YTDOWNLOAD, Silence
Cutter bridge và YT_NOTIFI nhưng không khởi động Qwen. Việc dừng Qwen kiểm tra PID, thời điểm
khởi động và dấu lệnh `qwen_worker.supervisor`, nên không dừng nhầm tiến trình Python khác.

## Ranh giới Silence Cutter bridge

Handoff enhanced mới chỉ được nhận khi Qwen thực sự sẵn sàng. Nếu Qwen đang tắt hoặc chưa
sẵn sàng, bridge trả HTTP `503` với mã `QWEN_WORKER_UNAVAILABLE`; không chạy Qwen cục bộ.
Handoff đã tồn tại vẫn giữ hành vi idempotent để job đang chạy có thể hoàn tất khi tắt engine.

## Hồi quy

- YT_NOTIFI: `python -m pytest -q` — **168 passed**.
- Silence Cutter bridge: `python -m pytest -q tests/test_contentops_process_bridge.py` với thư
  viện runtime Silence Cutter được thêm tạm vào `PYTHONPATH` — **11 passed**.
- `python -m compileall -q app` — đạt.
- Phân tích cú pháp `start_production.ps1` và `stop_production.ps1` — đạt.
- `git diff --check` — đạt.

Toàn bộ kiểm thử là cô lập. Không tắt, bật hoặc khởi động lại dịch vụ production hiện tại.
