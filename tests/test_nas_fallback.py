from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.cleanup_worker import CleanupWorker
from app.config import Settings
from app.main import create_app
from app.models import VideoEvent
from app.nas_sync_worker import NasSyncWorker
from app.process_worker import ProcessHandoffWorker
from app.state import StateStore
from tests.conftest import CHANNEL_ID


NOW = datetime(2026, 8, 14, tzinfo=timezone.utc)


class Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class Bridge:
    def __init__(self, payload):
        self.payload, self.posts = payload, []

    def post(self, url, json):
        self.posts.append((url, json))
        return Response(self.payload)

    def get(self, _url):
        return Response(self.payload)


def test_default_fallback_is_fixed_to_f_drive(monkeypatch):
    monkeypatch.setenv("LOCAL_OUTPUT_FALLBACK_ROOT", "")
    monkeypatch.setenv("LOCAL_FALLBACK_MIN_FREE_GB", "20")
    settings = Settings.from_env()
    assert settings.local_output_fallback_root == Path(r"F:\ContentOpsFallback")
    assert settings.local_fallback_min_free_gb == 20


def downloaded_job(settings, tmp_path, video_id="fallback001", *, nas_ready=False, channel="Test Channel"):
    state = StateStore(settings.state_db)
    intended = tmp_path / "nas" / "Member" / channel
    if nas_ready:
        intended.mkdir(parents=True)
    event = VideoEvent(video_id, CHANNEL_ID, "Title", "", "", f"https://youtu.be/{video_id}")
    assert state.create_processing_job(event, channel, str(intended), "QUEUED", None, owner_id="member_1")
    job = state.processing_jobs()[0]
    source = tmp_path / f"{video_id}.mp4"
    source.write_bytes(b"source")
    state.update_download_job(
        job["id"], status="DOWNLOADED", download_state="DONE", downloaded_file_path=str(source),
    )
    return state, intended, state.processing_jobs()[0]


def pending_sync_job(settings, tmp_path, video_id="fallback001", channel="Test Channel"):
    state, intended, job = downloaded_job(settings, tmp_path, video_id, channel=channel)
    processing = settings.local_output_fallback_root / "member_1" / channel / str(job["id"])
    processing.mkdir(parents=True)
    files = []
    for index in range(1, 4):
        path = processing / f"Exact Title_PART_{index}.mp4"
        path.write_bytes(f"part-{index}".encode())
        files.append(path)
    state.set_processing_route(job["id"], str(processing), "PENDING")
    state.update_process_job(
        job["id"], status="COMPLETED", process_state="DONE", progress=100,
        processed_file_path=str(files[0]),
        processed_files_json=json.dumps([str(path) for path in files]),
    )
    return state, intended, processing, files


def test_nas_available_processes_directly(settings, tmp_path):
    state, intended, _ = downloaded_job(settings, tmp_path, nas_ready=True)
    bridge = Bridge({"external_id": "p1", "state": "QUEUED", "progress_percent": 0})
    ProcessHandoffWorker(settings, state, bridge).tick(NOW)
    job = state.processing_jobs()[0]
    assert bridge.posts[0][1]["output_dir"] == str(intended)
    assert job["processing_output_dir"] == str(intended)
    assert job["nas_sync_state"] == "NOT_REQUIRED"
    assert bridge.posts[0][1]["enhanced_content_selection"] is True


def test_nas_offline_processes_locally_and_stays_successful(settings, tmp_path):
    state, intended, job = downloaded_job(settings, tmp_path)
    processing = settings.local_output_fallback_root / "member_1" / "Test Channel" / str(job["id"])
    processing.mkdir(parents=True)
    files = [processing / f"Exact Title_PART_{index}.mp4" for index in range(1, 4)]
    for path in files:
        path.write_bytes(b"part")
    bridge = Bridge({"external_id": "p1", "state": "DONE", "processed_files": [str(path) for path in files]})
    ProcessHandoffWorker(settings, state, bridge).tick(NOW)
    saved = state.processing_jobs()[0]
    assert saved["status"] == "COMPLETED"
    assert saved["output_dir"] == saved["intended_output_dir"] == str(intended)
    assert saved["processing_output_dir"] == str(processing)
    assert saved["nas_sync_state"] == "PENDING"
    assert json.loads(saved["processed_files_json"]) == [str(path) for path in files]


def test_fallback_unavailable_and_low_disk_retry_without_failing(settings, tmp_path, monkeypatch):
    state, _, _ = downloaded_job(settings, tmp_path)
    monkeypatch.setattr("app.process_worker.prepare_fallback", lambda *_: (_ for _ in ()).throw(RuntimeError("LOCAL_FALLBACK_UNAVAILABLE")))
    ProcessHandoffWorker(settings, state, Bridge({})).tick(NOW)
    job = state.processing_jobs()[0]
    assert (job["status"], job["process_error"]) == ("PROCESS_PENDING", "LOCAL_FALLBACK_UNAVAILABLE")

    monkeypatch.undo()
    other_settings = replace(settings, state_db=tmp_path / "low.db", local_fallback_min_free_gb=10**9)
    low_root = tmp_path / "low"
    low_root.mkdir()
    other_state, _, _ = downloaded_job(other_settings, low_root, "fallback002")
    ProcessHandoffWorker(other_settings, other_state, Bridge({})).tick(NOW)
    low = other_state.processing_jobs()[0]
    assert (low["status"], low["process_error"]) == ("PROCESS_PENDING", "LOCAL_FALLBACK_DISK_LOW")


def test_nas_return_syncs_verifies_rewrites_paths_and_removes_local(settings, tmp_path):
    state, intended, processing, files = pending_sync_job(settings, tmp_path)
    intended.parent.mkdir(parents=True)
    NasSyncWorker(settings, state).tick(NOW)
    job = state.processing_jobs()[0]
    destinations = [intended / path.name for path in files]
    assert job["nas_sync_state"] == "DONE" and job["nas_synced_at"] == NOW.isoformat()
    assert json.loads(job["processed_files_json"]) == [str(path) for path in destinations]
    assert job["processed_file_path"] == str(destinations[0])
    assert job["fallback_cleanup_at"] == NOW.isoformat()
    assert all(path.is_file() and path.stat().st_size > 0 for path in destinations)
    assert not processing.exists()


def test_sync_failure_retains_local_and_uses_bounded_backoff(settings, tmp_path, monkeypatch):
    state, intended, processing, files = pending_sync_job(settings, tmp_path)
    intended.parent.mkdir(parents=True)
    monkeypatch.setattr("app.nas_sync_worker.shutil.copyfile", lambda *_: (_ for _ in ()).throw(OSError("copy failed")))
    NasSyncWorker(settings, state).tick(NOW)
    job = state.processing_jobs()[0]
    assert job["nas_sync_state"] == "FAILED_RETRY" and job["nas_sync_attempts"] == 1
    assert job["nas_sync_next_attempt_at"] == (NOW + timedelta(seconds=30)).isoformat()
    assert processing.is_dir() and all(path.is_file() for path in files)


def test_restart_resumes_pending_to_original_snapshot(settings, tmp_path):
    state, intended, _, files = pending_sync_job(settings, tmp_path)
    original = str(intended)
    intended.parent.mkdir(parents=True)
    state.update_nas_sync(state.processing_jobs()[0]["id"], "SYNCING")
    restarted = StateStore(settings.state_db)
    NasSyncWorker(settings, restarted).tick(NOW)
    job = restarted.processing_jobs()[0]
    assert job["intended_output_dir"] == original
    assert json.loads(job["processed_files_json"]) == [str(intended / path.name) for path in files]


def test_identical_destination_is_idempotent(settings, tmp_path):
    state, intended, processing, files = pending_sync_job(settings, tmp_path)
    intended.mkdir(parents=True)
    for source in files:
        (intended / source.name).write_bytes(source.read_bytes())
    NasSyncWorker(settings, state).tick(NOW)
    assert state.processing_jobs()[0]["nas_sync_state"] == "DONE"
    assert not processing.exists()


def test_different_destination_sets_conflict_and_preserves_local(settings, tmp_path):
    state, intended, processing, files = pending_sync_job(settings, tmp_path)
    intended.mkdir(parents=True)
    (intended / files[0].name).write_bytes(b"different-size")
    NasSyncWorker(settings, state).tick(NOW)
    job = state.processing_jobs()[0]
    assert job["nas_sync_state"] == "CONFLICT"
    assert job["nas_sync_error"].startswith("DESTINATION_CONFLICT:")
    assert processing.is_dir() and all(path.is_file() for path in files)


def test_cleanup_waits_for_sync_and_manual_retry_is_available(settings, tmp_path):
    state, _, processing, _ = pending_sync_job(settings, tmp_path)
    assert state.cleanup_job_due(NOW.isoformat()) is None
    client = TestClient(create_app(settings, state=state))
    job_id = state.processing_jobs()[0]["id"]
    assert client.post(f"/api/jobs/{job_id}/retry-nas-sync").status_code == 200
    assert processing.is_dir()
    state.update_nas_sync(job_id, "DONE")
    assert state.cleanup_job_due(NOW.isoformat())["id"] == job_id


def test_multiple_pending_jobs_are_isolated_and_synced_one_at_a_time(settings, tmp_path):
    state, first_intended, first_processing, _ = pending_sync_job(settings, tmp_path, "fallback001", "One")
    first_intended.parent.mkdir(parents=True)
    event = VideoEvent("fallback002", CHANNEL_ID, "Title", "", "", "https://youtu.be/fallback002")
    second_intended = tmp_path / "nas" / "Member" / "Two"
    state.create_processing_job(event, "Two", str(second_intended), "QUEUED", None, owner_id="member_1")
    second = next(job for job in state.processing_jobs() if job["video_id"] == "fallback002")
    second_processing = settings.local_output_fallback_root / "member_1" / "Two" / str(second["id"])
    second_processing.mkdir(parents=True)
    second_file = second_processing / "Second_PART_1.mp4"
    second_file.write_bytes(b"second")
    state.set_processing_route(second["id"], str(second_processing), "PENDING")
    state.update_process_job(
        second["id"], status="COMPLETED", process_state="DONE",
        processed_file_path=str(second_file), processed_files_json=json.dumps([str(second_file)]),
    )
    NasSyncWorker(settings, state).tick(NOW)
    jobs = {job["video_id"]: job for job in state.processing_jobs()}
    assert jobs["fallback001"]["nas_sync_state"] == "DONE"
    assert jobs["fallback002"]["nas_sync_state"] == "PENDING"
    assert not first_processing.exists() and second_processing.is_dir()


def test_cleanup_worker_never_runs_while_sync_pending(settings, tmp_path):
    state, _, processing, _ = pending_sync_job(settings, tmp_path)
    worker = CleanupWorker(settings, state, probe=lambda _path: None)
    worker.tick(NOW)
    assert processing.is_dir()
    assert state.processing_jobs()[0]["cleanup_state"] is None


def test_restart_finishes_fallback_cleanup_after_sync_commit(settings, tmp_path):
    state, _, processing, _ = pending_sync_job(settings, tmp_path)
    job_id = state.processing_jobs()[0]["id"]
    state.update_nas_sync(job_id, "DONE", synced_at=NOW.isoformat())
    NasSyncWorker(settings, StateStore(settings.state_db)).tick(NOW + timedelta(seconds=1))
    saved = state.processing_jobs()[0]
    assert not processing.exists()
    assert saved["fallback_cleanup_at"] == (NOW + timedelta(seconds=1)).isoformat()
