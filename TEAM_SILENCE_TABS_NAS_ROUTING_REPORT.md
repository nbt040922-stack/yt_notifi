# BÁO CÁO 4 TEAM SILENCE TABS VÀ NAS ROUTING

## Cấu hình nhóm

Nguồn sự thật duy nhất là `config/team_members.json`, gồm đúng bốn bản ghi:

- ID ổn định: `member_1` đến `member_4`.
- Tên hiển thị: `Member 1` đến `Member 4`.
- Thư mục NAS: `Member_1` đến `Member_4`.

Tên hiển thị và thư mục NAS có thể đổi tại một file mà không đổi owner ID. Loader yêu cầu đúng bốn thành viên, ID duy nhất, đủ trường và tên thư mục con an toàn. Có thể đổi đường dẫn file bằng `TEAM_MEMBERS_FILE`.

## Dashboard và channel ownership

Dashboard lấy danh sách thành viên từ `GET /api/team-members` và tạo động bốn tab Silence cùng tab Notify hiện hữu. Mỗi tab chỉ hiển thị channel và job của owner tương ứng. Add Channel gửi owner của tab hiện hành. Mỗi channel có dropdown Move; API PATCH cho phép thay đổi `owner_id` hoặc `enabled` và từ chối owner ngoài cấu hình.

Silence channels vẫn nằm trong `channels.json`; không chuyển sang SQLite. Record mới có `owner_id`. Record legacy thiếu owner được đọc bằng member đầu tiên và sẽ được persist chuẩn hóa ở lần mutation an toàn tiếp theo. Notify table/API/UI không có owner và không bị thay đổi.

## Snapshot job và NAS routing

`processing_jobs` được migration không phá hủy với cột nullable `owner_id`. Job mới snapshot đồng thời:

- `owner_id` của channel tại thời điểm phát hiện.
- `output_dir` đã resolve đầy đủ.

Cấu trúc output được giữ per-channel dưới thư mục thành viên:

```text
NAS_OUTPUT_ROOT\<member.nas_folder>\<sanitized channel name>
```

Ví dụ: `...\Member_2\Channel Name`. YT_NOTIFI chỉ tạo channel subfolder khi member root đã tồn tại. Thiếu owner config trả `OWNER_CONFIG_MISSING`; NAS root hoặc member root thiếu trả `NAS_UNAVAILABLE`. Không có local fallback và không chuyển sang owner khác.

Bốn member root đã được tạo trên NAS production:

- `\\192.168.1.18\Team 1\ContentOps\Member_1`
- `\\192.168.1.18\Team 1\ContentOps\Member_2`
- `\\192.168.1.18\Team 1\ContentOps\Member_3`
- `\\192.168.1.18\Team 1\ContentOps\Member_4`

## Move, restart và retry

Move channel chỉ tác động video tương lai. Job cũ giữ nguyên `owner_id` và `output_dir`, kể cả sau restart, retry hoặc đổi `display_name`/`nas_folder`. Download Worker và Process Worker tiếp tục đọc `job.output_dir`; không tra owner live. Handoff giữ nguyên `handoff_id`, `enhanced_content_selection=true` và output snapshot qua mọi retry.

Legacy job có `owner_id=NULL` vẫn giữ nguyên output path cũ, không bị âm thầm reroute. UI chỉ gán nhãn legacy job vào member đầu tiên để dễ quan sát.

## Filename và cleanup

YT_NOTIFI không đổi tên output. Test bridge dùng chính xác:

- `My Better Title_PART_1.mp4`
- `My Better Title_PART_2.mp4`
- `My Better Title_PART_3.mp4`

Các path được lưu nguyên trong `processed_files_json`, phần đầu vào `processed_file_path`, rồi cleanup xác minh đúng ba file mà không rename. Toàn bộ điều kiện cleanup hiện hữu giữ nguyên.

## Nghiệm thu

- Production được restart bằng Task Scheduler; YT_NOTIFI, YTDOWNLOAD, Silence Cutter và Qwen đều lên bình thường.
- Trình duyệt thật hiển thị bốn tab Member 1–4 và Notify Channels.
- Channel legacy xuất hiện dưới Member 1; Member 2 trống đúng filter.
- Dropdown move có đủ bốn owner.
- Job legacy hiển thị owner nhưng vẫn giữ output path lịch sử.
- Notify tab vẫn hiển thị đủ bulk form và 21 channel production, không mutation dữ liệu.
- Controlled tests tạo job cho cả bốn owner và xác nhận exact member/channel output paths.

## Kiểm thử

- Toàn bộ YT_NOTIFI: 145 passed, 1 cảnh báo deprecation hiện hữu.
- Silence Cutter bridge contract: 9 passed.
- Team config, invalid owner, legacy normalization, add/filter/move/restart: đạt.
- Snapshot owner/output, future-owner semantics, retry/idempotency và missing NAS: đạt.
- Notify-only không owner, không processing, bulk/poller regression: đạt.
- Python compile và `git diff --check`: đạt.

Không thay đổi YouTube polling, Telegram semantics, YTDOWNLOAD, Silence Cutter, enhanced mode, Qwen, formatter, cleanup logic, Task Scheduler hoặc ports.
