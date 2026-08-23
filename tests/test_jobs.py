from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.config import Channel, load_team_members
from app.detector import handle_detected_video
from app.jobs import sanitize_folder_name
from app.main import create_app
from app.models import VideoEvent
from app.poller import ChannelPoller
from app.state import StateStore
from tests.conftest import CHANNEL_ID, VIDEO_ID

NEW_VIDEO = "abcdefghijk"


def event(video_id: str = VIDEO_ID) -> VideoEvent:
    return VideoEvent(video_id, CHANNEL_ID, "Video title", "2026-08-12T00:00:00Z", "", f"https://youtu.be/{video_id}")


def notifier():
    value = Mock()
    value.send_video.return_value = True
    return value


def owned(settings, root):
    members = load_team_members(settings.team_members_file)
    member_root = root / members[0].nas_folder
    member_root.mkdir(parents=True)
    return members, member_root


def test_new_video_creates_one_queued_job_and_reuses_folder(settings, tmp_path):
    root = tmp_path / "nas"
    members, member_root = owned(settings, root)
    folder = member_root / "Test Channel"
    folder.mkdir()
    state, telegram = StateStore(settings.state_db), notifier()

    assert handle_detected_video(event(), state, telegram, {CHANNEL_ID: "Test Channel"}, nas_output_root=root, owner_id=members[0].id, team_members=members) == "NEW"
    assert handle_detected_video(event(), state, telegram, {CHANNEL_ID: "Test Channel"}, nas_output_root=root, owner_id=members[0].id, team_members=members) == "DUPLICATE"

    jobs = state.processing_jobs()
    assert len(jobs) == 1
    assert (jobs[0]["status"], jobs[0]["channel_name"], jobs[0]["output_dir"], jobs[0]["error"]) == (
        "QUEUED", "Test Channel", str(folder), None,
    )
    telegram.send_video.assert_called_once()


def test_baseline_creates_no_job(settings, tmp_path):
    state = StateStore(settings.state_db)
    assert handle_detected_video(
        event(), state, notifier(), {CHANNEL_ID: "Test Channel"},
        baseline=True, nas_output_root=tmp_path,
    ) == "BASELINE"
    assert state.processing_jobs() == []


def test_remote_processing_does_not_require_local_nas(settings):
    state, telegram = StateStore(settings.state_db), notifier()
    assert handle_detected_video(
        event("remote-video"), state, telegram, {CHANNEL_ID: "Test Channel"},
        nas_output_root=None, remote_processing=True,
    ) == "NEW"
    job = state.processing_jobs()[0]
    assert (job["status"], job["error"]) == ("QUEUED", None)


def test_missing_channel_folder_is_created_with_safe_name(settings, tmp_path):
    state = StateStore(settings.state_db)
    members, member_root = owned(settings, tmp_path)
    handle_detected_video(
        event(), state, notifier(), {CHANNEL_ID: "Test: Channel?"}, nas_output_root=tmp_path,
        owner_id=members[0].id, team_members=members,
    )
    job = state.processing_jobs()[0]
    assert job["channel_name"] == "Test: Channel?"
    assert job["output_dir"] == str(member_root / "Test Channel")
    assert (member_root / "Test Channel").is_dir()


def test_nas_unavailable_queues_job_and_telegram_still_sends(settings, tmp_path):
    state, telegram = StateStore(settings.state_db), notifier()
    missing = tmp_path / "missing"
    members = load_team_members(settings.team_members_file)
    assert handle_detected_video(
        event(), state, telegram, {CHANNEL_ID: "Test"}, nas_output_root=missing,
        owner_id=members[0].id, team_members=members,
    ) == "NEW"
    job = state.processing_jobs()[0]
    assert (job["status"], job["error"]) == ("QUEUED", None)
    assert job["intended_output_dir"] == str(missing / members[0].nas_folder / "Test")
    telegram.send_video.assert_called_once()


def test_nas_unavailable_does_not_crash_poller(settings, tmp_path):
    executable = tmp_path / "yt-dlp.exe"
    executable.touch()
    settings = replace(settings, ytdlp_path=str(executable), nas_output_root=tmp_path / "missing")
    state, telegram = StateStore(settings.state_db), notifier()
    poller = ChannelPoller(settings, state, telegram, runner=Mock())
    state.record_poll_success(CHANNEL_ID, VIDEO_ID)
    poller.runner.return_value.stdout = '{"entries":[{"id":"abcdefghijk","title":"New"}]}'
    poller.runner.return_value.returncode = 0

    results = poller.poll_channel(Channel(CHANNEL_ID, "Test"))

    assert results[0][1] == "NEW"
    assert (state.processing_jobs()[0]["status"], state.processing_jobs()[0]["error"]) == ("QUEUED", None)
    telegram.send_video.assert_called_once()


def test_jobs_api_returns_newest_first(settings):
    state = StateStore(settings.state_db)
    state.create_processing_job(event(VIDEO_ID), "First", "A", "QUEUED", None)
    state.create_processing_job(event(NEW_VIDEO), "Second", "B", "FAILED", "NAS_UNAVAILABLE")
    response = TestClient(create_app(settings, state=state)).get("/api/jobs")
    assert response.status_code == 200
    assert [job["video_id"] for job in response.json()] == [NEW_VIDEO, VIDEO_ID]


def test_disabled_channel_has_no_job(settings, tmp_path):
    settings.channels_file.write_text(
        f'[{ {"channel_id": CHANNEL_ID, "name": "Off", "enabled": False} }]'.replace("'", '"').replace("False", "false"),
        encoding="utf-8",
    )
    executable = tmp_path / "yt-dlp.exe"
    executable.touch()
    settings = replace(settings, ytdlp_path=str(executable), nas_output_root=tmp_path)
    state, runner = StateStore(settings.state_db), Mock()
    poller = ChannelPoller(settings, state, notifier(), runner=runner)
    assert asyncio.run(poller.poll_once()) == []
    assert state.processing_jobs() == []
    runner.assert_not_called()


def test_windows_folder_sanitization():
    assert sanitize_folder_name('US Politics: Daily?') == "US Politics Daily"
    assert sanitize_folder_name("name. ") == "name"
    assert sanitize_folder_name('<>:"/\\|?*') == "Channel"
    assert sanitize_folder_name("CON") == "CON_"
