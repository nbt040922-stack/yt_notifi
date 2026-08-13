from __future__ import annotations

import sqlite3
import subprocess
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import httpx
import pytest

from app.models import VideoEvent
from app.poller import ChannelPoller
from app.process_worker import ProcessHandoffWorker
from app.state import StateStore
from tests.conftest import CHANNEL_ID, VIDEO_ID


class Response:
    def __init__(self, payload, status_code=200):
        self.payload, self.status_code = payload, status_code

    def json(self):
        return self.payload


class Bridge:
    def __init__(self, responses):
        self.responses, self.posts, self.gets = iter(responses), [], []

    def post(self, url, json):
        assert json["enhanced_content_selection"] is True
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


def downloaded_job(settings, tmp_path):
    state = StateStore(settings.state_db)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    event = VideoEvent(VIDEO_ID, CHANNEL_ID, "Video", "", "", f"https://www.youtube.com/watch?v={VIDEO_ID}")
    assert state.create_processing_job(
        event, "JOIN Name", str(tmp_path / "Member_2" / "JOIN Name"), "QUEUED", None,
        owner_id="member_2",
    )
    job = state.processing_jobs()[0]
    state.update_download_job(
        job["id"], status="DOWNLOADED", external_id="download-1",
        download_state="DONE", progress=100, downloaded_file_path=str(source),
    )
    return state, source


def payload(state="QUEUED", **extra):
    return {"external_id": "contentops-process-1", "state": state, "progress_percent": 0, **extra}


def test_downloaded_job_submits_once_and_tracks_existing(settings, tmp_path):
    state, source = downloaded_job(settings, tmp_path)
    bridge = Bridge([Response(payload()), Response(payload("PROCESSING", progress_percent=42))])
    worker = ProcessHandoffWorker(settings, state, bridge)
    worker.tick()
    worker.tick()
    job = state.processing_jobs()[0]
    assert len(bridge.posts) == 1 and len(bridge.gets) == 1
    assert job["process_external_id"] == "contentops-process-1"
    assert bridge.posts[0][1]["handoff_id"] == str(job["id"])
    assert bridge.posts[0][1]["source_file"] == str(source)
    assert bridge.posts[0][1]["output_dir"] == job["output_dir"]
    assert job["owner_id"] == "member_2"
    assert bridge.posts[0][1]["enhanced_content_selection"] is True
    assert (job["status"], job["process_progress"]) == ("PROCESSING", 42)


def test_bridge_offline_restart_retries_same_enhanced_handoff(settings, tmp_path):
    state, _ = downloaded_job(settings, tmp_path)
    bridge = Bridge([httpx.ConnectError("offline"), Response(payload("PROCESSING"))])
    worker = ProcessHandoffWorker(settings, state, bridge)
    now = datetime(2026, 8, 12, tzinfo=timezone.utc)
    worker.tick(now)
    assert state.processing_jobs()[0]["status"] == "PROCESS_PENDING"
    ProcessHandoffWorker(settings, state, bridge).tick(now + timedelta(seconds=5))
    assert state.processing_jobs()[0]["status"] == "PROCESSING"
    assert len(bridge.posts) == 2
    assert bridge.posts[0][1]["handoff_id"] == bridge.posts[1][1]["handoff_id"]
    assert bridge.posts[0][1]["output_dir"] == bridge.posts[1][1]["output_dir"]
    assert all(post[1]["enhanced_content_selection"] is True for post in bridge.posts)


@pytest.mark.parametrize("remote,local", [("QUEUED", "PROCESS_PENDING"), ("PROCESSING", "PROCESSING"), ("FINALIZING", "PROCESSING")])
def test_active_state_mapping(settings, tmp_path, remote, local):
    state, _ = downloaded_job(settings, tmp_path)
    ProcessHandoffWorker(settings, state, Bridge([Response(payload(remote, progress_percent=62))])).tick()
    job = state.processing_jobs()[0]
    assert (job["status"], job["process_state"], job["process_progress"]) == (local, remote, 62)


def test_done_persists_exact_part_paths(settings, tmp_path):
    state, _ = downloaded_job(settings, tmp_path)
    exact = [tmp_path / "nas" / f"My Better Title_PART_{index}.mp4" for index in range(1, 4)]
    exact[0].parent.mkdir()
    for path in exact:
        path.write_bytes(b"part")
    ProcessHandoffWorker(settings, state, Bridge([Response(payload(
        "DONE", processed_files=[str(path) for path in exact],
        processed_file_path=str(exact[0]),
    ))])).tick()
    job = state.processing_jobs()[0]
    assert (job["status"], job["processed_file_path"]) == ("COMPLETED", str(exact[0]))
    assert json.loads(job["processed_files_json"]) == [str(path) for path in exact]


def test_done_requires_every_returned_part_to_exist(settings, tmp_path):
    state, _ = downloaded_job(settings, tmp_path)
    missing = str(tmp_path / "nas" / "PART_1.mp4")
    ProcessHandoffWorker(settings, state, Bridge([Response(payload("DONE", processed_files=[missing]))])).tick()
    job = state.processing_jobs()[0]
    assert (job["status"], job["process_error"]) == ("FAILED", "MISSING_PROCESSED_FILES")


def test_terminal_failure_and_missing_source_response(settings, tmp_path):
    state, _ = downloaded_job(settings, tmp_path)
    ProcessHandoffWorker(settings, state, Bridge([Response({"error": "SOURCE_FILE_MISSING"}, 422)])).tick()
    job = state.processing_jobs()[0]
    assert (job["status"], job["process_error"]) == ("FAILED", "SOURCE_FILE_MISSING")


def test_restart_uses_get_not_post(settings, tmp_path):
    state, _ = downloaded_job(settings, tmp_path)
    job = state.processing_jobs()[0]
    state.update_process_job(job["id"], status="PROCESSING", external_id="contentops-process-1", process_state="PROCESSING")
    bridge = Bridge([Response(payload("FINALIZING", progress_percent=95))])
    ProcessHandoffWorker(settings, state, bridge).tick()
    assert bridge.posts == [] and len(bridge.gets) == 1


def test_old_processing_rows_migrate_without_loss(tmp_path):
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as db:
        db.execute("""CREATE TABLE processing_jobs (
            id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL,
            video_id TEXT NOT NULL UNIQUE, video_url TEXT NOT NULL, video_title TEXT NOT NULL,
            source_channel_id TEXT NOT NULL, channel_name TEXT NOT NULL,
            output_dir TEXT NOT NULL, error TEXT)""")
        db.execute("INSERT INTO processing_jobs VALUES (1,'2026-01-01','DOWNLOADED',?,?,?,?,?,?,?)",
                   (VIDEO_ID, "https://youtu.be/x", "Old", CHANNEL_ID, "JOIN", "D:\\NAS", None))
    job = StateStore(database).processing_jobs()[0]
    assert (job["id"], job["status"], job["process_external_id"]) == (1, "DOWNLOADED", None)


def test_processor_offline_does_not_block_polling_or_telegram(settings, tmp_path):
    executable = tmp_path / "yt-dlp.exe"
    executable.touch()
    nas = tmp_path / "nas"
    nas.mkdir()
    settings = replace(settings, ytdlp_path=str(executable), nas_output_root=nas)
    state, telegram = StateStore(settings.state_db), Mock()
    telegram.send_video.return_value = True
    state.record_poll_success(CHANNEL_ID, VIDEO_ID)
    output = '{"entries":[{"id":"abcdefghijk","title":"New"}]}'
    poller = ChannelPoller(settings, state, telegram, runner=lambda *a, **k: subprocess.CompletedProcess([], 0, stdout=output, stderr=""))
    poller.poll_channel(poller.channels[0])
    job = state.processing_jobs()[0]
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    state.update_download_job(job["id"], status="DOWNLOADED", download_state="DONE", downloaded_file_path=str(source))
    ProcessHandoffWorker(settings, state, Bridge([httpx.ConnectError("offline")])).tick()
    poller.poll_channel(poller.channels[0])
    assert state.processing_jobs()[0]["status"] == "PROCESS_PENDING"
    telegram.send_video.assert_called_once()
