from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.channel_store import ChannelStore, ChannelStoreError
from app.config import TeamMember, load_team_members
from app.detector import handle_detected_video
from app.jobs import create_processing_job
from app.main import create_app
from app.models import VideoEvent
from app.state import StateStore
from tests.conftest import CHANNEL_ID, VIDEO_ID


def event(video_id=VIDEO_ID, channel_id=CHANNEL_ID):
    return VideoEvent(video_id, channel_id, "Video", "", "", f"https://youtu.be/{video_id}")


def notifier():
    value = Mock()
    value.send_video.return_value = True
    return value


def test_team_config_has_exactly_four_unique_stable_ids(settings, tmp_path):
    members = load_team_members(settings.team_members_file)
    assert len(members) == len({member.id for member in members}) == 4
    renamed = tmp_path / "team.json"
    renamed.write_text(json.dumps([
        {"id": member.id, "display_name": f"Renamed {index}", "nas_folder": member.nas_folder}
        for index, member in enumerate(members)
    ]), encoding="utf-8")
    assert [member.id for member in load_team_members(renamed)] == [member.id for member in members]


def test_invalid_team_config_rejected(tmp_path):
    path = tmp_path / "team.json"
    path.write_text(json.dumps([
        {"id": "same", "display_name": "A", "nas_folder": f"M{i}"} for i in range(4)
    ]), encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        load_team_members(path)


def test_legacy_channel_defaults_to_first_owner_and_persists_on_update(settings):
    members = load_team_members(settings.team_members_file)
    store = ChannelStore(settings.channels_file, members)
    assert store.list()[0].owner_id == members[0].id
    moved, _ = store.update(CHANNEL_ID, owner_id=members[1].id)
    assert moved.owner_id == members[1].id
    assert ChannelStore(settings.channels_file, members).list()[0].owner_id == members[1].id
    assert json.loads(settings.channels_file.read_text(encoding="utf-8"))[0]["owner_id"] == members[1].id


def test_channel_api_adds_filters_and_rejects_owner_changes(settings):
    members = load_team_members(settings.team_members_file)
    client = TestClient(create_app(settings))
    ids = ["UC" + f"{index:022d}" for index in range(10, 14)]
    for channel_id, member in zip(ids, members):
        response = client.post("/api/channels", json={
            "channel_id": channel_id, "name": member.display_name, "owner_id": member.id,
        })
        assert response.status_code == 201 and response.json()["owner_id"] == member.id
    rows = client.get("/api/channels").json()
    assert all(sum(row["owner_id"] == member.id for row in rows) >= 1 for member in members)
    owner_change = client.patch(f"/api/channels/{ids[0]}", json={"owner_id": members[3].id})
    assert owner_change.status_code == 400
    assert client.get("/api/channels").json()[1]["owner_id"] == members[0].id


def test_notify_channels_remain_owner_free(settings):
    state = StateStore(settings.state_db)
    state.add_notify_channel(CHANNEL_ID, "Notify", f"https://youtube.com/channel/{CHANNEL_ID}")
    row = dict(state.notify_channels()[0])
    assert "owner_id" not in row


def test_all_four_owners_snapshot_exact_member_and_channel_folder(settings, tmp_path):
    members = load_team_members(settings.team_members_file)
    root = tmp_path / "nas"
    root.mkdir()
    state = StateStore(settings.state_db)
    for index, member in enumerate(members):
        (root / member.nas_folder).mkdir()
        item = event(f"video{index:06d}", "UC" + f"{index:022d}")
        assert create_processing_job(state, item, f"Channel {index}", member.id, members, root)
    jobs = sorted(state.processing_jobs(), key=lambda row: row["owner_id"])
    for job, member in zip(jobs, members):
        assert job["owner_id"] == member.id
        assert Path(job["output_dir"]) == root / member.nas_folder / job["channel_name"]


def test_channel_move_and_restart_do_not_mutate_existing_snapshot(settings, tmp_path):
    members = load_team_members(settings.team_members_file)
    root = tmp_path / "nas"
    for member in members:
        (root / member.nas_folder).mkdir(parents=True)
    store = ChannelStore(settings.channels_file, members)
    store.update(CHANNEL_ID, owner_id=members[1].id)
    state = StateStore(settings.state_db)
    handle_detected_video(
        event(), state, notifier(), {CHANNEL_ID: "Test Channel"}, nas_output_root=root,
        owner_id=members[1].id, team_members=members,
    )
    original = dict(state.processing_jobs()[0])
    store.update(CHANNEL_ID, owner_id=members[3].id)
    restarted_store = ChannelStore(settings.channels_file, members)
    restarted_state = StateStore(settings.state_db)
    handle_detected_video(
        event("abcdefghijk"), restarted_state, notifier(), {CHANNEL_ID: "Test Channel"},
        nas_output_root=root, owner_id=restarted_store.list()[0].owner_id, team_members=members,
    )
    jobs = {row["video_id"]: dict(row) for row in restarted_state.processing_jobs()}
    assert jobs[VIDEO_ID]["owner_id"] == original["owner_id"] == members[1].id
    assert jobs[VIDEO_ID]["output_dir"] == original["output_dir"]
    assert jobs["abcdefghijk"]["owner_id"] == members[3].id
    assert Path(jobs["abcdefghijk"]["output_dir"]).parts[-2] == members[3].nas_folder


def test_missing_owner_fails_but_missing_member_folder_queues_for_fallback(settings, tmp_path):
    members = load_team_members(settings.team_members_file)
    root = tmp_path / "nas"
    root.mkdir()
    state = StateStore(settings.state_db)
    create_processing_job(state, event(), "Channel", "missing", members, root)
    create_processing_job(state, event("abcdefghijk"), "Channel", members[0].id, members, root)
    jobs = {row["video_id"]: row for row in state.processing_jobs()}
    assert (jobs[VIDEO_ID]["status"], jobs[VIDEO_ID]["error"], jobs[VIDEO_ID]["output_dir"]) == (
        "FAILED", "OWNER_CONFIG_MISSING", "",
    )
    assert (jobs["abcdefghijk"]["status"], jobs["abcdefghijk"]["error"], jobs["abcdefghijk"]["output_dir"]) == (
        "QUEUED", None, str(root / members[0].nas_folder / "Channel"),
    )
