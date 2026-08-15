from __future__ import annotations

import json
import sqlite3
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.detector import handle_detected_video
from app.main import create_app
from app.models import VideoEvent
from app.state import StateStore
from tests.conftest import CHANNEL_ID


def event(index: int = 1) -> VideoEvent:
    video_id = f"video{index:06d}"
    return VideoEvent(
        video_id, CHANNEL_ID, f"Video {index}", "", "",
        f"https://www.youtube.com/watch?v={video_id}",
    )


def add_job(state, tmp_path, index=1, status="QUEUED"):
    item = event(index)
    output = tmp_path / f"nas-{index}"
    output.mkdir()
    assert state.create_processing_job(
        item, f"Channel {index}", str(output), status, None, owner_id="member_1",
    )
    job = next(row for row in state.processing_jobs() if row["video_id"] == item.video_id)
    return item, job, output


def complete_job(state, tmp_path, index=1, *, nas_state="NOT_REQUIRED", clean_fallback=True):
    item, job, output = add_job(state, tmp_path, index)
    source = tmp_path / f"source-{index}.mp4"
    source.write_bytes(b"source")
    delivered = output / f"PART_{index}.mp4"
    delivered.write_bytes(b"delivered")
    state.update_download_job(
        job["id"], status="DOWNLOADED", download_state="DONE",
        downloaded_file_path=str(source),
    )
    if nas_state == "NOT_REQUIRED":
        state.set_processing_route(job["id"], str(output), nas_state)
    else:
        fallback = tmp_path / f"fallback-{index}"
        fallback.mkdir()
        state.set_processing_route(job["id"], str(fallback), nas_state)
    state.update_process_job(
        job["id"], status="COMPLETED", process_state="DONE", progress=100,
        processed_file_path=str(delivered),
        processed_files_json=json.dumps([str(delivered)]),
    )
    state.update_cleanup_job(
        job["id"], state="CLEANED", source_deleted=True, cleanup_at="2026-08-16T00:00:00+00:00",
    )
    if nas_state == "DONE" and clean_fallback:
        fallback.rmdir()
        state.mark_fallback_cleaned(job["id"], "2026-08-16T00:00:00+00:00")
    return item, state.processing_job(job["id"]), delivered


@pytest.mark.parametrize("terminal_status", ["COMPLETED", "DONE"])
def test_clear_completed_succeeds_without_deleting_delivered_file(
    settings, tmp_path, terminal_status,
):
    state = StateStore(settings.state_db)
    _, job, delivered = complete_job(state, tmp_path)
    if terminal_status == "DONE":
        state.update_process_job(job["id"], status="DONE", process_state="DONE", progress=100)
    response = TestClient(create_app(settings, state=state)).post("/api/jobs/clear-completed")
    assert response.status_code == 200 and response.json() == {"cleared": 1}
    assert state.processing_job(job["id"]) is None
    assert delivered.read_bytes() == b"delivered"


@pytest.mark.parametrize(
    "status", [
        "QUEUED", "DOWNLOAD_PENDING", "DOWNLOADING", "DOWNLOADED",
        "PROCESS_PENDING", "PROCESSING", "FAILED", "CANCELLED",
    ],
)
def test_active_failed_and_cancelled_jobs_are_not_cleared(settings, tmp_path, status):
    state = StateStore(settings.state_db)
    _, job, _ = add_job(state, tmp_path, status=status)
    assert state.clear_completed_jobs() == 0
    assert state.processing_job(job["id"])["status"] == status


@pytest.mark.parametrize("nas_state", ["PENDING", "SYNCING", "FAILED_RETRY", "CONFLICT"])
def test_incomplete_nas_delivery_is_not_clearable(settings, tmp_path, nas_state):
    state = StateStore(settings.state_db)
    _, job, _ = complete_job(state, tmp_path, nas_state=nas_state)
    assert state.clear_completed_jobs() == 0
    assert state.processing_job(job["id"])["nas_sync_state"] == nas_state


def test_fallback_must_be_cleaned_before_history_clear(settings, tmp_path):
    state = StateStore(settings.state_db)
    _, job, delivered = complete_job(state, tmp_path, nas_state="DONE", clean_fallback=False)
    assert state.clear_completed_jobs() == 0
    assert state.processing_job(job["id"])["fallback_cleanup_at"] is None
    assert delivered.is_file()


@pytest.mark.parametrize("dependency", ["cleanup", "output", "retry"])
def test_unresolved_cleanup_or_missing_output_is_not_clearable(settings, tmp_path, dependency):
    state = StateStore(settings.state_db)
    _, job, delivered = complete_job(state, tmp_path)
    if dependency == "cleanup":
        state.update_cleanup_job(job["id"], state="PENDING")
    elif dependency == "output":
        delivered.unlink()
    else:
        state.update_process_job(
            job["id"], status="COMPLETED", process_state="DONE",
            next_attempt_at="2099-01-01T00:00:00+00:00",
        )
    assert state.clear_completed_jobs() == 0
    assert state.processing_job(job["id"]) is not None


def test_bulk_clear_count_excludes_failed_and_cancelled(settings, tmp_path):
    state = StateStore(settings.state_db)
    complete_job(state, tmp_path, 1)
    complete_job(state, tmp_path, 2, nas_state="DONE")
    add_job(state, tmp_path, 3, "FAILED")
    add_job(state, tmp_path, 4, "CANCELLED")
    assert state.clear_completed_jobs() == 2
    assert {row["status"] for row in state.processing_jobs()} == {"FAILED", "CANCELLED"}


@pytest.mark.parametrize("status", ["FAILED", "CANCELLED"])
def test_failed_or_cancelled_job_can_be_cleared_individually(settings, tmp_path, status):
    state = StateStore(settings.state_db)
    item, job, output = add_job(state, tmp_path, status=status)
    retained = output / "diagnostic.mp4"
    retained.write_bytes(b"keep")
    assert state.record_event(item)
    state.record_notification_attempt(item.video_id, True, None)

    response = TestClient(create_app(settings, state=state)).delete(f"/api/jobs/{job['id']}")
    assert response.status_code == 200 and response.json() == {"status": "cleared"}
    assert state.processing_job(job["id"]) is None and retained.read_bytes() == b"keep"
    assert state.get_video(item.video_id)["notification_sent"] == 1


@pytest.mark.parametrize("status", ["QUEUED", "PROCESSING", "COMPLETED"])
def test_active_or_completed_job_cannot_use_failed_clear(settings, tmp_path, status):
    state = StateStore(settings.state_db)
    _, job, _ = add_job(state, tmp_path, status=status)
    response = TestClient(create_app(settings, state=state)).delete(f"/api/jobs/{job['id']}")
    assert response.status_code == 409 and response.json()["error"] == "JOB_NOT_CLEARABLE"
    assert state.processing_job(job["id"]) is not None


def test_clear_failed_job_not_found_is_specific(settings):
    response = TestClient(create_app(settings, state=StateStore(settings.state_db))).delete(
        "/api/jobs/999"
    )
    assert response.status_code == 404 and response.json()["error"] == "JOB_NOT_FOUND"


def test_video_dedupe_and_telegram_history_survive_clear_and_restart(settings, tmp_path):
    state = StateStore(settings.state_db)
    item, _, _ = complete_job(state, tmp_path)
    assert state.record_event(item)
    state.record_notification_attempt(item.video_id, True, None)
    assert state.clear_completed_jobs() == 1

    restarted = StateStore(settings.state_db)
    notifier = Mock()
    result = handle_detected_video(
        item, restarted, notifier, {CHANNEL_ID: "Channel 1"},
        create_job=True, owner_id="member_1", team_members=[],
    )
    assert result == "DUPLICATE"
    notifier.send_video.assert_not_called()
    video = restarted.get_video(item.video_id)
    assert video["notification_sent"] == 1 and video["notification_attempts"] == 1
    assert restarted.processing_jobs() == []


def test_jobs_api_default_and_maximum_limits(settings, tmp_path):
    state = StateStore(settings.state_db)
    for index in range(1, 502):
        add_job(state, tmp_path, index, "FAILED")
    client = TestClient(create_app(settings, state=state))
    default = client.get("/api/jobs").json()
    maximum = client.get("/api/jobs?limit=999").json()
    limited = client.get("/api/jobs?limit=3").json()
    assert len(default) == 200 and len(maximum) == 500 and len(limited) == 3
    assert [row["id"] for row in limited] == [501, 500, 499]


def test_clear_rolls_back_whole_transaction_on_delete_failure(settings, tmp_path):
    state = StateStore(settings.state_db)
    _, first, _ = complete_job(state, tmp_path, 1)
    _, second, _ = complete_job(state, tmp_path, 2)
    with sqlite3.connect(settings.state_db) as db:
        db.execute(
            f"""CREATE TRIGGER block_clear BEFORE DELETE ON processing_jobs
                WHEN OLD.id={second['id']}
                BEGIN SELECT RAISE(ABORT, 'blocked'); END"""
        )
    with pytest.raises(sqlite3.IntegrityError):
        state.clear_completed_jobs()
    assert {row["id"] for row in state.processing_jobs()} == {first["id"], second["id"]}


def test_dashboard_has_one_bulk_clear_action():
    html = open("app/dashboard.html", encoding="utf-8").read()
    assert html.count('id="clear-completed"') == 1
    assert "/api/jobs/clear-completed" in html
    assert "Xóa lịch sử các job đã hoàn thành?" in html
    assert "Xóa lịch sử job này? File đã tạo sẽ được giữ nguyên." in html
