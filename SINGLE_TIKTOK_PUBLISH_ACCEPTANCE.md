# SINGLE TIKTOK PUBLISH ACCEPTANCE

## Kết quả

**RESULT: BLOCKED**

Không upload và không bấm Post. Hệ thống fail-closed trước hành động TikTok đầu tiên vì hai điều kiện bắt buộc không đạt:

1. MinHa hiện chỉ có fresh UID probe/CDP; không có TikTok publisher hoặc cơ chế đã kiểm chứng để đặt visibility `Only you` cho riêng bài test.
2. SQLite không có processing job nào của kênh đích. Hai MP4 cũ trên NAS không có liên kết job/video/caption nên không thể chọn làm nguồn hợp lệ.

Không thêm publisher mới vì yêu cầu cấm phát minh subsystem mới và yêu cầu dừng nếu private visibility chưa được hỗ trợ tin cậy.

## Đích được ghim cứng

- Channel: `TN003UK - Nhật`
- Channel ID: `UCNiurMpWExWgio2lqldycbA`
- MinHa profile ID: `072d59b2-3a5c-4c3b-abd6-5ab9e829e9aa`
- MinHa profile name: `NDE003`
- TikTok username: `@user7588053660900`
- Expected UID: `7574927887251407894` (string)

Không dùng username để chọn profile, không fallback profile và không chạm profile khác.

## Fresh identity probe

Probe trực tiếp đúng persistent profile ngay trong lần acceptance:

- HTTP: `200`
- Thời điểm: `2026-08-17T15:24:32.166830+00:00`
- Current UID: `7574927887251407894` (string)
- Expected UID: `7574927887251407894` (string)
- Probe status: `DETECTED`
- Logged in: `true`
- Identity: `MATCH`
- Probe error: `null`
- Trạng thái profile trước probe: `stopped`
- Trạng thái profile sau probe: `stopped`

MinHa đã khôi phục trạng thái chạy/dừng ban đầu.

## Kiểm tra video và caption

SQLite:

- Video đã phát hiện của channel: 5.
- Processing job của channel: 0.
- Processing job được chọn: 0.

NAS có hai tệp cũ trong đúng thư mục kênh:

- `\\192.168.1.18\Team 1\ContentOps\TN003UK - Nhật\BETA222C77-1.mp4`
- `\\192.168.1.18\Team 1\ContentOps\TN003UK - Nhật\BETA222C77-2.mp4`

Không có sidecar metadata/caption và không có hàng `processing_jobs` liên kết các tệp này với một YouTube video. Vì vậy cả hai chỉ là ứng viên chưa xác minh, không được chọn hoặc upload.

## PRE-PUBLISH CHECK

```text
PRE-PUBLISH CHECK
Channel: TN003UK - Nhật
Channel ID: UCNiurMpWExWgio2lqldycbA
Profile: NDE003 (072d59b2-3a5c-4c3b-abd6-5ab9e829e9aa)
Username: @user7588053660900
Expected UID: 7574927887251407894
Current UID: 7574927887251407894
Identity: MATCH
Video: BLOCKED — không có processed job/output có provenance
Visibility: BLOCKED — current automation không hỗ trợ Only you
Caption: BLOCKED — không có caption liên kết với processed output
Other jobs selected: 0
```

PRE-PUBLISH CHECK không đạt toàn bộ trường bắt buộc, nên không tiến hành upload/Post.

## Bằng chứng acceptance

- `channel_name`: `TN003UK - Nhật`
- `channel_id`: `UCNiurMpWExWgio2lqldycbA`
- `video_source_path`: `null`
- `processed_file_path`: `null`
- `minha_profile_id`: `072d59b2-3a5c-4c3b-abd6-5ab9e829e9aa`
- `minha_profile_name`: `NDE003`
- `tiktok_username`: `user7588053660900`
- `expected_tiktok_uid`: `7574927887251407894`
- `current_tiktok_uid`: `7574927887251407894`
- `identity_state_before_publish`: `MATCH`
- `visibility`: `Only you` yêu cầu, nhưng `UNSUPPORTED`
- `caption`: `null`
- `upload_started_at`: `null`
- `upload_completed_at`: `null`
- `publish_result`: `BLOCKED`
- `tiktok_post_id_or_url`: `null`
- `failure_reason`: `PRIVATE_VISIBILITY_UNSUPPORTED; NO_PROCESSED_JOB_WITH_PROVENANCE; CAPTION_UNAVAILABLE`
- Other profiles touched: `0`
- Other jobs published: `0`
- TikTok uploads: `0`
- TikTok posts: `0`

## Kiểm thử

- MinHa identity guard: `python -m pytest -q backend/tests/test_tiktok_identity.py` → `2 passed`.
- YT_NOTIFI mapping/probe guard: `python -m pytest -q tests/test_minha_mapping.py` → `8 passed, 1 skipped`.

Các test hiện có chứng minh MATCH được phép qua identity guard; UID unlocked, mismatch, probe error và các trạng thái không hợp lệ bị chặn; mapping dùng stable profile ID và không fallback. Không thêm test publish/single-job/race giả tạo vì codebase chưa có publisher để kiểm thử. Việc triển khai publisher, private visibility và durable once-only receipt là thay đổi riêng cần được thiết kế trước lần acceptance tiếp theo.

## Ảnh và log

- Screenshot: không tạo.
- Log publish: không tạo vì upload chưa bắt đầu.
- Bằng chứng probe được lưu bởi MinHa tại `tiktok_last_checked_at` nêu trên.

Không commit, không push.
