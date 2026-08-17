# Báo cáo sửa nút Silence Engine

## Phạm vi

Chỉ sửa điều khiển Silence Engine trên dashboard và vòng đời Qwen do YT_NOTIFI sở hữu. Không thay đổi polling, Telegram, YTDOWNLOAD, Silence Cutter, MinHa hoặc luồng xử lý video.

## Nguyên nhân và hành vi frontend

Logic cũ tính trạng thái gửi đi bằng:

```javascript
!processingControl.silence_engine_enabled || processingControl.qwen_status === 'ERROR'
```

Vì vậy `ON / ERROR` lại gửi yêu cầu bật (`true`) thay vì tắt. Logic mới chỉ đảo `silence_engine_enabled`, nên mọi trạng thái đang bật (`READY`, `STARTING`, `ERROR`) đều gửi `false` sau một lần bấm.

Nút hiện rõ `ON / <trạng thái>`, `OFF / STOPPING`, `OFF / ERROR` hoặc `OFF`. Nút bị khóa khi PATCH đang chạy và trong suốt `STOPPING`. Khi `ERROR`, dashboard hiển thị một dòng lỗi ngắn, giới hạn 160 ký tự và không hiển thị stack trace.

## Chuyển trạng thái backend

- `ON`, `ON / STARTING`, `ON / READY`, `ON / ERROR` → yêu cầu OFF → `silence_engine_enabled=false` → `STOPPING` → `OFF`.
- Nếu cổng Qwen vẫn mở sau khi dừng → `OFF / ERROR` với `QWEN_PORT_STILL_OPEN`.
- `OFF` hoặc `OFF / ERROR` → yêu cầu ON → `STARTING` → trạng thái nạp model/warm-up → `READY`, hoặc `ON / ERROR` nếu khởi động thật sự lỗi.
- Khi tắt, hệ thống vẫn đợi job xử lý đang hoạt động và chỉ dừng tiến trình Qwen có thông tin sở hữu hợp lệ.
- Trạng thái `OFF` do launcher khôi phục với `off_requested_at=null` nay được giữ ổn định, không còn mắc kẹt ở `STOPPING`.

## Nguyên nhân Qwen ERROR thực tế

Trước khi sửa, API trả:

```json
{
  "silence_engine_enabled": true,
  "qwen_status": "ERROR",
  "error": "RuntimeError: local Qwen model is not configured",
  "waiting_jobs": 0
}
```

Stack production ban đầu được chạy khi engine OFF. Nhánh này không đặt `SEMANTIC_QWEN_MODEL`. Khi dashboard bật Qwen động, tiến trình con kế thừa môi trường thiếu đường dẫn model nên worker báo lỗi trên. Model thực tế có sẵn tại:

`D:\Silence_cutter\local_models\Qwen2.5-VL-7B-Instruct-AWQ`

`QwenProcessManager.start()` nay truyền môi trường riêng cho tiến trình con và dùng đúng model cục bộ hiện có làm mặc định khi biến môi trường chưa được cấu hình.

## Nghiệm thu production

Thực hiện trên dashboard thật tại `D:\yt_notifi`:

1. Trạng thái đầu: `ON / ERROR`, lỗi model chưa cấu hình.
2. Bấm một lần: backend nhận `silence_engine_enabled=false`; UI chuyển ngay `OFF / STOPPING` và khóa nút.
3. Tiến trình cũ đã mất metadata sở hữu nên backend báo đúng `OFF / ERROR — QWEN_PORT_STILL_OPEN`, không giả vờ đã tắt.
4. Khởi động lại stack bằng launcher để dọn cây tiến trình cũ: cổng 8792 đóng và không còn tiến trình/GPU entry của Qwen.
5. Sau khi nạp bản sửa: trạng thái giữ ổn định ở `OFF`.
6. Bấm ON: API đi qua `LOADING_MODEL`, `WARMING_UP`, rồi `READY`. Kiểm tra production xác nhận `Model Loaded=True`, `Warm=True`, `Device=cuda`, cổng 8792 mở.
7. Bấm OFF: UI hiện `OFF / STOPPING`, sau đó `OFF`; cổng 8792 đóng, PID Qwen chấm dứt và GPU entry của PID biến mất.
8. Trong cả quá trình, YT_NOTIFI, YTDOWNLOAD và Silence Cutter vẫn `RUNNING`; polling tiếp tục cập nhật, Telegram vẫn `CONFIGURED`, NAS vẫn truy cập được. Không có job đang xử lý phải chờ. MinHa không bị dừng hoặc thay đổi.

Trạng thái production cuối cùng: Silence Engine `OFF`; YT_NOTIFI, YTDOWNLOAD và Silence Cutter đang chạy.

## Kiểm thử

- Kiểm thử riêng dashboard/processing control: `29 passed` trước khi bổ sung ma trận trạng thái.
- Toàn bộ hồi quy: `236 passed, 1 skipped`.
- `python -m compileall -q app tests`: đạt.
- `git diff --check`: đạt.

Các test mới bao phủ logic đảo trạng thái không đặc cách ERROR, bảo vệ PATCH/STOPPING, hiển thị lỗi, các trạng thái bật đều yêu cầu OFF, retry từ OFF/ERROR, lỗi cổng còn mở, trạng thái OFF sau restart và đường dẫn model khi Qwen được bật động.
