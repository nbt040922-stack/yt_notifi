from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.download_worker import DownloadHandoffWorker
from app.main import create_app
from app.models import VideoEvent
from app.process_worker import ProcessHandoffWorker
from app.state import StateStore
from tests.conftest import CHANNEL_ID, VIDEO_ID


class Response:
    def __init__(self, payload, status_code=200):
        self.payload, self.status_code = payload, status_code

    def json(self):
        return self.payload


class Bridge:
    def __init__(self, responses=()):
        self.responses = iter(responses)
        self.posts, self.gets = [], []

    def post(self, url, json):
        self.posts.append((url, json))
        return next(self.responses)

    def get(self, url):
        self.gets.append(url)
        return next(self.responses)


class Control:
    def __init__(self, ready):
        self.ready = ready

    def is_ready(self):
        return self.ready

    def pause_reason(self):
        return "SILENCE_ENGINE_DISABLED"


def make_job(settings, tmp_path, *, status="QUEUED", video_id=VIDEO_ID):
    state = StateStore(settings.state_db)
    output = tmp_path / "nas" / "Member Two" / "Original Channel"
    output.mkdir(parents=True)
    event = VideoEvent(
        video_id, CHANNEL_ID, "Original title", "", "",
        f"https://www.youtube.com/watch?v={video_id}",
    )
    assert state.create_processing_job(
        event, "Original Channel", str(output), status, None, owner_id="member_2",
    )
    return state, dict(state.processing_jobs()[0]), output


def cancel(client, job_id):
    return client.post(f"/api/jobs/{job_id}/cancel")


def retry(client, job_id):
    return client.post(f"/api/jobs/{job_id}/retry")


def test_cancel_queued_is_immediate_and_idempotent(settings, tmp_path):
    state, job, _ = make_job(settings, tmp_path)
    client = TestClient(create_app(settings, state=state))
    assert cancel(client, job["id"]).status_code == 200
    assert cancel(client, job["id"]).status_code == 200
    saved = state.processing_job(job["id"])
    assert saved["status"] == "CANCELLED"
    assert saved["cancel_requested"] == 1
    assert saved["cancelled_at"] and saved["cancel_reason"] == "USER_REQUEST"
    assert state.download_jobs_due("9999-12-31T00:00:00+00:00") == []


@pytest.mark.parametrize("status", ["DOWNLOAD_PENDING", "DOWNLOADED", "PROCESS_PENDING"])
def test_cancel_pending_job_stops_future_work(settings, tmp_path, status):
    state, job, _ = make_job(settings, tmp_path, status=status)
    response = cancel(TestClient(create_app(settings, state=state)), job["id"])
    assert response.status_code == 200
    assert state.processing_job(job["id"])["status"] == "CANCELLED"
    assert state.download_jobs_due("9999-12-31T00:00:00+00:00") == []
    assert state.process_jobs_due("9999-12-31T00:00:00+00:00") == []


@pytest.mark.parametrize("stage", ["download", "process"])
def test_active_cancel_wins_worker_completion_race(settings, tmp_path, stage):
    state, job, _ = make_job(settings, tmp_path)
    if stage == "download":
        state.update_download_job(
            job["id"], status="DOWNLOADING", external_id="contentops-1",
            download_state="DOWNLOADING",
        )
    else:
        source = tmp_path / "source.mp4"
        source.write_bytes(b"video")
        state.update_download_job(
            job["id"], status="DOWNLOADED", download_state="DONE",
            downloaded_file_path=str(source),
        )
        state.update_process_job(
            job["id"], status="PROCESSING", external_id="contentops-process-1",
            process_state="PROCESSING",
        )
    client = TestClient(create_app(settings, state=state))
    assert cancel(client, job["id"]).status_code == 200
    state.update_download_job(
        job["id"], status="DOWNLOADED", download_state="DONE",
        downloaded_file_path=str(tmp_path / "late.mp4"),
    )
    state.update_process_job(
        job["id"], status="COMPLETED", process_state="DONE",
        processed_file_path=str(tmp_path / "late-part.mp4"),
    )
    saved = state.processing_job(job["id"])
    assert saved["status"] == "CANCELLED" and saved["cancel_requested"] == 1
    assert saved["processed_file_path"] is None


def test_cancel_survives_restart_and_does_not_resume(settings, tmp_path):
    settings = replace(settings, processing_work_root=tmp_path / "work")
    state, job, _ = make_job(settings, tmp_path)
    assert cancel(TestClient(create_app(settings, state=state)), job["id"]).status_code == 200
    restarted = StateStore(settings.state_db)
    bridge = Bridge()
    DownloadHandoffWorker(settings, restarted, bridge).tick()
    ProcessHandoffWorker(settings, restarted, bridge).tick()
    assert restarted.processing_job(job["id"])["status"] == "CANCELLED"
    assert bridge.posts == [] and bridge.gets == []


def test_retry_failed_download_preserves_snapshot_and_notification(settings, tmp_path):
    state, job, output = make_job(settings, tmp_path)
    state.update_download_job(
        job["id"], status="FAILED", download_state="FAILED",
        download_error="network", attempts=3,
    )
    event = VideoEvent(VIDEO_ID, CHANNEL_ID, "Original title", "", "", job["video_url"])
    assert state.record_event(event)
    state.record_notification_attempt(VIDEO_ID, True, None)
    response = retry(TestClient(create_app(settings, state=state)), job["id"])
    assert response.status_code == 200
    saved = state.processing_job(job["id"])
    assert saved["status"] == "QUEUED" and saved["manual_retry_count"] == 1
    assert saved["download_external_id"] is None and saved["download_attempts"] == 3
    assert (saved["video_id"], saved["source_channel_id"], saved["channel_name"]) == (
        VIDEO_ID, CHANNEL_ID, "Original Channel",
    )
    assert saved["owner_id"] == "member_2" and saved["intended_output_dir"] == str(output)
    assert state.get_video(VIDEO_ID)["notification_attempts"] == 1


def test_retry_failed_processing_reuses_source_and_new_stable_handoff(settings, tmp_path):
    state, job, _ = make_job(settings, tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    state.update_download_job(
        job["id"], status="DOWNLOADED", external_id="contentops-1",
        download_state="DONE", progress=100, downloaded_file_path=str(source), attempts=2,
    )
    state.update_process_job(
        job["id"], status="FAILED", external_id="contentops-process-1",
        process_state="FAILED", process_error="render failed", attempts=4,
    )
    client = TestClient(create_app(settings, state=state))
    assert retry(client, job["id"]).status_code == 200
    saved = state.processing_job(job["id"])
    assert saved["status"] == "PROCESS_PENDING" and saved["downloaded_file_path"] == str(source)
    assert saved["download_external_id"] == "contentops-1" and saved["process_external_id"] is None
    assert saved["download_attempts"] == 2 and saved["process_attempts"] == 4
    bridge = Bridge([Response({"external_id": "contentops-process-1-retry-1", "state": "QUEUED"})])
    ProcessHandoffWorker(settings, state, bridge, Control(True)).tick()
    assert bridge.posts[0][1]["handoff_id"] == f"{job['id']}-retry-1"
    assert bridge.posts[0][1]["enhanced_content_selection"] is True


@pytest.mark.parametrize("after_download,expected", [(False, "QUEUED"), (True, "PROCESS_PENDING")])
def test_retry_cancelled_resumes_earliest_incomplete_stage(settings, tmp_path, after_download, expected):
    state, job, _ = make_job(settings, tmp_path)
    if after_download:
        source = tmp_path / "source.mp4"
        source.write_bytes(b"video")
        state.update_download_job(
            job["id"], status="DOWNLOADED", download_state="DONE",
            downloaded_file_path=str(source),
        )
    client = TestClient(create_app(settings, state=state))
    assert cancel(client, job["id"]).status_code == 200
    assert retry(client, job["id"]).status_code == 200
    assert state.processing_job(job["id"])["status"] == expected


def test_duplicate_retry_does_not_create_second_attempt(settings, tmp_path):
    state, job, _ = make_job(settings, tmp_path, status="FAILED")
    client = TestClient(create_app(settings, state=state))
    assert retry(client, job["id"]).status_code == 200
    second = retry(client, job["id"])
    assert second.status_code == 409 and second.json()["error"] == "JOB_ALREADY_RUNNING"
    assert state.processing_job(job["id"])["manual_retry_count"] == 1


def test_retry_processing_while_engine_off_waits_without_post(settings, tmp_path):
    state, job, _ = make_job(settings, tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    state.update_download_job(
        job["id"], status="DOWNLOADED", download_state="DONE",
        downloaded_file_path=str(source),
    )
    state.update_process_job(job["id"], status="FAILED", process_state="FAILED")
    assert retry(TestClient(create_app(settings, state=state)), job["id"]).status_code == 200
    bridge = Bridge()
    ProcessHandoffWorker(settings, state, bridge, Control(False)).tick()
    saved = state.processing_job(job["id"])
    assert saved["status"] == "PROCESS_PENDING"
    assert saved["process_error"] == "SILENCE_ENGINE_DISABLED" and bridge.posts == []


def test_retry_preserves_existing_fallback_route(settings, tmp_path):
    state, job, _ = make_job(settings, tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    fallback = settings.local_output_fallback_root / "member_2" / "Original Channel" / str(job["id"])
    fallback.mkdir(parents=True)
    state.update_download_job(
        job["id"], status="DOWNLOADED", download_state="DONE",
        downloaded_file_path=str(source),
    )
    state.set_processing_route(job["id"], str(fallback), "PENDING")
    state.update_process_job(job["id"], status="FAILED", process_state="FAILED")
    assert retry(TestClient(create_app(settings, state=state)), job["id"]).status_code == 200
    bridge = Bridge([Response({"external_id": "contentops-process-1-retry-1", "state": "QUEUED"})])
    ProcessHandoffWorker(settings, state, bridge, Control(True)).tick()
    assert bridge.posts[0][1]["output_dir"] == str(fallback)
    assert state.processing_job(job["id"])["nas_sync_state"] == "PENDING"


def test_retry_nas_failure_does_not_rerender(settings, tmp_path):
    state, job, output = make_job(settings, tmp_path)
    local = tmp_path / "fallback" / "PART_1.mp4"
    local.parent.mkdir()
    local.write_bytes(b"part")
    state.set_processing_route(job["id"], str(local.parent), "PENDING")
    state.update_process_job(
        job["id"], status="COMPLETED", process_state="DONE",
        processed_file_path=str(local), processed_files_json=json.dumps([str(local)]),
    )
    state.update_nas_sync(job["id"], "FAILED_RETRY", attempts=2, error="NAS_UNAVAILABLE")
    response = retry(TestClient(create_app(settings, state=state)), job["id"])
    saved = state.processing_job(job["id"])
    assert response.status_code == 200 and saved["nas_sync_state"] == "PENDING"
    duplicate = retry(TestClient(create_app(settings, state=state)), job["id"])
    assert duplicate.status_code == 409 and duplicate.json()["error"] == "JOB_ALREADY_RUNNING"
    assert saved["status"] == "COMPLETED" and state.process_jobs_due("9999-12-31T00:00:00+00:00") == []
    assert saved["processed_file_path"] == str(local) and saved["intended_output_dir"] == str(output)


def test_completed_job_wins_race_and_is_never_cancelled_or_retried(settings, tmp_path):
    state, job, _ = make_job(settings, tmp_path)
    state.update_process_job(job["id"], status="COMPLETED", process_state="DONE")
    client = TestClient(create_app(settings, state=state))
    cancelled = cancel(client, job["id"])
    retried = retry(client, job["id"])
    assert cancelled.status_code == 409 and cancelled.json()["error"] == "JOB_NOT_CANCELLABLE"
    assert retried.status_code == 409 and retried.json()["error"] == "JOB_NOT_RETRYABLE"
    assert state.processing_job(job["id"])["status"] == "COMPLETED"


def test_job_control_not_found_errors_are_specific(settings):
    client = TestClient(create_app(settings, state=StateStore(settings.state_db)))
    assert cancel(client, 999).json()["error"] == "JOB_NOT_FOUND"
    assert retry(client, 999).json()["error"] == "JOB_NOT_FOUND"


def test_dashboard_exposes_safe_manual_controls():
    html = Path("app/dashboard.html").read_text(encoding="utf-8")
    assert "if (!confirm(" in html
    assert "/cancel" in html and "/retry" in html
    assert "['FAILED','CANCELLED']" in html
