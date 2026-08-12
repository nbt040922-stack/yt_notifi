import asyncio
import json
import subprocess
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from app.config import Channel, enabled_channels
from app.poller import ChannelPoller, channel_videos_url, parse_ytdlp_result, probe_channel
from app.state import StateStore
from tests.conftest import CHANNEL_ID, VIDEO_ID

NEW_VIDEO = "abcdefghijk"
NEWEST_VIDEO = "zyxwvutsrqp"
SECOND_CHANNEL = "UCaaaaaaaaaaaaaaaaaaaaaa"


def payload(*entries):
    return json.dumps({"entries": [{"id": video_id, "title": title, **extra} for video_id, title, extra in entries]})


def completed(stdout):
    return subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")


def configured(settings, tmp_path, runner, channels=None):
    executable = tmp_path / "yt-dlp.exe"
    executable.touch()
    settings = replace(settings, ytdlp_path=str(executable))
    state = StateStore(settings.state_db)
    notifier = Mock()
    notifier.send_video.return_value = True
    return ChannelPoller(settings, state, notifier, channels, runner), state, notifier


def test_channel_videos_url_and_result_parsing():
    channel = Channel(CHANNEL_ID, "Test")
    assert channel_videos_url(CHANNEL_ID) == f"https://www.youtube.com/channel/{CHANNEL_ID}/videos"
    events = parse_ytdlp_result(payload((VIDEO_ID, "Video", {"timestamp": 1_700_000_000})), channel)
    assert [(event.video_id, event.source) for event in events] == [(VIDEO_ID, "poll")]
    assert events[0].published.endswith("+00:00")


def test_probe_builds_lightweight_command(tmp_path):
    seen = {}

    def runner(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return completed(payload((VIDEO_ID, "Video", {})))

    probe_channel(tmp_path / "yt-dlp.exe", Channel(CHANNEL_ID, "Test"), runner)
    assert seen["command"][-1] == channel_videos_url(CHANNEL_ID)
    assert seen["command"][1:6] == ["--flat-playlist", "--playlist-end", "3", "--dump-single-json", "--no-warnings"]
    assert seen["kwargs"]["timeout"] == 20


def test_first_observation_baselines_without_notification(settings, tmp_path):
    poller, state, notifier = configured(
        settings, tmp_path, lambda *_args, **_kwargs: completed(payload((VIDEO_ID, "Old", {})))
    )
    results = poller.poll_channel(poller.channels[0])
    assert results[0][1] == "BASELINE"
    assert state.get_video(VIDEO_ID)["baseline"] == 1
    assert state.get_video(VIDEO_ID)["detection_source"] == "poll"
    notifier.send_video.assert_not_called()


def test_new_and_multiple_videos_after_baseline_notify_in_order(settings, tmp_path):
    outputs = iter([
        payload((VIDEO_ID, "Old", {})),
        payload((NEWEST_VIDEO, "Newest", {}), (NEW_VIDEO, "New", {}), (VIDEO_ID, "Old", {})),
    ])
    poller, state, notifier = configured(settings, tmp_path, lambda *_args, **_kwargs: completed(next(outputs)))
    channel = poller.channels[0]
    poller.poll_channel(channel)
    results = poller.poll_channel(channel)
    assert [classification for _, classification in results] == ["DUPLICATE", "NEW", "NEW"]
    assert [call.args[0].video_id for call in notifier.send_video.call_args_list] == [NEW_VIDEO, NEWEST_VIDEO]
    assert state.get_video(NEW_VIDEO)["detection_source"] == "poll"


def test_duplicate_poll_does_not_notify_twice(settings, tmp_path):
    outputs = iter([payload((VIDEO_ID, "Old", {})), payload((NEW_VIDEO, "New", {}), (VIDEO_ID, "Old", {}))])
    poller, state, notifier = configured(settings, tmp_path, lambda *_args, **_kwargs: completed(next(outputs)))
    poller.poll_channel(poller.channels[0])
    poller.poll_channel(poller.channels[0])
    poller.runner = lambda *_args, **_kwargs: completed(payload((NEW_VIDEO, "New", {}), (VIDEO_ID, "Old", {})))
    poller.poll_channel(poller.channels[0])
    notifier.send_video.assert_called_once()


def test_restart_preserves_baseline_and_does_not_resend(settings, tmp_path):
    runner = lambda *_args, **_kwargs: completed(payload((VIDEO_ID, "Old", {})))
    poller, state, notifier = configured(settings, tmp_path, runner)
    poller.poll_channel(poller.channels[0])
    reopened = StateStore(settings.state_db)
    restarted = ChannelPoller(poller.settings, reopened, notifier, poller.channels, runner)
    assert restarted.poll_channel(restarted.channels[0])[0][1] == "DUPLICATE"
    notifier.send_video.assert_not_called()


def test_missing_ytdlp_does_not_crash(settings):
    poller = ChannelPoller(settings, StateStore(settings.state_db), Mock())
    poller.executable = None
    assert asyncio.run(poller.poll_once()) == []
    stop = asyncio.Event()
    stop.set()
    asyncio.run(poller.run(stop))


def test_timeout_is_recorded(settings, tmp_path):
    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("yt-dlp", 20)

    poller, state, _ = configured(settings, tmp_path, timeout)
    asyncio.run(poller.poll_once())
    row = state.get_poll_state(CHANNEL_ID)
    assert row["consecutive_failures"] == 1
    assert row["last_error"] == "TimeoutExpired"


def test_one_failed_channel_does_not_block_other(settings, tmp_path):
    channels = [Channel(CHANNEL_ID, "Broken"), Channel(SECOND_CHANNEL, "Good")]

    def runner(command, **_kwargs):
        if CHANNEL_ID in command[-1]:
            raise RuntimeError("broken")
        return completed(payload((VIDEO_ID, "Old", {})))

    poller, state, _ = configured(settings, tmp_path, runner, channels)
    asyncio.run(poller.poll_once())
    assert state.get_poll_state(CHANNEL_ID)["consecutive_failures"] == 1
    assert state.get_poll_state(SECOND_CHANNEL)["last_success_at"] is not None


def test_backoff_increases_caps_and_success_resets(settings):
    state = StateStore(settings.state_db)
    delays = [state.record_poll_failure(CHANNEL_ID, "fail", 10) for _ in range(5)]
    assert delays == [10, 20, 30, 60, 60]
    assert state.record_poll_success(CHANNEL_ID, VIDEO_ID) is True
    assert state.get_poll_state(CHANNEL_ID)["consecutive_failures"] == 0


def test_polling_loop_shuts_down_cleanly(settings):
    poller = ChannelPoller(settings, StateStore(settings.state_db), Mock())
    poller.executable = None

    async def run():
        stop = asyncio.Event()
        task = asyncio.create_task(poller.run(stop))
        await asyncio.sleep(0)
        stop.set()
        await asyncio.wait_for(task, 1)

    asyncio.run(run())


def test_disabled_channels_are_not_polled(settings, tmp_path):
    settings.channels_file.write_text(
        f'[{ {"channel_id": CHANNEL_ID, "name": "Off", "enabled": False} }]'.replace("'", '"').replace("False", "false"),
        encoding="utf-8",
    )
    poller, _, _ = configured(settings, tmp_path, Mock())
    assert enabled_channels(settings.channels_file) == []
    assert poller.channels == []
