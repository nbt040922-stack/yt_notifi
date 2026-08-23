# TIKTOK PUBLISHER CORE v1 REPORT

## Kiến trúc

Luồng mới chỉ chạy khi có lệnh rõ ràng cho một đầu ra cụ thể:

`processing_job hoàn tất -> publish_job bền vững -> probe UID mới -> hồ sơ MinHa chính xác -> tải lên -> caption -> Only you -> READY_FOR_POST`

Publisher Core hỗ trợ chạy thật chỉ qua publish job ID rõ ràng. Auto-post chỉ được gọi tại thời điểm processing job hoàn tất; không có scheduler độc lập, chế độ hàng loạt hay kích hoạt từ polling.

## Tệp thay đổi

- `app/tiktok_publisher.py`: hàng đợi, kiểm tra nguồn gốc, định danh, điều khiển trình duyệt, idempotency và biên nhận.
- `app/minha.py`: các lệnh MinHa tối thiểu cho probe, trạng thái, mở và dừng hồ sơ.
- `app/main.py`: API tạo/xem/chạy thử publish job.
- `requirements.txt`: Playwright để kết nối CDP vào đúng hồ sơ MinHa đang chạy.
- `tests/test_tiktok_publisher.py`: kiểm thử chuyên biệt Publisher Core v1.

## Cơ sở dữ liệu

SQLite có thêm:

- `publish_jobs`: lưu job, trạng thái, caption, đường dẫn, UID, lỗi, số lần chạy và PRE-PUBLISH CHECK.
- `publish_receipts`: biên nhận bền vững sau khi xác minh đăng thành công.

Khóa `idempotency_key` là SHA-256 của `processing_job_id + định danh đường dẫn output + minha_profile_id` và có ràng buộc UNIQUE.

`DONE` chỉ được ghi cùng một biên nhận có phương thức xác minh cụ thể. ID/URL TikTok được phép để trống nếu giao diện chưa cung cấp; hệ thống không tự bịa.

## API

- `POST /api/publish-jobs`
- `GET /api/publish-jobs`
- `GET /api/publish-jobs/{id}`
- `POST /api/publish-jobs/{id}/run`

Tạo job bắt buộc truyền chính xác `processing_job_id`, `channel_id` và `video_path`. API chạy mặc định là dry-run; `dry_run=false` chỉ chạy khi có publish job ID cụ thể.

## Kiểm tra nguồn gốc

Publisher chỉ nhận output khi:

- processing job tồn tại và đã hoàn tất;
- `process_state=DONE`;
- kênh khớp;
- file nằm trong `processed_files_json` hoặc hợp đồng cũ `processed_file_path` của chính job;
- file tồn tại và không rỗng;
- caption lấy nguyên văn từ `video_title` của job;
- `minha_profile_id` trong job còn khớp mapping hiện tại của kênh.

Không quét NAS, không chọn file mới nhất, không suy luận caption từ tên file và không nhận file mồ côi.

## Bảo vệ định danh

Ngay trước upload, hệ thống:

1. đọc hồ sơ bằng stable `minha_profile_id`;
2. yêu cầu expected UID là chuỗi số thập phân;
3. chạy probe MinHa mới;
4. đọc lại hồ sơ sau probe;
5. yêu cầu logged-in, UID hiện tại là chuỗi số, hai UID bằng nhau và trạng thái mới là `MATCH`.

Mọi trạng thái thiếu/mâu thuẫn đều BLOCK trước khi mở trang upload. Giá trị MATCH cũ trên dashboard không thể bỏ qua probe mới. Username chỉ được lưu làm bằng chứng, không dùng để định tuyến.

## Trình duyệt và quyền riêng tư

Playwright kết nối qua CDP vào đúng persistent profile MinHa. Không tạo profile tạm, không sao chép cookie, không đổi proxy/fingerprint và không có profile dự phòng.

Nếu hồ sơ ban đầu dừng, Publisher mở qua MinHa rồi trả về trạng thái dừng. Nếu ban đầu đang chạy, hồ sơ vẫn chạy. Không đụng hồ sơ khác.

Selector upload/caption/quyền riêng tư được gom trong một lớp. Chỉ `ONLY_YOU` được chấp nhận; không xác minh được quyền riêng tư thì BLOCK. Dry-run dừng tại `READY_FOR_POST`, tuyệt đối không nhấn Đăng.

## Idempotency, đồng thời và sự cố

- Một output chỉ có một publish job v1; không có override.
- Chuyển trạng thái nhận việc dùng giao dịch `BEGIN IMMEDIATE`.
- Toàn hệ thống chỉ cho một publish job ở trạng thái đang chạy.
- Job `READY_FOR_POST` hoặc `DONE` không thể chạy lần hai.
- Khi khởi động lại, job bị ngắt trước giai đoạn Đăng chuyển `FAILED`.
- Job bị ngắt ở `POSTING`/`VERIFYING` chuyển `POST_RESULT_UNCERTAIN` và không thể tự chạy lại.

## Kiểm thử

Đã kiểm tra:

- nguồn gốc hợp lệ, file mồ côi, thiếu job, sai kênh, thiếu caption/file;
- toàn bộ trạng thái khóa định danh và MATCH mới;
- stable profile ID, không định tuyến bằng username, không fallback;
- quyền riêng tư và upload phải được xác minh;
- chống tạo trùng, chạy đúp, chạy lại DONE và tranh chấp hai job;
- phục hồi `POST_RESULT_UNCERTAIN`;
- giữ nguyên trạng thái chạy/dừng của hồ sơ;
- biên nhận bền vững trước khi chuyển DONE.

Kết quả:

- Publisher chuyên biệt: `21 passed`.
- Toàn bộ YT_NOTIFI: `257 passed, 1 skipped`.
- Kiểm tra cú pháp Python: đạt.
- Kiểm tra phụ thuộc môi trường: đạt, không có gói lỗi.
- `git diff --check`: đạt.

## DRY-RUN ACCEPTANCE

- Channel: `TN003UK - Nhật` (`UCNiurMpWExWgio2lqldycbA`)
- Processing job: không có job hoàn tất hợp lệ
- Output: không chọn; hai file NAS mồ côi không được sử dụng
- Profile: `NDE003` (`072d59b2-3a5c-4c3b-abd6-5ab9e829e9aa`)
- Username: `user7588053660900`
- Expected UID: `7574927887251407894`
- Current UID: `7574927887251407894`
- Identity: `MATCH`, `DETECTED`, đã đăng nhập
- Trạng thái profile trước/sau probe: `stopped -> stopped`
- Caption: chưa có vì không có processing job hợp lệ
- Visibility: bắt buộc `ONLY_YOU`, chưa cấu hình trên TikTok vì chưa có output hợp lệ
- Upload: KHÔNG
- Post clicked: KHÔNG
- Idempotency: schema và khóa UNIQUE sẵn sàng; chưa tạo job thật
- Other profiles touched: KHÔNG
- Other jobs published: KHÔNG

Cách tạo dữ liệu thử an toàn nhất là để đúng kênh TN003UK có một video mới đi qua luồng hiện hữu YouTube polling -> YTDOWNLOAD -> Silence Cutter -> NAS. Không thêm đường tắt ghi DB và không dùng file NAS mồ côi.

## Kết quả cuối

`BLOCKED`

Lý do duy nhất: chưa có processing job hoàn tất, có nguồn gốc và caption hợp lệ cho TN003UK. Publisher Core v1 đã sẵn sàng cho một dry-run ngay khi job đó xuất hiện.
