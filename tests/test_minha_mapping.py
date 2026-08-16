from __future__ import annotations

import json
import os
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.channel_store import ChannelStore
from app.detector import handle_detected_video
from app.main import create_app
from app.minha import MinHaUnavailable
from app.models import VideoEvent
from app.state import StateStore
from tests.conftest import CHANNEL_ID, VIDEO_ID


PROFILE_ID = "0923d692-e561-4468-b508-e73fa5e93659"
SECOND_CHANNEL = "UCaaaaaaaaaaaaaaaaaaaaaa"


def profile(**changes):
    value = {
        "id": PROFILE_ID,
        "name": "TN001UK",
        "tiktok_username": "tn001uk",
        "tiktok_uid": "7123456789012345678",
        "expected_tiktok_uid": "7123456789012345678",
        "tiktok_account_match": "MATCH",
    }
    value.update(changes)
    return value


class FakeMinHa:
    def __init__(self, profiles=None, unavailable=False):
        self.profiles = {item["id"]: item for item in ([profile()] if profiles is None else profiles)}
        self.unavailable = unavailable

    def list_profiles(self):
        if self.unavailable:
            raise MinHaUnavailable
        return list(self.profiles.values())

    def get_profile(self, profile_id):
        if self.unavailable:
            raise MinHaUnavailable
        return self.profiles.get(profile_id)


class Notifier:
    last_error = None
    last_transient = False

    def send_video(self, *_args):
        return True


def client_for(settings, minha=None):
    state = StateStore(settings.state_db)
    store = ChannelStore(settings.channels_file)
    app = create_app(settings, state=state, channel_store=store, minha_client=minha or FakeMinHa())
    return TestClient(app), state, store


def assign(client, profile_id=PROFILE_ID):
    return client.patch(
        f"/api/channels/{CHANNEL_ID}/minha-profile",
        json={"minha_profile_id": profile_id},
    )


def test_assign_persists_across_restart_and_unassigns(settings):
    client, _state, _store = client_for(settings)
    response = assign(client)
    assert response.status_code == 200
    assert response.json()["minha_profile_id"] == PROFILE_ID

    restarted, _state, _store = client_for(settings)
    assert restarted.get("/api/channels").json()[0]["minha_profile_id"] == PROFILE_ID
    cleared = assign(restarted, None)
    assert cleared.status_code == 200 and cleared.json()["minha_profile_id"] is None


def test_invalid_duplicate_unlocked_and_mismatch_are_rejected(settings):
    client, state, _store = client_for(settings)
    assert assign(client, "missing").json()["error"] == "MINHA_PROFILE_NOT_FOUND"
    assert client.get("/api/jobs").json() == []

    assert assign(client).status_code == 200
    client.post("/api/channels", json={"channel_id": SECOND_CHANNEL, "name": "Second"})
    duplicate = client.patch(
        f"/api/channels/{SECOND_CHANNEL}/minha-profile", json={"minha_profile_id": PROFILE_ID},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"] == "MINHA_PROFILE_ALREADY_ASSIGNED"
    assert state.processing_jobs() == []

    unlocked_client, _state, _store = client_for(
        settings, FakeMinHa([profile(expected_tiktok_uid=None, tiktok_account_match="UNLOCKED")]),
    )
    unlocked_client.patch(f"/api/channels/{CHANNEL_ID}/minha-profile", json={"minha_profile_id": None})
    assert assign(unlocked_client).json()["error"] == "UID_UNLOCKED"

    mismatch_client, _state, _store = client_for(
        settings, FakeMinHa([profile(tiktok_uid="other", tiktok_account_match="MISMATCH")]),
    )
    assert assign(mismatch_client).json()["error"] == "ACCOUNT_MISMATCH"


def test_legacy_minha_profile_without_identity_fields_can_be_mapped(settings):
    legacy_profile = {"id": PROFILE_ID, "name": "TN001UK"}
    client, _state, _store = client_for(settings, FakeMinHa([legacy_profile]))
    assigned = assign(client)
    assert assigned.status_code == 200
    assert assigned.json()["minha_profile_id"] == PROFILE_ID
    assert client.get(f"/api/channels/{CHANNEL_ID}/publish-target").json()["status"] == "PROBE_ERROR"


def test_resolver_uses_live_minha_state_and_keeps_mapping_offline(settings):
    client, _state, store = client_for(settings)
    assert assign(client).status_code == 200
    assert client.get(f"/api/channels/{CHANNEL_ID}/publish-target").json()["status"] == "OK"

    renamed = FakeMinHa([profile(name="TN001UK Renamed")])
    live_client = TestClient(create_app(settings, minha_client=renamed))
    resolved = live_client.get(f"/api/channels/{CHANNEL_ID}/publish-target").json()
    assert resolved["status"] == "OK" and resolved["profile"]["name"] == "TN001UK Renamed"

    offline_client = TestClient(create_app(settings, minha_client=FakeMinHa(unavailable=True)))
    assert offline_client.get("/api/minha/profiles").status_code == 503
    assert offline_client.get(f"/api/channels/{CHANNEL_ID}/publish-target").json()["status"] == "MINHA_UNAVAILABLE"
    assert store.list()[0].minha_profile_id == PROFILE_ID

    deleted_client = TestClient(create_app(settings, minha_client=FakeMinHa([])))
    assert deleted_client.get(f"/api/channels/{CHANNEL_ID}/publish-target").json()["status"] == "MINHA_PROFILE_NOT_FOUND"


def test_resolver_reports_all_account_guard_states(settings):
    client, _state, store = client_for(settings)
    assert client.get(f"/api/channels/{CHANNEL_ID}/publish-target").json()["status"] == "MINHA_PROFILE_UNASSIGNED"
    assert client.get(f"/api/channels/{SECOND_CHANNEL}/publish-target").json()["status"] == "CHANNEL_NOT_FOUND"
    store.set_minha_profile(CHANNEL_ID, PROFILE_ID)
    expected = {
        "UNLOCKED": "UID_UNLOCKED",
        "MISMATCH": "ACCOUNT_MISMATCH",
        "NOT_LOGGED_IN": "NOT_LOGGED_IN",
        "NOT_DETECTED": "UID_NOT_DETECTED",
        "ERROR": "PROBE_ERROR",
    }
    for minha_state, status in expected.items():
        fake = FakeMinHa([profile(tiktok_account_match=minha_state)])
        client = TestClient(create_app(settings, minha_client=fake))
        assert client.get(f"/api/channels/{CHANNEL_ID}/publish-target").json()["status"] == status


def test_channel_rename_preserves_mapping_and_job_snapshots_profile_id(settings, tmp_path):
    client, state, _store = client_for(settings)
    assert assign(client).status_code == 200
    renamed = client.patch(f"/api/channels/{CHANNEL_ID}", json={"name": "Renamed"})
    assert renamed.json()["minha_profile_id"] == PROFILE_ID

    event = VideoEvent(VIDEO_ID, CHANNEL_ID, "Video", "", "", f"https://youtu.be/{VIDEO_ID}")
    members = json.loads(settings.team_members_file.read_text(encoding="utf-8"))
    from app.config import TeamMember
    typed_members = [TeamMember(**item) for item in members]
    assert handle_detected_video(
        event, state, Notifier(), {CHANNEL_ID: "Renamed"}, nas_output_root=tmp_path,
        owner_id="member_1", team_members=typed_members, minha_profile_id=PROFILE_ID,
    ) == "NEW"
    assert state.processing_jobs()[0]["minha_profile_id"] == PROFILE_ID


def test_channel_toggles_preserve_mapping_and_failed_patch_does_not_erase_it(settings):
    client, _state, _store = client_for(settings)
    assert assign(client).status_code == 200
    for payload in ({"cut_enabled": False}, {"enabled": False}, {"enabled": True}):
        response = client.patch(f"/api/channels/{CHANNEL_ID}", json=payload)
        assert response.status_code == 200
        assert response.json()["minha_profile_id"] == PROFILE_ID

    failed = assign(client, "missing")
    assert failed.status_code == 404
    assert client.get("/api/channels").json()[0]["minha_profile_id"] == PROFILE_ID


def test_dashboard_contains_live_profile_id_selector(settings):
    client, _state, _store = client_for(settings)
    html = client.get("/").text
    assert "/api/minha/profiles" in html
    assert "/minha-profile" in html
    assert "option.value = profile.id" in html
    assert "MinHa đang không khả dụng; mapping được giữ nguyên" in html


@pytest.mark.skipif(os.getenv("MINHA_ACCEPTANCE") != "1", reason="controlled local acceptance only")
def test_controlled_real_minha_mapping_survives_restart_without_jobs(settings):
    real_channel_id = "UCNiurMpWExWgio2lqldycbA"
    settings.channels_file.write_text(json.dumps([{
        "channel_id": real_channel_id,
        "name": "Controlled existing channel",
        "enabled": False,
        "owner_id": "member_1",
        "cut_enabled": False,
    }]), encoding="utf-8")
    settings = replace(settings, minha_base_url="http://127.0.0.1:18080")
    client = TestClient(create_app(settings))
    profiles = client.get("/api/minha/profiles").json()
    tn001uk = next(item for item in profiles if item["name"] == "TN001UK")
    assert tn001uk["tiktok_account_match"] == "MATCH"

    assigned = client.patch(
        f"/api/channels/{real_channel_id}/minha-profile",
        json={"minha_profile_id": tn001uk["id"]},
    )
    assert assigned.status_code == 200
    assert client.get(f"/api/channels/{real_channel_id}/publish-target").json()["status"] == "OK"
    assert client.get("/api/jobs").json() == []

    restarted = TestClient(create_app(settings))
    assert restarted.get("/api/channels").json()[0]["minha_profile_id"] == tn001uk["id"]
    assert restarted.get(f"/api/channels/{real_channel_id}/publish-target").json()["status"] == "OK"
    assert restarted.get("/api/jobs").json() == []
