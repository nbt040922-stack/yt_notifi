from unittest.mock import Mock

from app.detector import deliver_processing_notification, handle_detected_video, resume_processed_notifications
from app.models import VideoEvent
from app.state import StateStore
from app.telegram import TelegramNotifier


def event(video_id="dQw4w9WgXcQ"):
    return VideoEvent(video_id, "UC_x5XG1OV2P6uZZ5FSM9Ttw", "Video", "", "", f"https://youtu.be/{video_id}")


def test_cut_off_notifies_immediately(settings):
    state, notifier = StateStore(settings.state_db), Mock()
    notifier.send_video.return_value = True
    result = handle_detected_video(event(), state, notifier, {event().channel_id: "Channel"}, create_job=False)
    assert result == "NEW"
    notifier.send_video.assert_called_once()
    assert state.get_video(event().video_id)["notification_ready"] == 1


def test_cut_on_defers_until_processing_release(settings):
    state, notifier = StateStore(settings.state_db), Mock()
    notifier.send_video.return_value = True
    video = event()
    result = handle_detected_video(
        video, state, notifier, {video.channel_id: "Channel"}, create_job=False,
        defer_notification=True,
    )
    assert result == "NEW"
    notifier.send_video.assert_not_called()
    assert state.pending_notifications() == []
    state.release_notification(video.video_id)
    row = state.get_video(video.video_id)
    assert row["notification_ready"] == 1
    assert row["notification_sent"] == 0


def test_completed_cut_job_releases_notification_after_restart(settings):
    state, notifier = StateStore(settings.state_db), Mock()
    notifier.send_processing_complete.return_value = True
    video = event("abcdefghijk")
    handle_detected_video(video, state, notifier, {video.channel_id: "Channel"}, defer_notification=True)
    state.create_processing_job(video, "Channel", str(settings.local_output_fallback_root), "COMPLETED", None)
    state.update_process_job(1, status="COMPLETED", process_state="DONE", progress=100)
    resume_processed_notifications(state, notifier, {video.channel_id: "Channel"}, {video.channel_id})
    notifier.send_processing_complete.assert_called_once()


def test_completed_cut_job_notifies_edit_and_file_location(settings):
    state, notifier = StateStore(settings.state_db), Mock()
    notifier.send_processing_complete.return_value = True
    video = event("lHGOp2vBH7A")
    handle_detected_video(video, state, notifier, {video.channel_id: "Channel"}, defer_notification=True)
    state.create_processing_job(video, "Channel", str(settings.local_output_fallback_root), "COMPLETED", None)
    output = settings.local_output_fallback_root / "clean_master.mp4"
    state.update_process_job(
        1, status="COMPLETED", process_state="DONE", progress=100,
        processed_file_path=str(output), processed_files_json='["' + str(output) + '"]',
    )
    state.release_notification(video.video_id)

    deliver_processing_notification(state.processing_job(1), state, notifier, "Channel")

    notifier.send_processing_complete.assert_called_once_with(
        video.title, [str(output)], "Channel", video.channel_id, video.url,
    )
    assert state.get_video(video.video_id)["notification_sent"] == 1


def test_completed_cut_notification_contains_channel_details_and_source_link():
    notifier = TelegramNotifier("token", "chat")
    notifier.send_message = Mock(return_value=True)

    assert notifier.send_processing_complete(
        "Video", ["D:/out/PART_1.mp4"], "Kênh đã ghi chú", "UC123", "https://youtu.be/abc123",
    )
    text = notifier.send_message.call_args.args[0]
    assert "Kênh đã ghi chú" in text
    assert "UC123" in text
    assert "https://youtu.be/abc123" in text
    assert "Video nguồn" in text
