from __future__ import annotations

import asyncio
import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.channel_store import ChannelStore
from app.main import create_app
from app.models import VideoEvent
from app.poller import ChannelPoller
from app.state import StateStore
from tests.conftest import CHANNEL_ID, VIDEO_ID

NEW_CHANNEL = "UCaaaaaaaaaaaaaaaaaaaaaa"
NEW_VIDEO = "abcdefghijk"


def completed(video_id=VIDEO_ID, title="Video"):
    payload = json.dumps({"entries": [{"id": video_id, "title": title}]})
    return subprocess.CompletedProcess([], 0, stdout=payload, stderr="")


def api(settings):
    state = StateStore(settings.state_db)
    return TestClient(create_app(settings, state=state)), state


class ProcessingControlStub:
    def __init__(self):
        self.enabled = True

    def snapshot(self):
        return {
            "silence_engine_enabled": self.enabled, "qwen_status": "READY" if self.enabled else "OFF",
            "error": None, "waiting_jobs": 2,
        }

    def request(self, enabled):
        self.enabled = enabled
        return self.snapshot()


def test_dashboard_loads(settings):
    response = TestClient(create_app(settings)).get("/")
    assert response.status_code == 200
    assert "YT_NOTIFI" in response.text
    assert "THÊM HÀNG LOẠT KÊNH" in response.text


def test_dashboard_accepts_lan_host_header(settings):
    response = TestClient(create_app(settings)).get(
        "/", headers={"host": "192.168.1.31:8787"}
    )
    assert response.status_code == 200 and "YT_NOTIFI" in response.text


def test_dashboard_processing_control_api(settings):
    control = ProcessingControlStub()
    client = TestClient(create_app(settings, processing_control=control))
    assert client.get("/api/processing-control").json() == control.snapshot()
    disabled = client.patch(
        "/api/processing-control", json={"silence_engine_enabled": False}
    )
    assert disabled.status_code == 200
    assert disabled.json()["silence_engine_enabled"] is False
    assert client.patch(
        "/api/processing-control", json={"silence_engine_enabled": "false"}
    ).status_code == 400


def test_get_channels_includes_runtime_state(settings):
    client, state = api(settings)
    waiting = client.get("/api/channels").json()[0]
    assert waiting["last_poll_at"] is None
    assert waiting["status"] == "Waiting for first poll"
    state.record_poll_success(CHANNEL_ID, VIDEO_ID)
    item = client.get("/api/channels").json()[0]
    assert item["channel_id"] == CHANNEL_ID
    assert item["latest_seen_video_id"] == VIDEO_ID
    assert item["failures"] == 0
    assert item["status"] == "Healthy"


def test_empty_display_name_rejected(settings):
    client, _ = api(settings)
    assert client.post("/api/channels", json={"channel_id": NEW_CHANNEL}).status_code == 400
    assert client.post("/api/channels", json={"channel_id": NEW_CHANNEL, "name": "   "}).json()["error"] == "INVALID_CHANNEL_NAME"


def test_add_channel_url(settings):
    client, _ = api(settings)
    response = client.post(
        "/api/channels",
        json={"url": f"https://www.youtube.com/channel/{NEW_CHANNEL}", "name": "Kênh mới"},
    )
    assert response.status_code == 201
    assert response.json()["channel_id"] == NEW_CHANNEL
    assert response.json()["name"] == "Kênh mới"


def test_invalid_and_duplicate_channel_rejected(settings):
    client, _ = api(settings)
    invalid = client.post("/api/channels", json={"channel_id": "bad", "name": "Bad"})
    duplicate = client.post("/api/channels", json={"channel_id": CHANNEL_ID, "name": "Duplicate"})
    assert invalid.json()["error"] == "INVALID_CHANNEL_ID"
    assert duplicate.status_code == 409
    assert duplicate.json()["error"] == "CHANNEL_ALREADY_EXISTS"


def test_disable_enable_and_remove(settings):
    client, state = api(settings)
    state.record_poll_success(CHANNEL_ID, VIDEO_ID)
    disabled = client.patch(f"/api/channels/{CHANNEL_ID}", json={"enabled": False}).json()
    assert disabled["status"] == "Disabled"
    enabled = client.patch(f"/api/channels/{CHANNEL_ID}", json={"enabled": True}).json()
    assert enabled["status"] == "Waiting for first poll"
    assert state.get_poll_state(CHANNEL_ID)["initialized"] == 0
    assert client.delete(f"/api/channels/{CHANNEL_ID}").json() == {"status": "removed"}
    assert client.get("/api/channels").json() == []


def test_remove_and_readd_preserve_video_history(settings):
    client, state = api(settings)
    item = VideoEvent(VIDEO_ID, CHANNEL_ID, "Old", "", "", f"https://youtu.be/{VIDEO_ID}")
    assert state.record_event(item, baseline=True) is True
    client.delete(f"/api/channels/{CHANNEL_ID}")
    client.post("/api/channels", json={"channel_id": CHANNEL_ID, "name": "Again"})
    assert state.get_video(VIDEO_ID)["baseline"] == 1
    assert state.get_poll_state(CHANNEL_ID)["initialized"] == 0


def test_atomic_write_and_generation(settings):
    store = ChannelStore(settings.channels_file)
    before = store.generation
    store.add(NEW_CHANNEL, "Mới")
    assert store.generation == before + 1
    assert not settings.channels_file.with_name("channels.json.tmp").exists()
    assert json.loads(settings.channels_file.read_text(encoding="utf-8"))[1]["name"] == "Mới"


def test_concurrent_mutations_do_not_lose_updates(settings):
    store = ChannelStore(settings.channels_file)
    ids = ["UC" + f"{index:022d}" for index in range(12)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda channel_id: store.add(channel_id, "Same name"), ids))
    saved = store.list()
    assert {channel.channel_id for channel in saved} == {CHANNEL_ID, *ids}


def test_malformed_config_is_not_overwritten(settings):
    original = b"{broken json"
    settings.channels_file.write_bytes(original)
    client, _ = api(settings)
    assert client.get("/api/channels").json()["error"] == "CONFIG_INVALID"
    assert client.post("/api/channels", json={"channel_id": NEW_CHANNEL, "name": "New"}).json()["error"] == "CONFIG_INVALID"
    assert settings.channels_file.read_bytes() == original


def test_new_channel_hot_reloads_and_baselines_without_telegram(settings, tmp_path):
    executable = tmp_path / "yt-dlp.exe"
    executable.touch()
    settings = replace(settings, ytdlp_path=str(executable))
    store = ChannelStore(settings.channels_file)
    state = StateStore(settings.state_db)
    notifier = Mock()
    poller = ChannelPoller(settings, state, notifier, channel_loader=store.enabled, runner=lambda *_a, **_k: completed())
    store.add(NEW_CHANNEL, "Mới")
    results = asyncio.run(poller.poll_once())
    assert {channel.channel_id for channel, _ in results} == {CHANNEL_ID, NEW_CHANNEL}
    assert state.get_video(VIDEO_ID)["baseline"] == 1
    notifier.send_video.assert_not_called()


def test_disabled_channel_stops_future_cycles(settings, tmp_path):
    executable = tmp_path / "yt-dlp.exe"
    executable.touch()
    settings = replace(settings, ytdlp_path=str(executable))
    store = ChannelStore(settings.channels_file)
    calls = []
    poller = ChannelPoller(settings, StateStore(settings.state_db), Mock(), channel_loader=store.enabled, runner=lambda command, **_k: calls.append(command) or completed())
    asyncio.run(poller.poll_once())
    store.update(CHANNEL_ID, False)
    asyncio.run(poller.poll_once())
    assert len(calls) == 1


def test_reenable_baselines_videos_uploaded_while_disabled(settings, tmp_path):
    executable = tmp_path / "yt-dlp.exe"
    executable.touch()
    settings = replace(settings, ytdlp_path=str(executable))
    store = ChannelStore(settings.channels_file)
    state = StateStore(settings.state_db)
    notifier = Mock()
    outputs = iter([completed(VIDEO_ID), completed(NEW_VIDEO)])
    poller = ChannelPoller(settings, state, notifier, channel_loader=store.enabled, runner=lambda *_a, **_k: next(outputs))
    asyncio.run(poller.poll_once())
    store.update(CHANNEL_ID, False)
    _, changed = store.update(CHANNEL_ID, True)
    assert changed is True
    state.reset_poll_baseline(CHANNEL_ID)
    asyncio.run(poller.poll_once())
    assert state.get_video(NEW_VIDEO)["baseline"] == 1
    assert state.processing_jobs() == []
    notifier.send_video.assert_not_called()


def test_remove_during_active_poll_does_not_crash(settings, tmp_path):
    executable = tmp_path / "yt-dlp.exe"
    executable.touch()
    settings = replace(settings, ytdlp_path=str(executable))
    store = ChannelStore(settings.channels_file)
    started = threading.Event()
    release = threading.Event()

    def runner(*_args, **_kwargs):
        started.set()
        release.wait(2)
        return completed()

    poller = ChannelPoller(settings, StateStore(settings.state_db), Mock(), channel_loader=store.enabled, runner=runner)

    async def exercise():
        task = asyncio.create_task(poller.poll_once())
        await asyncio.to_thread(started.wait, 2)
        store.remove(CHANNEL_ID)
        release.set()
        await task
        assert await poller.poll_once() == []

    asyncio.run(exercise())
