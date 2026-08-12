from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.cleanup_worker import CleanupWorker, validate_workspace
from app.models import VideoEvent
from app.state import StateStore
from tests.conftest import CHANNEL_ID, VIDEO_ID


NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)


def completed_job(settings, tmp_path, *, dry_run=False, outputs=2):
    work_root, nas_root = tmp_path / "work", tmp_path / "nas"
    output_dir = nas_root / "Channel"
    work_root.mkdir()
    output_dir.mkdir(parents=True)
    settings = replace(
        settings, processing_work_root=work_root, nas_output_root=nas_root,
        contentops_cleanup_dry_run=dry_run,
    )
    state = StateStore(settings.state_db)
    event = VideoEvent(VIDEO_ID, CHANNEL_ID, "Video", "", "", "https://youtu.be/x")
    state.create_processing_job(event, "Channel", str(output_dir), "QUEUED", None)
    job = state.processing_jobs()[0]
    workspace = work_root / str(job["id"])
    workspace.mkdir()
    source = workspace / "source.mp4"
    source.write_bytes(b"source bytes")
    parts = []
    for index in range(outputs):
        part = output_dir / f"PART_{index + 1}.mp4"
        part.write_bytes(b"nas part")
        parts.append(str(part))
    state.update_download_job(
        job["id"], status="DOWNLOADED", download_state="DONE",
        downloaded_file_path=str(source),
    )
    state.update_process_job(
        job["id"], status="COMPLETED", process_state="DONE", progress=100,
        processed_file_path=parts[0] if parts else None,
        processed_files_json=json.dumps(parts),
    )
    return settings, state, workspace, [Path(value) for value in parts]


def test_valid_completed_job_deletes_only_workspace_and_persists(settings, tmp_path):
    settings, state, workspace, parts = completed_job(settings, tmp_path)
    expected = sum(path.stat().st_size for path in workspace.rglob("*") if path.is_file())
    CleanupWorker(settings, state, probe=lambda _path: None).tick(NOW)
    job = state.processing_jobs()[0]
    assert not workspace.exists() and all(path.is_file() for path in parts)
    assert (job["status"], job["cleanup_state"], job["source_deleted"]) == (
        "COMPLETED", "CLEANED", 1,
    )
    assert job["cleanup_bytes_freed"] == expected and job["cleanup_at"] == NOW.isoformat()
    assert state.download_jobs_due(NOW.isoformat()) == []
    assert state.process_jobs_due(NOW.isoformat()) == []
    assert state.cleanup_job_due(NOW.isoformat()) is None


def test_dry_run_verifies_but_never_deletes(settings, tmp_path):
    settings, state, workspace, parts = completed_job(settings, tmp_path, dry_run=True)
    seen = []
    CleanupWorker(settings, state, probe=seen.append).tick(NOW)
    job = state.processing_jobs()[0]
    assert workspace.is_dir() and seen == parts
    assert (job["cleanup_state"], job["source_deleted"]) == ("PENDING", 0)


@pytest.mark.parametrize("failure", ["empty", "missing", "outside", "zero"])
def test_invalid_part_set_is_preserved(settings, tmp_path, failure):
    settings, state, workspace, parts = completed_job(settings, tmp_path)
    job = state.processing_jobs()[0]
    values = [str(path) for path in parts]
    if failure == "empty":
        values = []
    elif failure == "missing":
        parts[0].unlink()
    elif failure == "outside":
        other = tmp_path / "outside.mp4"
        other.write_bytes(b"part")
        values[0] = str(other)
    else:
        parts[0].write_bytes(b"")
    state.update_process_job(
        job["id"], status="COMPLETED", process_state="DONE",
        processed_files_json=json.dumps(values),
    )
    CleanupWorker(settings, state, probe=lambda _path: None).tick(NOW)
    assert workspace.is_dir()
    assert state.processing_jobs()[0]["cleanup_state"] in {"PENDING", "VERIFY_FAILED"}


def test_ffprobe_failure_preserves_workspace(settings, tmp_path):
    settings, state, workspace, _ = completed_job(settings, tmp_path)
    worker = CleanupWorker(
        settings, state, probe=lambda _path: (_ for _ in ()).throw(RuntimeError("bad media")),
    )
    worker.tick(NOW)
    job = state.processing_jobs()[0]
    assert workspace.is_dir()
    assert (job["cleanup_state"], job["cleanup_error"]) == ("VERIFY_FAILED", "bad media")


def test_path_safety_blocks_root_and_outside(settings, tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    with pytest.raises(ValueError, match="UNSAFE_CLEANUP_PATH"):
        validate_workspace(root, root, 1)
    with pytest.raises(ValueError, match="UNSAFE_CLEANUP_PATH"):
        validate_workspace(root, tmp_path / "outside" / "1", 1)


def test_failed_job_is_ignored(settings, tmp_path):
    settings, state, workspace, _ = completed_job(settings, tmp_path)
    job = state.processing_jobs()[0]
    state.update_process_job(job["id"], status="FAILED", process_state="FAILED")
    CleanupWorker(settings, state, probe=lambda _path: None).tick(NOW)
    assert workspace.is_dir() and state.processing_jobs()[0]["cleanup_state"] is None


def test_historical_completed_job_can_be_cleaned_after_migration(settings, tmp_path):
    settings, state, workspace, _ = completed_job(settings, tmp_path)
    restarted = StateStore(settings.state_db)
    CleanupWorker(settings, restarted, probe=lambda _path: None).tick(NOW)
    assert not workspace.exists() and restarted.processing_jobs()[0]["cleanup_state"] == "CLEANED"


def test_cleanup_columns_migrate_without_data_loss(tmp_path):
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as db:
        db.execute("""CREATE TABLE processing_jobs (
            id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL,
            video_id TEXT NOT NULL UNIQUE, video_url TEXT NOT NULL, video_title TEXT NOT NULL,
            source_channel_id TEXT NOT NULL, channel_name TEXT NOT NULL,
            output_dir TEXT NOT NULL, error TEXT)""")
        db.execute("INSERT INTO processing_jobs VALUES (1,'2026-01-01','COMPLETED',?,?,?,?,?,?,?)",
                   (VIDEO_ID, "https://youtu.be/x", "Old", CHANNEL_ID, "JOIN", "D:\\NAS", None))
    job = StateStore(database).processing_jobs()[0]
    assert job["status"] == "COMPLETED" and job["cleanup_state"] is None
    assert job["source_deleted"] == 0 and job["cleanup_bytes_freed"] is None
