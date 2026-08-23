from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.channel_store import ChannelStore
from app.main import create_app
from app.minha import MinHaUnavailable
from app.models import VideoEvent
from app.state import StateStore
from app.tiktok_publisher import (
    PRIVATE_VISIBILITY,
    PreparedPost,
    PostedResult,
    PublishError,
    PublishStore,
    TikTokPublisher,
    TikTokUploadAutomation,
)
from tests.conftest import CHANNEL_ID


PROFILE_ID = "0923d692-e561-4468-b508-e73fa5e93659"
UID = "7574927887251407894"


class FakeMinHa:
    base_url = "http://127.0.0.1:8080"
    auth_token = ""

    def __init__(self, probe=None, *, profile=True, running=False, unavailable=False):
        self.profile = ({
            "id": PROFILE_ID,
            "name": "NDE003",
            "expected_tiktok_uid": UID,
            "tiktok_account_match": "MATCH",
        } if profile else None)
        self.probe = probe or {
            "status": "DETECTED", "logged_in": True,
            "tiktok_uid": UID, "tiktok_username": "user7588053660900",
        }
        self.running = running
        self.unavailable = unavailable
        self.calls = []

    def get_profile(self, profile_id):
        self.calls.append(("get", profile_id))
        if self.unavailable:
            raise MinHaUnavailable
        return self.profile if profile_id == PROFILE_ID else None

    def probe_tiktok(self, profile_id):
        self.calls.append(("probe", profile_id))
        if self.unavailable:
            raise MinHaUnavailable
        return self.probe

    def profile_status(self, profile_id):
        self.calls.append(("status", profile_id))
        return {"status": "running" if self.running else "stopped"}

    def launch_profile(self, profile_id):
        self.calls.append(("launch", profile_id))
        self.running = True
        return {"status": "running"}

    def stop_profile(self, profile_id):
        self.calls.append(("stop", profile_id))
        self.running = False
        return {"ok": True}

    def list_profiles(self):
        return [self.profile] if self.profile else []


class Automation:
    def __init__(self, result=None, error=None, publish_error=None):
        self.result = result or PreparedPost(True, PRIVATE_VISIBILITY, "VISIBLE_TEXT:Only you")
        self.error = error
        self.publish_error = publish_error
        self.calls = []
        self.publish_calls = []

    def prepare(self, profile_id, video_path, caption):
        self.calls.append((profile_id, video_path, caption))
        if self.error:
            raise self.error
        return self.result

    def publish(self, profile_id, video_path, caption, before_post, after_post):
        self.publish_calls.append((profile_id, video_path, caption))
        before_post(self.result)
        after_post()
        if self.publish_error:
            raise self.publish_error
        return PostedResult("SUCCESS_TEXT:Video uploaded")


class Notifier:
    def __init__(self):
        self.messages = []
        self.on_send = None

    def send_message(self, text):
        if self.on_send:
            self.on_send()
        self.messages.append(text)
        return True


def setup_publisher(
    settings, tmp_path, *, title="Caption", minha=None, automation=None,
    notifier=None, video_id="video000001",
):
    state = StateStore(settings.state_db)
    channels = ChannelStore(settings.channels_file)
    channels.set_minha_profile(CHANNEL_ID, PROFILE_ID)
    output = tmp_path / f"{video_id}.mp4"
    output.write_bytes(b"real-video")
    event = VideoEvent(video_id, CHANNEL_ID, title, "", "", f"https://youtu.be/{video_id}")
    assert state.create_processing_job(
        event, "Test Channel", str(tmp_path), "QUEUED", None,
        owner_id="member_1", minha_profile_id=PROFILE_ID,
    )
    processing = state.processing_jobs()[0]
    state.update_process_job(
        processing["id"], status="COMPLETED", process_state="DONE", progress=100,
        processed_file_path=str(output), processed_files_json=json.dumps([str(output)]),
    )
    minha = minha or FakeMinHa()
    automation = automation or Automation()
    publisher = TikTokPublisher(state, channels, minha, automation, notifier)
    return publisher, state, output, minha, automation


def create_candidate(publisher, state, output):
    processing = state.processing_jobs()[0]
    return publisher.create(processing["id"], CHANNEL_ID, str(output))


def test_valid_provenance_and_api_round_trip(settings, tmp_path):
    publisher, state, output, _minha, _automation = setup_publisher(settings, tmp_path)
    client = TestClient(create_app(settings, state=state, publisher=publisher, minha_client=FakeMinHa()))
    response = client.post("/api/publish-jobs", json={
        "processing_job_id": state.processing_jobs()[0]["id"],
        "channel_id": CHANNEL_ID,
        "video_path": str(output),
    })
    assert response.status_code == 201
    assert response.json()["caption"] == "Caption"
    job_id = response.json()["id"]
    assert client.get("/api/publish-jobs").json()[0]["id"] == job_id
    assert client.get(f"/api/publish-jobs/{job_id}").json()["receipt"] is None
    assert client.get("/api/jobs").json()[0]["tiktok_publish"]["status"] == "QUEUED"


def test_orphan_missing_job_wrong_channel_and_missing_caption_are_rejected(settings, tmp_path):
    publisher, state, output, _minha, _automation = setup_publisher(settings, tmp_path)
    orphan = tmp_path / "orphan.mp4"
    orphan.write_bytes(b"orphan")
    processing_id = state.processing_jobs()[0]["id"]
    with pytest.raises(PublishError, match="OUTPUT_NOT_OWNED"):
        publisher.create(processing_id, CHANNEL_ID, str(orphan))
    with pytest.raises(PublishError, match="PROCESSING_JOB_NOT_FOUND"):
        publisher.create(999, CHANNEL_ID, str(orphan))
    with pytest.raises(PublishError, match="CHANNEL_MISMATCH"):
        publisher.create(processing_id, "UCaaaaaaaaaaaaaaaaaaaaaa", str(output))

    with sqlite3.connect(settings.state_db) as db:
        db.execute("UPDATE processing_jobs SET video_title='' WHERE id=?", (processing_id,))
    with pytest.raises(PublishError, match="CAPTION_MISSING"):
        publisher.create(processing_id, CHANNEL_ID, str(output))


def test_missing_file_and_incomplete_processing_are_rejected(settings, tmp_path):
    publisher, state, output, _minha, _automation = setup_publisher(settings, tmp_path)
    processing_id = state.processing_jobs()[0]["id"]
    output.unlink()
    with pytest.raises(PublishError, match="PROCESSED_OUTPUT_MISSING"):
        publisher.create(processing_id, CHANNEL_ID, str(output))
    output.write_bytes(b"video")
    with sqlite3.connect(settings.state_db) as db:
        db.execute("UPDATE processing_jobs SET status='PROCESSING' WHERE id=?", (processing_id,))
    with pytest.raises(PublishError, match="PROCESSING_JOB_NOT_COMPLETED"):
        publisher.create(processing_id, CHANNEL_ID, str(output))


@pytest.mark.parametrize("probe,profile_change,reason", [
    ({"status": "DETECTED", "logged_in": True, "tiktok_uid": UID}, {"expected_tiktok_uid": None}, "UID_UNLOCKED"),
    ({"status": "DETECTED", "logged_in": True, "tiktok_uid": "1111111111111111111"}, {}, "ACCOUNT_MISMATCH"),
    ({"status": "NOT_LOGGED_IN", "logged_in": False}, {}, "NOT_LOGGED_IN"),
    ({"status": "UID_NOT_DETECTED", "logged_in": True}, {}, "UID_NOT_DETECTED"),
    ({"status": "ERROR", "logged_in": False}, {}, "PROBE_ERROR"),
    ({"status": "NEW_STATE", "logged_in": True}, {}, "UNKNOWN_IDENTITY_STATE"),
])
def test_fresh_identity_fail_closed(settings, tmp_path, probe, profile_change, reason):
    minha = FakeMinHa(probe)
    minha.profile.update(profile_change)
    automation = Automation()
    publisher, state, output, _minha, _automation = setup_publisher(
        settings, tmp_path, minha=minha, automation=automation,
    )
    job = create_candidate(publisher, state, output)
    result = publisher.run(job["id"])
    assert result["status"] == "BLOCKED"
    assert result["failure_reason"] == reason
    assert automation.calls == []


def test_missing_profile_and_minha_unavailable_block_before_navigation(settings, tmp_path):
    for index, minha in enumerate((FakeMinHa(profile=False), FakeMinHa(unavailable=True))):
        publisher, state, output, _minha, automation = setup_publisher(
            settings, tmp_path, minha=minha, video_id=f"video00000{index + 2}",
        )
        result = publisher.run(create_candidate(publisher, state, output)["id"])
        assert result["failure_reason"] in {"MINHA_PROFILE_NOT_FOUND", "MINHA_UNAVAILABLE"}
        assert automation.calls == []


def test_stale_dashboard_match_cannot_bypass_fresh_probe(settings, tmp_path):
    minha = FakeMinHa({
        "status": "DETECTED", "logged_in": True,
        "tiktok_uid": "9999999999999999999", "tiktok_username": "wrong",
    })
    assert minha.profile["tiktok_account_match"] == "MATCH"
    publisher, state, output, _minha, automation = setup_publisher(settings, tmp_path, minha=minha)
    result = publisher.run(create_candidate(publisher, state, output)["id"], dry_run=False)
    assert (result["status"], result["failure_reason"]) == ("BLOCKED", "ACCOUNT_MISMATCH")
    assert automation.calls == []


def test_match_uses_stable_profile_id_and_stops_before_post(settings, tmp_path):
    publisher, state, output, minha, automation = setup_publisher(settings, tmp_path)
    job = create_candidate(publisher, state, output)
    result = publisher.run(job["id"])
    check = json.loads(result["pre_publish_check_json"])
    assert result["status"] == "READY_FOR_POST"
    assert check["post_clicked"] is False
    assert check["identity"] == "MATCH"
    assert check["visibility"] == PRIVATE_VISIBILITY
    assert automation.calls == [(PROFILE_ID, str(output), "Caption")]
    assert all(call[1] == PROFILE_ID for call in minha.calls)
    assert "user7588053660900" not in {call[1] for call in minha.calls}


@pytest.mark.parametrize("result", [
    PreparedPost(True, "PUBLIC", "VISIBLE_TEXT:Public"),
    PreparedPost(False, PRIVATE_VISIBILITY, "VISIBLE_TEXT:Only you"),
])
def test_unverified_private_or_upload_blocks_post(settings, tmp_path, result):
    publisher, state, output, _minha, _automation = setup_publisher(
        settings, tmp_path, automation=Automation(result=result),
    )
    saved = publisher.run(create_candidate(publisher, state, output)["id"])
    assert saved["status"] in {"BLOCKED", "FAILED", "FAILED_PRE_POST"}
    assert saved["status"] != "READY_FOR_POST"


def test_idempotency_double_create_and_double_run(settings, tmp_path):
    publisher, state, output, _minha, automation = setup_publisher(settings, tmp_path)
    job = create_candidate(publisher, state, output)
    with pytest.raises(PublishError) as duplicate:
        create_candidate(publisher, state, output)
    assert duplicate.value.code == "PUBLISH_JOB_ALREADY_EXISTS"
    assert publisher.run(job["id"])["status"] == "READY_FOR_POST"
    with pytest.raises(PublishError) as rerun:
        publisher.run(job["id"])
    assert rerun.value.code == "PUBLISH_JOB_NOT_RUNNABLE"
    assert len(automation.calls) == 1


def test_global_concurrency_allows_only_one_claim(settings, tmp_path):
    publisher, state, output, _minha, _automation = setup_publisher(settings, tmp_path)
    first = create_candidate(publisher, state, output)
    second_output = tmp_path / "second.mp4"
    second_output.write_bytes(b"second")
    event = VideoEvent("video000099", CHANNEL_ID, "Second", "", "", "https://youtu.be/video000099")
    state.create_processing_job(
        event, "Test Channel", str(tmp_path), "QUEUED", None,
        owner_id="member_1", minha_profile_id=PROFILE_ID,
    )
    second_processing = next(row for row in state.processing_jobs() if row["video_id"] == "video000099")
    state.update_process_job(
        second_processing["id"], status="COMPLETED", process_state="DONE",
        processed_file_path=str(second_output), processed_files_json=json.dumps([str(second_output)]),
    )
    second = publisher.create(second_processing["id"], CHANNEL_ID, str(second_output))
    publisher.store.claim(first["id"])
    with pytest.raises(PublishError, match="PUBLISHER_BUSY"):
        publisher.store.claim(second["id"])


def test_restart_marks_post_click_uncertain_and_never_auto_retries(settings, tmp_path):
    publisher, state, output, _minha, _automation = setup_publisher(settings, tmp_path)
    job = create_candidate(publisher, state, output)
    with sqlite3.connect(settings.state_db) as db:
        db.execute("UPDATE publish_jobs SET status='POSTING' WHERE id=?", (job["id"],))
    restarted = PublishStore(state)
    saved = restarted.get(job["id"])
    assert saved["status"] == "POST_RESULT_UNCERTAIN"
    with pytest.raises(PublishError, match="PUBLISH_JOB_NOT_RUNNABLE"):
        restarted.claim(job["id"])


@pytest.mark.parametrize("initially_running,expected_calls", [
    (False, ["launch", "stop"]),
    (True, []),
])
def test_browser_lifecycle_restores_original_state(settings, tmp_path, initially_running, expected_calls):
    minha = FakeMinHa(running=initially_running)
    publisher, state, output, _minha, _automation = setup_publisher(settings, tmp_path, minha=minha)
    assert publisher.run(create_candidate(publisher, state, output)["id"])["status"] == "READY_FOR_POST"
    lifecycle = [name for name, _profile_id in minha.calls if name in {"launch", "stop"}]
    assert lifecycle == expected_calls
    assert minha.running is initially_running


def test_explicit_real_post_writes_receipt_then_notifies(settings, tmp_path):
    notifier = Notifier()
    publisher, state, output, _minha, automation = setup_publisher(
        settings, tmp_path, notifier=notifier,
    )
    job = create_candidate(publisher, state, output)
    notifier.on_send = lambda: publisher.store.receipt(job["id"])["verification_method"]
    client = TestClient(create_app(settings, state=state, publisher=publisher))
    response = client.post(f"/api/publish-jobs/{job['id']}/run", json={"dry_run": False})
    assert response.status_code == 200 and response.json()["status"] == "DONE"
    assert len(automation.publish_calls) == 1
    assert publisher.store.receipt(job["id"])["verification_method"].startswith("SUCCESS_TEXT:")
    assert len(notifier.messages) == 1 and "\u2705 TIKTOK POSTED" in notifier.messages[0]

    rerun = client.post(f"/api/publish-jobs/{job['id']}/run", json={"dry_run": False})
    assert rerun.status_code == 409
    assert len(automation.publish_calls) == 1 and len(notifier.messages) == 1


def test_done_auto_publish_creates_ordered_unique_jobs(settings, tmp_path):
    publisher, state, first, _minha, automation = setup_publisher(settings, tmp_path)
    parts = [first]
    for index in (2, 3):
        path = tmp_path / f"video000001_PART_{index}.mp4"
        path.write_bytes(b"part")
        parts.append(path)
    processing = state.processing_jobs()[0]
    with sqlite3.connect(settings.state_db) as db:
        db.execute(
            "UPDATE processing_jobs SET processed_files_json=?, processed_file_path=? WHERE id=?",
            (json.dumps([str(path) for path in parts]), str(parts[0]), processing["id"]),
        )
    first_run = publisher.handle_processing_done(processing["id"])
    second_run = publisher.handle_processing_done(processing["id"])
    rows = publisher.store.list()
    assert [row["video_path"] for row in reversed(rows)] == [str(path) for path in parts]
    assert all(row["status"] == "DONE" for row in rows)
    assert len(first_run) == 3 and second_run == []
    assert [call[1] for call in automation.publish_calls] == [str(path) for path in parts]


def test_done_auto_publish_skips_unmapped_channel(settings, tmp_path):
    publisher, state, output, _minha, automation = setup_publisher(settings, tmp_path)
    ChannelStore(settings.channels_file).set_minha_profile(CHANNEL_ID, None)
    assert publisher.handle_processing_done(state.processing_jobs()[0]["id"]) == []
    assert publisher.store.list() == []
    assert automation.publish_calls == []


def test_real_post_requires_explicit_existing_publish_job(settings):
    client = TestClient(create_app(settings))
    response = client.post("/api/publish-jobs/999/run", json={"dry_run": False})
    assert response.status_code == 404
    assert response.json()["error"] == "PUBLISH_JOB_NOT_FOUND"


def test_real_post_requires_private_before_click(settings, tmp_path):
    notifier = Notifier()
    automation = Automation(result=PreparedPost(True, "PUBLIC", "VISIBLE_TEXT:Public"))
    publisher, state, output, _minha, _automation = setup_publisher(
        settings, tmp_path, automation=automation, notifier=notifier,
    )
    result = publisher.run(create_candidate(publisher, state, output)["id"], dry_run=False)
    assert (result["status"], result["failure_reason"]) == (
        "BLOCKED", "PRIVATE_VISIBILITY_NOT_CONFIRMED",
    )
    assert publisher.store.receipt(result["id"]) is None
    assert not any("TIKTOK POSTED" in message for message in notifier.messages)


def test_post_verification_failure_becomes_uncertain_and_never_retries(settings, tmp_path):
    notifier = Notifier()
    automation = Automation(publish_error=PublishError("POST_SUCCESS_NOT_VERIFIED"))
    publisher, state, output, _minha, _automation = setup_publisher(
        settings, tmp_path, automation=automation, notifier=notifier,
    )
    job = create_candidate(publisher, state, output)
    result = publisher.run(job["id"], dry_run=False)
    assert result["status"] == "POST_RESULT_UNCERTAIN"
    assert publisher.store.receipt(job["id"]) is None
    assert "NO AUTO RETRY" in notifier.messages[0]
    with pytest.raises(PublishError) as rerun:
        publisher.run(job["id"], dry_run=False)
    assert rerun.value.code == "PUBLISH_JOB_NOT_RUNNABLE"
    assert len(automation.publish_calls) == 1


def test_done_requires_and_persists_durable_receipt(settings, tmp_path):
    publisher, state, output, _minha, _automation = setup_publisher(settings, tmp_path)
    job = create_candidate(publisher, state, output)
    publisher.store.update(
        job["id"], "VERIFYING", expected_tiktok_uid=UID,
        current_tiktok_uid=UID, tiktok_username="user7588053660900",
    )
    done = publisher.store.complete_with_receipt(
        job["id"], verification_method="SUCCESS_TEXT:Video published successfully",
    )
    receipt = publisher.store.receipt(job["id"])
    assert done["status"] == "DONE" and done["completed_at"]
    assert receipt["idempotency_key"] == job["idempotency_key"]
    assert receipt["tiktok_post_id"] is None and receipt["tiktok_post_url"] is None
    with pytest.raises(PublishError) as rerun:
        publisher.store.claim(job["id"])
    assert rerun.value.code == "PUBLISH_JOB_NOT_RUNNABLE"


def test_weak_success_signal_cannot_create_receipt(settings, tmp_path):
    publisher, state, output, _minha, _automation = setup_publisher(settings, tmp_path)
    job = create_candidate(publisher, state, output)
    publisher.store.update(job["id"], "VERIFYING")
    with pytest.raises(PublishError) as error:
        publisher.store.complete_with_receipt(job["id"], verification_method="UPLOAD_COMPLETE")
    assert error.value.code == "POST_VERIFICATION_WEAK"
    assert publisher.store.receipt(job["id"]) is None


def test_publish_clear_only_removes_safe_terminal_history(settings, tmp_path):
    publisher, state, output, _minha, _automation = setup_publisher(settings, tmp_path)
    done = create_candidate(publisher, state, output)
    failed_path = tmp_path / "failed.mp4"; failed_path.write_bytes(b"failed")
    failed = publisher.store.create(state.processing_jobs()[0]["id"], CHANNEL_ID, PROFILE_ID, str(failed_path), "Caption")
    uncertain_path = tmp_path / "uncertain.mp4"; uncertain_path.write_bytes(b"uncertain")
    uncertain = publisher.store.create(state.processing_jobs()[0]["id"], CHANNEL_ID, PROFILE_ID, str(uncertain_path), "Caption")
    active_path = tmp_path / "active.mp4"; active_path.write_bytes(b"active")
    active = publisher.store.create(state.processing_jobs()[0]["id"], CHANNEL_ID, PROFILE_ID, str(active_path), "Caption")
    publisher.store.update(done["id"], "DONE")
    publisher.store.update(failed["id"], "FAILED")
    publisher.store.update(uncertain["id"], "POST_RESULT_UNCERTAIN")
    publisher.store.update(active["id"], "UPLOADING")
    assert publisher.clear_completed() == 1
    assert publisher.clear_failed() == 1
    assert publisher.store.get(uncertain["id"])["status"] == "POST_RESULT_UNCERTAIN"
    assert publisher.store.get(active["id"])["status"] == "UPLOADING"


def test_publish_diagnostics_persist_attach_and_post_counts(settings, tmp_path):
    publisher, state, output, _minha, _automation = setup_publisher(settings, tmp_path)
    job = create_candidate(publisher, state, output)
    saved = publisher.store.update(
        job["id"], "UPLOADING", current_step="FILE_ATTACHED",
        upload_attach_count=1, post_click_count=0, post_attempted_at=None,
        post_verification_method=None, tiktok_post_id=None, tiktok_post_url=None,
    )
    assert saved["upload_attach_count"] == 1
    assert saved["post_click_count"] == 0


def test_restart_pre_click_state_is_failed_pre_post(settings, tmp_path):
    publisher, state, output, _minha, _automation = setup_publisher(settings, tmp_path)
    job = create_candidate(publisher, state, output)
    with sqlite3.connect(settings.state_db) as db:
        db.execute("UPDATE publish_jobs SET status='UPLOADING' WHERE id=?", (job["id"],))
    restarted = PublishStore(state)
    saved = restarted.get(job["id"])
    assert saved["status"] == "FAILED_PRE_POST"
    assert saved["failure_reason"] == "PROCESS_RESTART_BEFORE_POST"


def test_post_click_count_is_one_and_uncertain_never_retries(settings, tmp_path):
    automation = Automation(publish_error=PublishError("POST_SUCCESS_NOT_VERIFIED"))
    publisher, state, output, _minha, _automation = setup_publisher(settings, tmp_path, automation=automation)
    job = create_candidate(publisher, state, output)
    saved = publisher.run(job["id"], dry_run=False)
    assert saved["status"] == "POST_RESULT_UNCERTAIN"
    assert saved["post_click_count"] == 1
    with pytest.raises(PublishError, match="PUBLISH_JOB_NOT_RUNNABLE"):
        publisher.run(job["id"], dry_run=False)


def test_receipt_survives_cleanup_and_blocks_duplicate(settings, tmp_path):
    publisher, state, output, _minha, _automation = setup_publisher(settings, tmp_path)
    job = create_candidate(publisher, state, output)
    publisher.store.update(job["id"], "VERIFYING", expected_tiktok_uid=UID, current_tiktok_uid=UID, tiktok_username="user7588053660900")
    publisher.store.complete_with_receipt(job["id"], verification_method="SUCCESS_TEXT:Video published successfully")
    assert publisher.clear_history() == 0
    assert publisher.store.receipt(job["id"]) is not None
    with pytest.raises(PublishError, match="PUBLISH_JOB_NOT_RUNNABLE"):
        publisher.run(job["id"], dry_run=False)


def test_private_data_value_one_is_only_you(settings):
    automation = TikTokUploadAutomation(FakeMinHa())
    assert automation._is_private_option({"dataValue": "1"}) is True



def test_ready_to_post_hold_does_not_stop_profile(settings, tmp_path, monkeypatch):
    monkeypatch.setenv("TIKTOK_DEBUG_READY_ONLY", "1")
    minha = FakeMinHa(running=False)
    publisher, state, output, _minha, _automation = setup_publisher(settings, tmp_path, minha=minha)
    publisher.ready_only = True
    saved = publisher.run(create_candidate(publisher, state, output)["id"], dry_run=False)
    assert saved["status"] == "READY_TO_POST"
    assert [name for name, _ in minha.calls if name == "stop"] == []
    assert minha.running is True
