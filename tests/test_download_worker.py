from __future__ import annotations

import sqlite3
import json
import subprocess
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.download_worker import DownloadHandoffWorker
from app.poller import ChannelPoller
from app.models import VideoEvent
from app.state import StateStore
from app.config import load_team_members
from tests.conftest import CHANNEL_ID, VIDEO_ID
from unittest.mock import Mock


class Response:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


class Bridge:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.posts = []
        self.gets = []

    def post(self, url, json):
        self.posts.append((url, json))
        return self._next()

    def get(self, url):
        self.gets.append(url)
        return self._next()

    def _next(self):
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value


def queued_job(settings, tmp_path):
    root = tmp_path / "work"
    settings = replace(settings, processing_work_root=root)
    state = StateStore(settings.state_db)
    event = VideoEvent(VIDEO_ID, CHANNEL_ID, "Video", "", "", f"https://www.youtube.com/watch?v={VIDEO_ID}")
    assert state.create_processing_job(event, "JOIN Name", str(tmp_path / "nas"), "QUEUED", None)
    return settings, state


def payload(state="QUEUED", **extra):
    return {"external_id": "contentops-1", "state": state, "progress_percent": 0, **extra}


def test_queued_job_submitted_once_and_handoff_persisted(settings, tmp_path):
    settings, state = queued_job(settings, tmp_path)
    bridge = Bridge([Response(payload()), Response(payload("METADATA"))])
    worker = DownloadHandoffWorker(settings, state, bridge)
    worker.tick()
    worker.tick()
    job = state.processing_jobs()[0]
    assert len(bridge.posts) == 1
    assert len(bridge.gets) == 1
    assert job["download_external_id"] == "contentops-1"
    assert bridge.posts[0][1]["handoff_id"] == str(job["id"])
    assert bridge.posts[0][1]["work_dir"] == str(tmp_path / "work" / str(job["id"]))
    assert bridge.posts[0][1]["final_output_dir"] == str(tmp_path / "nas")


@pytest.mark.skip(reason="legacy remote-download shortcut removed; manual API uses YTDOWNLOAD :8790")
def test_remote_processing_skips_local_download_handoff(settings, tmp_path):
    settings = replace(settings, processing_work_root=None)
    state = StateStore(settings.state_db)
    remote_video_id = "remote12345"
    event = VideoEvent(remote_video_id, CHANNEL_ID, "Video", "", "", f"https://www.youtube.com/watch?v={remote_video_id}")
    assert state.create_processing_job(event, "Remote", "", "QUEUED", None)
    worker = DownloadHandoffWorker(settings, state, remote_processing=True)
    worker.tick()
    job = state.processing_jobs()[0]
    assert job["status"] == "PROCESS_PENDING"
    assert job["download_state"] == "REMOTE"


def test_bridge_unavailable_retries_then_recovers(settings, tmp_path):
    settings, state = queued_job(settings, tmp_path)
    bridge = Bridge([httpx.ConnectError("offline"), Response(payload("DOWNLOADING", progress_percent=12))])
    worker = DownloadHandoffWorker(settings, state, bridge)
    now = datetime(2026, 8, 12, tzinfo=timezone.utc)
    worker.tick(now)
    pending = state.processing_jobs()[0]
    assert pending["status"] == "DOWNLOAD_PENDING"
    assert pending["download_error"] == "ConnectError"
    worker.tick(now + timedelta(seconds=5))
    recovered = state.processing_jobs()[0]
    assert recovered["status"] == "DOWNLOADING"
    assert recovered["download_error"] is None


@pytest.mark.parametrize(
    "remote,local",
    [
        ("QUEUED", "DOWNLOAD_PENDING"),
        ("METADATA", "DOWNLOAD_PENDING"),
        ("DOWNLOADING", "DOWNLOADING"),
        ("MERGING", "DOWNLOADING"),
        ("VERIFYING", "DOWNLOADING"),
    ],
)
def test_active_state_mapping(settings, tmp_path, remote, local):
    settings, state = queued_job(settings, tmp_path)
    DownloadHandoffWorker(settings, state, Bridge([Response(payload(remote, progress_percent=64))])).tick()
    job = state.processing_jobs()[0]
    assert (job["status"], job["download_state"], job["download_progress"]) == (local, remote, 64)


def test_done_saves_exact_downloaded_path(settings, tmp_path):
    settings, state = queued_job(settings, tmp_path)
    exact = str(tmp_path / "work" / "1" / "source.mp4")
    DownloadHandoffWorker(
        settings, state, Bridge([Response(payload("DONE", downloaded_file_path=exact))])
    ).tick()
    job = state.processing_jobs()[0]
    assert (job["status"], job["downloaded_file_path"]) == ("DOWNLOADED", exact)


def test_done_prefers_downloader_metadata_title(settings, tmp_path):
    settings, state = queued_job(settings, tmp_path)
    exact = str(tmp_path / "work" / "1" / "source.mp4")
    DownloadHandoffWorker(
        settings, state, Bridge([Response(payload(
            "DONE", downloaded_file_path=exact,
            title="【コストコ】日本語タイトル",
        ))])
    ).tick()
    job = state.processing_jobs()[0]
    assert job["video_title"] == "【コストコ】日本語タイトル"


@pytest.mark.parametrize("remote", ["FAILED", "CANCELLED"])
def test_terminal_bridge_state_fails_job(settings, tmp_path, remote):
    settings, state = queued_job(settings, tmp_path)
    DownloadHandoffWorker(
        settings, state, Bridge([Response(payload(remote, error="download stopped"))])
    ).tick()
    job = state.processing_jobs()[0]
    assert (job["status"], job["download_error"]) == ("FAILED", "download stopped")


def test_restart_tracks_existing_external_job_without_post(settings, tmp_path):
    settings, state = queued_job(settings, tmp_path)
    job = state.processing_jobs()[0]
    state.update_download_job(
        job["id"], status="DOWNLOADING", external_id="contentops-1", download_state="DOWNLOADING",
    )
    bridge = Bridge([Response(payload("VERIFYING", progress_percent=99))])
    DownloadHandoffWorker(settings, state, bridge).tick()
    assert bridge.posts == []
    assert len(bridge.gets) == 1
    assert state.processing_jobs()[0]["status"] == "DOWNLOADING"


def test_existing_processing_jobs_migrate_without_data_loss(tmp_path):
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as db:
        db.execute(
            """CREATE TABLE processing_jobs (
                id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL,
                video_id TEXT NOT NULL UNIQUE, video_url TEXT NOT NULL, video_title TEXT NOT NULL,
                source_channel_id TEXT NOT NULL, channel_name TEXT NOT NULL,
                output_dir TEXT NOT NULL, error TEXT)"""
        )
        db.execute(
            "INSERT INTO processing_jobs VALUES (1,'2026-01-01','QUEUED',?,?,?,?,?,?,?)",
            (VIDEO_ID, "https://youtu.be/x", "Old", CHANNEL_ID, "JOIN", "D:\\NAS", None),
        )
    state = StateStore(database)
    job = state.processing_jobs()[0]
    assert (job["id"], job["status"], job["download_external_id"]) == (1, "QUEUED", None)
    assert job["updated_at"] == "2026-01-01"


def test_bridge_offline_does_not_block_polling_or_telegram(settings, tmp_path):
    executable = tmp_path / "yt-dlp.exe"
    executable.touch()
    nas = tmp_path / "nas"
    nas.mkdir()
    (nas / load_team_members(settings.team_members_file)[0].nas_folder).mkdir()
    settings = replace(
        settings, ytdlp_path=str(executable), nas_output_root=nas,
        processing_work_root=tmp_path / "work",
    )
    state, telegram = StateStore(settings.state_db), Mock()
    telegram.send_video.return_value = True
    state.record_poll_success(CHANNEL_ID, VIDEO_ID)
    output = json.dumps({"entries": [{"id": "abcdefghijk", "title": "New"}]})
    poller = ChannelPoller(
        settings, state, telegram,
        runner=lambda *args, **kwargs: subprocess.CompletedProcess([], 0, stdout=output, stderr=""),
    )
    poller.poll_channel(poller.channels[0])
    DownloadHandoffWorker(settings, state, Bridge([httpx.ConnectError("offline")])).tick()
    poller.poll_channel(poller.channels[0])
    assert state.processing_jobs()[0]["status"] == "DOWNLOAD_PENDING"
    telegram.send_video.assert_called_once()
