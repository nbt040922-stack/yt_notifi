from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import threading
import time
from dataclasses import replace
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.channel_resolver import ChannelResolveError, ResolvedChannel
from app.config import Channel
from app.config import load_team_members
from app.main import create_app
from app.poller import ChannelPoller
from app.process_worker import ProcessHandoffWorker
from app.state import StateStore
from tests.conftest import CHANNEL_ID, VIDEO_ID
from tests.test_process_worker import Bridge, Response, payload

IDS = ["UC" + f"{index:022d}" for index in range(1, 505)]
NEW_VIDEO = "abcdefghijk"
SECOND_VIDEO = "lmnopqrstuv"
THIRD_VIDEO = "wxyzABCDE12"


def resolver(_settings, value):
    if "bad" in value:
        raise ChannelResolveError("Could not resolve YouTube channel ID.")
    index = int(value.rsplit("channel", 1)[-1]) if "channel" in value else 1
    channel_id = IDS[index - 1]
    return ResolvedChannel(channel_id, f"https://www.youtube.com/channel/{channel_id}", f"Official {index}")


def client(settings, custom_resolver=resolver):
    state = StateStore(settings.state_db)
    return TestClient(create_app(settings, state=state, channel_resolver=custom_resolver)), state


def test_bulk_add_three_channels_saves_canonical_identity_and_official_name(settings):
    api, state = client(settings)
    response = api.post("/api/notify-channels/bulk", json={
        "channels": [f"https://youtube.com/@channel{i}" for i in range(1, 4)]
    })
    body = response.json()
    assert (body["total"], body["added"], body["existing"], body["failed"]) == (3, 3, 0, 0)
    rows = state.notify_channels()
    assert [(row["channel_id"], row["name"]) for row in rows] == [
        (IDS[index], f"Official {index + 1}") for index in range(3)
    ]
    assert all(row["source_url"].endswith(row["channel_id"]) for row in rows)
    assert all(not row["cut_enabled"] and row["owner_id"] is None for row in rows)


def test_legacy_notify_rows_migrate_to_cut_off(tmp_path):
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as db:
        db.execute("""CREATE TABLE notify_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL, source_url TEXT NOT NULL, created_at TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1)""")
        db.execute(
            "INSERT INTO notify_channels (channel_id,name,source_url,created_at) VALUES (?,?,?,?)",
            (IDS[0], "Legacy", "https://youtube.com/@legacy", "2026-01-01"),
        )
    row = StateStore(database).notify_channels()[0]
    assert row["cut_enabled"] == 0 and row["owner_id"] is None


def test_bulk_mixed_invalid_duplicate_alias_and_existing(settings):
    aliases = {"https://youtube.com/@same", f"https://youtube.com/channel/{IDS[0]}"}

    def aliases_resolver(_settings, value):
        if value == "https://youtube.com/@bad":
            raise ChannelResolveError("bad channel")
        if value in aliases:
            return ResolvedChannel(IDS[0], f"https://www.youtube.com/channel/{IDS[0]}", "Official")
        return resolver(_settings, value)

    api, state = client(settings, aliases_resolver)
    first = api.post("/api/notify-channels/bulk", json={"channels": ["https://youtube.com/@channel2"]})
    assert first.json()["added"] == 1
    response = api.post("/api/notify-channels/bulk", json={"channels": [
        "https://youtube.com/@same", "https://youtube.com/@same",
        f"https://youtube.com/channel/{IDS[0]}", "https://youtube.com/@channel2",
        "https://youtube.com/@bad",
    ]})
    body = response.json()
    assert (body["added"], body["existing"], body["failed"]) == (1, 3, 1)
    assert [item["status"] for item in body["results"]] == [
        "ADDED", "ALREADY_EXISTS", "ALREADY_EXISTS", "ALREADY_EXISTS", "FAILED"
    ]
    assert len(state.notify_channels()) == 2


def test_failed_resolve_not_persisted_and_batch_limits(settings):
    api, state = client(settings)
    assert api.post("/api/notify-channels/bulk", json={"channels": []}).status_code == 400
    assert api.post("/api/notify-channels/bulk", json={"channels": ["x"] * 501}).status_code == 400
    result = api.post("/api/notify-channels/bulk", json={
        "channels": ["https://youtube.com/@bad", "x" * 501, "https://youtube.com/@channel1"]
    })
    assert result.status_code == 200 and result.json()["failed"] == 2
    assert result.json()["added"] == 1
    assert len(state.notify_channels()) == 1


def test_bulk_resolution_has_bounded_concurrency(settings):
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
    response = api.post("/api/notify-channels/bulk", json={
        "channels": [f"https://youtube.com/@channel{i}" for i in range(1, 51)]
    })
    assert response.json()["added"] == 50
    assert 1 < peak <= 4


def test_notify_enable_disable_delete_is_independent(settings):
    api, state = client(settings)
    api.post("/api/notify-channels/bulk", json={"channels": ["https://youtube.com/@channel1"]})
    assert api.patch(f"/api/notify-channels/{IDS[0]}", json={"enabled": False}).json()["enabled"] is False
    assert state.notify_channels(enabled_only=True) == []
    assert api.patch(f"/api/notify-channels/{IDS[0]}", json={"enabled": True}).json()["enabled"] is True
    assert api.delete(f"/api/notify-channels/{IDS[0]}").json() == {"status": "removed"}
    assert api.get("/api/channels").json()[0]["channel_id"] == CHANNEL_ID


def test_cut_toggle_requires_valid_owner_and_persists_selection(settings):
    api, _ = client(settings)
    api.post("/api/notify-channels/bulk", json={"channels": ["https://youtube.com/@channel1"]})
    url = f"/api/notify-channels/{IDS[0]}"
    missing = api.patch(url, json={"cut_enabled": True})
    invalid = api.patch(url, json={"cut_enabled": True, "owner_id": "stranger"})
    assert (missing.status_code, missing.json()["error"]) == (400, "OWNER_REQUIRED")
    assert (invalid.status_code, invalid.json()["error"]) == (400, "INVALID_OWNER")

    selected = api.patch(url, json={"owner_id": "member_2"}).json()
    enabled = api.patch(url, json={"cut_enabled": True}).json()
    disabled = api.patch(url, json={"cut_enabled": False}).json()
    assert selected["cut_enabled"] is False
    assert enabled["cut_enabled"] is True and enabled["owner_id"] == "member_2"
    assert disabled["cut_enabled"] is False and disabled["owner_id"] == "member_2"


def video_payload(*ids):
    return json.dumps({"entries": [{"id": video_id, "title": video_id} for video_id in ids]})


def test_notify_only_baselines_then_notifies_once_without_processing(settings, tmp_path):
    executable = tmp_path / "yt-dlp.exe"
    executable.touch()
    settings = replace(settings, ytdlp_path=str(executable), nas_output_root=tmp_path)
    state, notifier = StateStore(settings.state_db), Mock()
    notifier.send_video.return_value = True
    channel = Channel(IDS[0], "Notify")
    outputs = iter([video_payload(VIDEO_ID), video_payload(NEW_VIDEO, VIDEO_ID), video_payload(NEW_VIDEO, VIDEO_ID)])
    poller = ChannelPoller(
        settings, state, notifier, [channel],
        runner=lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout=next(outputs), stderr=""),
        processing_channel_loader=lambda: set(),
    )
    assert poller.poll_channel(channel)[0][1] == "BASELINE"
    poller.poll_channel(channel)
    poller.poll_channel(channel)
    notifier.send_video.assert_called_once()
    assert state.get_video(NEW_VIDEO)["notification_sent"] == 1
    assert state.processing_jobs() == []


def test_cut_mode_snapshots_owner_and_off_only_affects_future_videos(settings, tmp_path):
    executable = tmp_path / "yt-dlp.exe"
    executable.touch()
    nas = tmp_path / "nas"
    nas.mkdir()
    settings = replace(settings, ytdlp_path=str(executable), nas_output_root=nas)
    state, notifier = StateStore(settings.state_db), Mock()
    notifier.send_video.return_value = True
    state.add_notify_channel(IDS[0], "Notify", "https://youtube.com/@channel1")
    members = load_team_members(settings.team_members_file)

    def channels():
        return [
            Channel(row["channel_id"], row["name"], True, row["owner_id"] or "")
            for row in state.notify_channels(enabled_only=True)
        ]

    def processing_ids():
        return {
            row["channel_id"] for row in state.notify_channels(enabled_only=True)
            if row["cut_enabled"]
        }

    outputs = iter([
        video_payload(VIDEO_ID),
        video_payload(NEW_VIDEO, VIDEO_ID),
        video_payload(SECOND_VIDEO, NEW_VIDEO),
        video_payload(THIRD_VIDEO, SECOND_VIDEO),
    ])
    poller = ChannelPoller(
        settings, state, notifier, runner=lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, stdout=next(outputs), stderr=""
        ), channel_loader=channels, processing_channel_loader=processing_ids,
        team_members=members,
    )
    asyncio.run(poller.poll_once())
    state.update_notify_channel(IDS[0], cut_enabled=True, owner_id="member_1")
    asyncio.run(poller.poll_once())
    state.update_notify_channel(IDS[0], owner_id="member_3")
    asyncio.run(poller.poll_once())
    state.update_notify_channel(IDS[0], cut_enabled=False)
    asyncio.run(poller.poll_once())

    jobs = {row["video_id"]: row for row in state.processing_jobs()}
    assert set(jobs) == {NEW_VIDEO, SECOND_VIDEO}
    assert jobs[NEW_VIDEO]["owner_id"] == "member_1"
    assert jobs[SECOND_VIDEO]["owner_id"] == "member_3"
    assert jobs[NEW_VIDEO]["output_dir"] == str(nas / members[0].nas_folder / "Notify")
    assert jobs[SECOND_VIDEO]["output_dir"] == str(nas / members[2].nas_folder / "Notify")
    assert notifier.send_video.call_count == 3

    source = tmp_path / "downloaded.mp4"
    source.write_bytes(b"video")
    state.update_download_job(
        jobs[NEW_VIDEO]["id"], status="DOWNLOADED", external_id="download-1",
        download_state="DONE", progress=100, downloaded_file_path=str(source),
    )

    class Engine:
        ready = False

        def is_ready(self):
            return self.ready

        def pause_reason(self):
            return "SILENCE_ENGINE_DISABLED"

    engine = Engine()
    bridge = Bridge([Response(payload("PROCESSING"))])
    worker = ProcessHandoffWorker(settings, state, bridge, engine)
    worker.tick()
    assert bridge.posts == []
    assert state.processing_job(jobs[NEW_VIDEO]["id"])["status"] == "PROCESS_PENDING"

    engine.ready = True
    worker.tick()
    assert bridge.posts[0][1]["enhanced_content_selection"] is True
    assert bridge.posts[0][1]["handoff_id"] == str(jobs[NEW_VIDEO]["id"])


def test_disabled_notify_loader_is_not_polled(settings, tmp_path):
    executable = tmp_path / "yt-dlp.exe"
    executable.touch()
    settings = replace(settings, ytdlp_path=str(executable))
    state = StateStore(settings.state_db)
    state.add_notify_channel(IDS[0], "Notify", "https://youtube.com/@channel1")
    state.update_notify_channel(IDS[0], False)
    loader = lambda: [Channel(row["channel_id"], row["name"]) for row in state.notify_channels(enabled_only=True)]
    poller = ChannelPoller(settings, state, Mock(), channel_loader=loader, processing_channel_loader=lambda: set())
    assert asyncio.run(poller.poll_once()) == []


def test_same_channel_in_both_collections_notifies_once_and_keeps_silence_job(settings, tmp_path):
    executable = tmp_path / "yt-dlp.exe"
    executable.touch()
    settings = replace(settings, ytdlp_path=str(executable), nas_output_root=tmp_path)
    state, notifier = StateStore(settings.state_db), Mock()
    notifier.send_video.return_value = True
    channel = Channel(CHANNEL_ID, "Silence")
    outputs = iter([video_payload(VIDEO_ID), video_payload(NEW_VIDEO, VIDEO_ID)])
    poller = ChannelPoller(
        settings, state, notifier, [channel],
        runner=lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, stdout=next(outputs), stderr=""),
        processing_channel_loader=lambda: {CHANNEL_ID},
    )
    poller.poll_channel(channel)
    poller.poll_channel(channel)
    notifier.send_video.assert_called_once()
    assert len(state.processing_jobs()) == 1


def test_dashboard_exposes_two_tabs_and_bulk_results(settings):
    html = client(settings)[0].get("/").text
    assert "/api/team-members" in html and "Notify Channels" in html
    assert 'id="notify-input"' in html and "/api/notify-channels/bulk" in html
    assert "result.added" in html and "result.existing" in html and "result.failed" in html
    assert "Cắt tool: ${channel.cut_enabled ? 'ON' : 'OFF'}" in html
    assert "Member nhận output" in html
