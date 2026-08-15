from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.channel_store import ChannelStore
from app.config import load_team_members
from app.detector import handle_detected_video
from app.main import create_app
from app.models import VideoEvent
from app.state import StateStore
from tests.conftest import CHANNEL_ID, VIDEO_ID


NEW_VIDEO = "abcdefghijk"


def event(video_id=VIDEO_ID):
    return VideoEvent(video_id, CHANNEL_ID, "Video", "", "", f"https://youtu.be/{video_id}")


def test_patch_name_trims_unicode_and_preserves_channel_state(settings):
    state = StateStore(settings.state_db)
    state.record_poll_success(CHANNEL_ID, VIDEO_ID)
    before_poll = dict(state.get_poll_state(CHANNEL_ID))
    before = ChannelStore(
        settings.channels_file, load_team_members(settings.team_members_file)
    ).list()[0]
    client = TestClient(create_app(settings, state=state))
    response = client.patch(
        f"/api/channels/{CHANNEL_ID}", json={"name": "  CNBC News - Nhật  "},
    )
    after = response.json()
    assert response.status_code == 200 and after["name"] == "CNBC News - Nhật"
    assert after["channel_id"] == before.channel_id
    assert after["owner_id"] == before.owner_id
    assert after["cut_enabled"] == before.cut_enabled
    assert dict(state.get_poll_state(CHANNEL_ID)) == before_poll


def test_patch_name_rejects_empty_and_too_long(settings):
    client = TestClient(create_app(settings))
    empty = client.patch(f"/api/channels/{CHANNEL_ID}", json={"name": "   "})
    long = client.patch(f"/api/channels/{CHANNEL_ID}", json={"name": "x" * 101})
    assert (empty.status_code, empty.json()["error"]) == (400, "INVALID_CHANNEL_NAME")
    assert (long.status_code, long.json()["error"]) == (400, "INVALID_CHANNEL_NAME")


def test_rename_keeps_old_job_path_and_future_job_uses_new_name(settings, tmp_path):
    members = load_team_members(settings.team_members_file)
    root = tmp_path / "nas"
    for member in members:
        (root / member.nas_folder).mkdir(parents=True)
    state, notifier = StateStore(settings.state_db), Mock()
    notifier.send_video.return_value = True
    store = ChannelStore(settings.channels_file, members)
    channel = store.list()[0]
    handle_detected_video(
        event(), state, notifier, {CHANNEL_ID: channel.name}, nas_output_root=root,
        owner_id=channel.owner_id, team_members=members,
    )
    old_job = dict(state.processing_jobs()[0])
    renamed, _ = store.update(CHANNEL_ID, name="Tên Mới - Mỹ")
    handle_detected_video(
        event(NEW_VIDEO), state, notifier, {CHANNEL_ID: renamed.name}, nas_output_root=root,
        owner_id=renamed.owner_id, team_members=members,
    )
    jobs = {row["video_id"]: row for row in state.processing_jobs()}
    assert jobs[VIDEO_ID]["output_dir"] == old_job["output_dir"]
    assert Path(jobs[NEW_VIDEO]["output_dir"]).name == "Tên Mới - Mỹ"
    assert notifier.send_video.call_args_list[-1].args[1] == "Tên Mới - Mỹ"


def test_dashboard_has_rename_dialog_and_preserves_active_tab(settings):
    html = TestClient(create_app(settings)).get("/").text
    assert "✎" in html and 'id="rename-dialog"' in html
    assert "if (!activeOwner) activeOwner = members[0].id" in html
    assert "renameChannel.channel_id" in html
