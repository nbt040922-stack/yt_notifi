from __future__ import annotations

import asyncio
import json
import subprocess
import threading
import time
from dataclasses import replace
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.channel_resolver import ChannelResolveError, ResolvedChannel
from app.channel_store import ChannelStore
from app.config import load_team_members
from app.main import create_app
from app.models import VideoEvent
from app.poller import ChannelPoller
from app.process_worker import ProcessHandoffWorker
from app.state import StateStore
from tests.conftest import CHANNEL_ID, VIDEO_ID
from tests.test_process_worker import Bridge, Response, payload

IDS = ["UC" + f"{index:022d}" for index in range(1, 60)]
NEW_VIDEO, SECOND_VIDEO, THIRD_VIDEO = "abcdefghijk", "lmnopqrstuv", "wxyzABCDE12"


def resolver(_settings, value):
    if "bad" in value:
        raise ChannelResolveError("Không nhận diện được kênh.")
    index = int(value.rsplit("channel", 1)[-1]) if "channel" in value else 1
    channel_id = IDS[index - 1]
    return ResolvedChannel(channel_id, f"https://www.youtube.com/channel/{channel_id}", f"Official {index}")


def client(settings, custom_resolver=resolver):
    state = StateStore(settings.state_db)
    return TestClient(create_app(settings, state=state, channel_resolver=custom_resolver)), state


def video_payload(*ids):
    return json.dumps({"entries": [{"id": video_id, "title": video_id} for video_id in ids]})


def test_dashboard_has_only_member_tabs_and_member_bulk_add(settings):
    html = client(settings)[0].get("/").text
    assert "/api/team-members" in html and "/api/channels/bulk" in html
    assert "channel-bulk-form" in html and "Cắt tool:" in html
    assert "Notify Channels" not in html and "notify-tab" not in html


def test_existing_main_channel_migrates_to_cut_on(settings):
    store = ChannelStore(settings.channels_file, load_team_members(settings.team_members_file))
    result = store.migrate_notify_channels([])
    saved = json.loads(settings.channels_file.read_text(encoding="utf-8"))[0]
    assert result == {"imported": 0, "conflicts": 0, "unresolved": 0}
    assert store.list()[0].cut_enabled is True and saved["cut_enabled"] is True


def test_new_channel_defaults_cut_off_and_owner_comes_from_tab(settings):
    api, _ = client(settings)
    response = api.post("/api/channels", json={
        "channel_id": IDS[0], "name": "New", "owner_id": "member_3",
    })
    assert response.status_code == 201
    assert response.json()["owner_id"] == "member_3"
    assert response.json()["cut_enabled"] is False


def test_bulk_add_per_member_defaults_off_and_rejects_cross_member_duplicate(settings):
    api, _ = client(settings)
    first = api.post("/api/channels/bulk", json={
        "channels": ["https://youtube.com/@channel1", "https://youtube.com/@channel2"],
        "owner_id": "member_2",
    }).json()
    duplicate = api.post("/api/channels/bulk", json={
        "channels": ["https://youtube.com/@channel1"], "owner_id": "member_4",
    }).json()
    assert (first["added"], first["failed"]) == (2, 0)
    rows = {row["channel_id"]: row for row in api.get("/api/channels").json()}
    assert rows[IDS[0]]["owner_id"] == "member_2" and rows[IDS[0]]["cut_enabled"] is False
    assert duplicate["existing"] == 1 and duplicate["results"][0]["error"]


def test_bulk_validation_and_bounded_resolution(settings):
    settings = replace(settings, notify_resolve_concurrency=4)
    lock, active, peak = threading.Lock(), 0, 0

    def measured(_settings, value):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return resolver(_settings, value)

    api, _ = client(settings, measured)
    assert api.post("/api/channels/bulk", json={"channels": [], "owner_id": "member_1"}).status_code == 400
    assert api.post("/api/channels/bulk", json={"channels": ["x"], "owner_id": "bad"}).status_code == 400
    result = api.post("/api/channels/bulk", json={
        "channels": [f"https://youtube.com/@channel{i}" for i in range(1, 21)],
        "owner_id": "member_1",
    }).json()
    assert result["added"] == 20 and 1 < peak <= 4


def test_legacy_notify_migration_is_safe_and_non_destructive(settings):
    state = StateStore(settings.state_db)
    state.add_notify_channel(CHANNEL_ID, "Conflict", "https://youtube.com/@conflict")
    state.update_notify_channel(CHANNEL_ID, cut_enabled=False, owner_id="member_2")
    state.add_notify_channel(IDS[0], "Imported", "https://youtube.com/@imported")
    state.update_notify_channel(IDS[0], cut_enabled=False, owner_id="member_3")
    state.add_notify_channel(IDS[1], "Unresolved", "https://youtube.com/@unresolved")
    store = ChannelStore(settings.channels_file, load_team_members(settings.team_members_file))
    result = store.migrate_notify_channels(state.notify_channels())
    channels = {channel.channel_id: channel for channel in store.list()}
    assert result == {"imported": 1, "conflicts": 1, "unresolved": 1}
    assert channels[CHANNEL_ID].cut_enabled is True
    assert channels[IDS[0]].owner_id == "member_3" and channels[IDS[0]].cut_enabled is False
    assert IDS[1] not in channels and len(state.notify_channels()) == 3


def test_cut_off_on_flow_snapshots_owner_and_notifies_once(settings, tmp_path):
    executable = tmp_path / "yt-dlp.exe"
    executable.touch()
    nas = tmp_path / "nas"
    nas.mkdir()
    settings = replace(settings, ytdlp_path=str(executable), nas_output_root=nas)
    members = load_team_members(settings.team_members_file)
    store = ChannelStore(settings.channels_file, members)
    store.update(CHANNEL_ID, owner_id="member_2", cut_enabled=False)
    state, notifier = StateStore(settings.state_db), Mock()
    notifier.send_video.return_value = True
    outputs = iter([
        video_payload(VIDEO_ID), video_payload(NEW_VIDEO, VIDEO_ID),
        video_payload(SECOND_VIDEO, NEW_VIDEO), video_payload(THIRD_VIDEO, SECOND_VIDEO),
    ])
    poller = ChannelPoller(
        settings, state, notifier,
        runner=lambda *_a, **_k: subprocess.CompletedProcess([], 0, stdout=next(outputs), stderr=""),
        channel_loader=store.enabled,
        processing_channel_loader=lambda: {
            channel.channel_id for channel in store.enabled() if channel.cut_enabled
        }, team_members=members,
    )
    asyncio.run(poller.poll_once())
    asyncio.run(poller.poll_once())
    assert state.processing_jobs() == []
    store.update(CHANNEL_ID, cut_enabled=True)
    asyncio.run(poller.poll_once())
    store.update(CHANNEL_ID, owner_id="member_4", cut_enabled=False)
    asyncio.run(poller.poll_once())
    jobs = state.processing_jobs()
    assert len(jobs) == 1 and jobs[0]["video_id"] == SECOND_VIDEO
    assert jobs[0]["owner_id"] == "member_2"
    assert jobs[0]["output_dir"] == str(nas / members[1].nas_folder / "Test Channel")
    assert notifier.send_video.call_count == 3


def test_cut_job_waits_for_engine_then_uses_enhanced_handoff(settings, tmp_path):
    state = StateStore(settings.state_db)
    source, output = tmp_path / "downloaded.mp4", tmp_path / "output"
    source.write_bytes(b"video")
    output.mkdir()
    event = VideoEvent(NEW_VIDEO, CHANNEL_ID, "Video", "", "", f"https://youtu.be/{NEW_VIDEO}")
    state.create_processing_job(event, "Channel", str(output), "QUEUED", None, owner_id="member_1")
    job = state.processing_jobs()[0]
    state.update_download_job(
        job["id"], status="DOWNLOADED", external_id="download-1", download_state="DONE",
        downloaded_file_path=str(source),
    )

    class Engine:
        ready = False
        def is_ready(self): return self.ready
        def pause_reason(self): return "SILENCE_ENGINE_DISABLED"

    engine, bridge = Engine(), Bridge([Response(payload("PROCESSING"))])
    worker = ProcessHandoffWorker(settings, state, bridge, engine)
    worker.tick()
    assert bridge.posts == [] and state.processing_job(job["id"])["status"] == "PROCESS_PENDING"
    engine.ready = True
    worker.tick()
    assert bridge.posts[0][1]["enhanced_content_selection"] is True
    assert bridge.posts[0][1]["handoff_id"] == str(job["id"])
