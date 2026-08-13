from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.config as config_module
from app.config import load_team_members, update_team_member
from app.jobs import create_processing_job
from app.main import create_app
from app.models import VideoEvent
from app.state import StateStore
from tests.conftest import CHANNEL_ID


def test_get_returns_four_members(settings):
    response = TestClient(create_app(settings)).get("/api/team-members")
    assert response.status_code == 200
    assert [member["id"] for member in response.json()] == [f"member_{index}" for index in range(1, 5)]


@pytest.mark.parametrize(
    ("payload", "expected_name", "expected_folder"),
    [
        ({"display_name": "Nhan"}, "Nhan", "Member_1"),
        ({"nas_folder": "Nhan_NAS"}, "Member 1", "Nhan_NAS"),
        ({"display_name": "Nhan", "nas_folder": "Nhan_NAS"}, "Nhan", "Nhan_NAS"),
    ],
)
def test_patch_member_fields(settings, payload, expected_name, expected_folder):
    client = TestClient(create_app(settings))
    response = client.patch("/api/team-members/member_1", json=payload)
    assert response.status_code == 200
    assert response.json() == {"id": "member_1", "display_name": expected_name, "nas_folder": expected_folder}
    assert load_team_members(settings.team_members_file)[0] == config_module.TeamMember(
        "member_1", expected_name, expected_folder,
    )


def test_member_id_is_immutable_and_unknown_id_is_rejected(settings):
    client = TestClient(create_app(settings))
    immutable = client.patch(
        "/api/team-members/member_1", json={"id": "member_2", "display_name": "Nhan"},
    )
    missing = client.patch("/api/team-members/unknown", json={"display_name": "Nhan"})
    assert immutable.status_code == 400
    assert load_team_members(settings.team_members_file)[0].id == "member_1"
    assert missing.status_code == 404 and missing.json()["error"] == "MEMBER_NOT_FOUND"


@pytest.mark.parametrize("payload", [{"display_name": "   "}, {"nas_folder": ""}, {"nas_folder": ".."}, {"nas_folder": "Nhan\\Team"}, {"nas_folder": "D:\\Nhan"}, {"nas_folder": "//server/share"}])
def test_invalid_member_values_are_rejected(settings, payload):
    response = TestClient(create_app(settings)).patch("/api/team-members/member_1", json=payload)
    assert response.status_code == 400
    assert response.json()["error"] == "INVALID_TEAM_MEMBER"


def test_update_preserves_four_member_invariant_and_channel_owner(settings):
    client = TestClient(create_app(settings))
    before_owner = client.get("/api/channels").json()[0]["owner_id"]
    assert client.patch("/api/team-members/member_1", json={"display_name": "Nhan"}).status_code == 200
    members = load_team_members(settings.team_members_file)
    assert len(members) == 4 and {member.id for member in members} == {f"member_{index}" for index in range(1, 5)}
    assert client.get("/api/channels").json()[0]["owner_id"] == before_owner == "member_1"


def test_config_update_is_atomic_on_replace_failure(settings, monkeypatch):
    original = settings.team_members_file.read_bytes()

    def fail_replace(source, destination):
        assert load_team_members(Path(source))[0].display_name == "Nhan"
        assert Path(destination) == settings.team_members_file
        raise OSError("disk failure")

    monkeypatch.setattr(config_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="disk failure"):
        update_team_member(settings.team_members_file, "member_1", display_name="Nhan")
    assert settings.team_members_file.read_bytes() == original
    assert not settings.team_members_file.with_name("team_members.json.tmp").exists()


def test_existing_job_keeps_snapshot_and_new_job_uses_updated_folder(settings, tmp_path):
    state = StateStore(settings.state_db)
    root = tmp_path / "nas"
    old_root = root / "Member_1"
    new_root = root / "Nhan_NAS"
    old_root.mkdir(parents=True)
    new_root.mkdir()
    members = load_team_members(settings.team_members_file)
    first = VideoEvent("firstvideo1", CHANNEL_ID, "First", "", "", "https://youtu.be/firstvideo1")
    second = VideoEvent("secondvide2", CHANNEL_ID, "Second", "", "", "https://youtu.be/secondvide2")
    create_processing_job(state, first, "Test Channel", "member_1", members, root)
    original_output = dict(state.processing_jobs()[0])["output_dir"]
    updated = update_team_member(settings.team_members_file, "member_1", nas_folder="Nhan_NAS")
    create_processing_job(state, second, "Test Channel", "member_1", updated, root)
    jobs = {job["video_id"]: dict(job) for job in state.processing_jobs()}
    assert jobs["firstvideo1"]["output_dir"] == original_output == str(old_root / "Test Channel")
    assert jobs["secondvide2"]["output_dir"] == str(new_root / "Test Channel")


def test_dashboard_contains_member_edit_ui():
    html = Path("app/dashboard.html").read_text(encoding="utf-8")
    assert 'id="member-dialog"' in html
    assert "edit-member" in html
    assert "/api/team-members/${editingMemberId}" in html
