# Báo cáo JOIN Channel Job Handoff + NAS Routing

## Kết quả

YT_NOTIFI giữ nguyên luồng Telegram và đồng thời tạo một processing job bền vững cho mỗi video thật sự mới. Các kênh hiện có được xem là JOIN channel; không thêm trường `joined` và không đổi schema `channels.json`.

## File thay đổi

- `.env.example`: thêm `NAS_OUTPUT_ROOT`
- `app/config.py`: nạp NAS root từ hệ thống cấu hình hiện có
- `app/jobs.py`: làm sạch tên thư mục, kiểm tra NAS và tạo job
- `app/state.py`: migration bảng `processing_jobs` và các hàm lưu/đọc job
- `app/detector.py`: tạo job tại nhánh video mới trước Telegram
- `app/poller.py`: truyền NAS root vào hook detector
- `app/main.py`: thêm `GET /api/jobs`
- `app/dashboard.html`: thêm mục Jobs tối giản
- `tests/test_jobs.py`, `tests/test_poller.py`, `tests/test_dashboard.py`: bao phủ handoff và regression

`config/channels.json` có thay đổi riêng của người dùng và không thuộc commit này.

## Job model và database

Bảng SQLite mới `processing_jobs` gồm:

- `id` — khóa số tự tăng
- `created_at`
- `status` — `QUEUED` hoặc `FAILED`
- `video_id` — `UNIQUE`, ngăn job trùng
- `video_url`, `video_title`
- `source_channel_id`
- `channel_name` — giữ nguyên tên JOIN dùng trong UI/data
- `output_dir`
- `error`

API `GET /api/jobs` trả job mới nhất trước. Không có write API trong phase này.

## NAS root và định tuyến

Biến môi trường:

```env
NAS_OUTPUT_ROOT=\\192.168.1.18\ContentOps
```

Ưu tiên UNC; source code không chứa drive letter, IP hoặc NAS path cố định. Output được tạo theo:

```text
NAS_OUTPUT_ROOT / sanitized(channel.name)
```

Tên JOIN gốc không bị sửa. Giá trị filesystem riêng loại bỏ ký tự Windows không hợp lệ `< > : " / \ | ? *`, control characters, dấu cách/dấu chấm cuối; kết quả rỗng dùng `Channel`; tên thiết bị dành riêng như `CON`, `NUL`, `COM1` được thêm `_`.

## Hook video mới và thứ tự

Hook nằm trong `handle_detected_video()` ngay sau `state.record_event()` trả về video mới, sau nhánh baseline và trước `deliver_notification()`:

```text
record_event mới
→ tạo/kiểm tra job và thư mục
→ gửi Telegram
```

Baseline, duplicate, kênh disabled và baseline sau re-enable không đi qua hook job. SQLite video dedupe và Telegram exactly-once không đổi.

## Hành vi lỗi

Trước khi đặt `QUEUED`, runtime xác nhận `NAS_OUTPUT_ROOT` tồn tại và là thư mục, sau đó tạo/reuse thư mục kênh. Nếu NAS không truy cập được:

- vẫn lưu job `FAILED`
- `error = NAS_UNAVAILABLE`
- không fallback sang local storage
- log ngắn `JOB_FAILED video_id=...`
- Telegram vẫn được gửi
- poller và các kênh khác tiếp tục chạy

Lỗi bất ngờ trong handoff được log `JOB_CREATE_FAILED`; không chặn Telegram.

## Duplicate protection

Job chỉ được yêu cầu sau quyết định NEW hiện có. Ngoài ra, `processing_jobs.video_id` có unique constraint và insert dùng `INSERT OR IGNORE`, nên poll lặp hoặc race không tạo job thứ hai.

## Kiểm thử

Lệnh cuối:

```text
python -m pytest -q
```

Kết quả trước commit: **78 passed, 1 warning**. Warning duy nhất là deprecation từ Starlette TestClient/httpx.

Bao phủ:

- video mới tạo đúng một job
- baseline và duplicate không tạo thêm job
- disabled và re-enable baseline không tạo backlog job
- làm sạch tên Windows
- tạo thư mục thiếu và reuse thư mục có sẵn
- NAS unavailable tạo `FAILED/NAS_UNAVAILABLE`
- lỗi NAS không crash poller và không chặn Telegram
- API trả newest first
- toàn bộ test cũ vẫn đạt

## Manual validation

Đã dùng root tạm `D:\YT_NOTIFI_NAS_TEST`, DB riêng và tên `Test: Channel?`:

- chuỗi phân loại: `BASELINE → NEW → DUPLICATE`
- Telegram giả được gọi đúng 1 lần
- tạo đúng 1 job `QUEUED`
- giữ `channel_name = Test: Channel?`
- `output_dir = D:\YT_NOTIFI_NAS_TEST\Test Channel`
- thư mục tồn tại
- poll lặp không tạo job thứ hai

Dashboard test riêng hiển thị đầy đủ Channel, Video, trạng thái lỗi, Output và Created; không có console error. Máy chủ và dữ liệu manual tạm đã được xóa. Không thực hiện upload thật hoặc kiểm tra NAS thật vì không có quyền điều khiển kênh/NAS trong lượt này.

## Quy tắc hoàn tất file tương lai

Processor downstream trong phase sau phải ghi `filename.processing.mp4`, rồi chỉ rename nguyên tử thành `filename.mp4` sau khi xử lý hoàn tất.

## Xác nhận phạm vi

Phase này **KHÔNG tích hợp YTDOWNLOAD, FFmpeg hoặc Silence Cutter**; không tải hay tạo video file nào.

JOIN CHANNEL JOB HANDOFF COMPLETE
