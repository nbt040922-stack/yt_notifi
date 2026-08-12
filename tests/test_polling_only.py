from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import Channel, Settings, enabled_channels, load_channels
from app.detector import deliver_notification, handle_detected_video
from app.main import create_app
from app.models import VideoEvent
from app.state import StateStore
from app.status import status_snapshot
from tests.conftest import CHANNEL_ID, VIDEO_ID

ROOT = Path(__file__).resolve().parent.parent


def event(video_id: str = VIDEO_ID) -> VideoEvent:
    return VideoEvent(video_id, CHANNEL_ID, "Video", "2026-08-12T00:00:00Z", "", f"https://youtu.be/{video_id}")


def test_health_has_no_secrets_or_public_webhook(settings):
    app = create_app(settings)
    routes = {(route.path, method) for route in app.routes for method in getattr(route, "methods", set())}
    response = TestClient(app).get("/health")
    assert response.json() == {"status": "ok", "service": "YT_NOTIFI", "enabled_channels": 1}
    assert "secret-token" not in response.text
    paths = {path for path, _ in routes}
    assert "/health" in paths
    assert not any("websub" in path or "callback" in path for path in paths)


def test_backend_startup_fails_without_ytdlp(monkeypatch, settings):
    monkeypatch.setattr("app.poller.find_ytdlp", lambda _settings: None)
    app = create_app(replace(settings, enable_background_tasks=True, ytdlp_path=""))
    with pytest.raises(RuntimeError, match="yt-dlp is required"):
        with TestClient(app):
            pass


def test_no_callback_configuration_required(settings):
    assert "public_callback_url" not in settings.__dict__
    assert "webhook_path" not in settings.__dict__
    assert "launcher_runtime_token" not in settings.__dict__


def test_runtime_has_no_websub_or_subscription_loop():
    source = (ROOT / "app" / "main.py").read_text(encoding="utf-8").lower()
    assert "websub" not in source
    assert "subscription" not in source
    assert "cloudflare" not in source


def test_channel_config_loading_and_disabled_filter(settings):
    assert load_channels(settings.channels_file) == [Channel(CHANNEL_ID, "Test Channel", True)]
    settings.channels_file.write_text(
        f'[{ {"channel_id": CHANNEL_ID, "name": "Off", "enabled": False} }]'.replace("'", '"').replace("False", "false"),
        encoding="utf-8",
    )
    assert enabled_channels(settings.channels_file) == []


def test_invalid_channel_config_rejected(tmp_path):
    path = tmp_path / "channels.json"
    path.write_text('[{"channel_id":"bad"}]', encoding="utf-8")
    with pytest.raises(ValueError):
        load_channels(path)


class FakeNotifier:
    def __init__(self, results, transient=True):
        self.results = iter(results)
        self.transient = transient
        self.last_error = None
        self.last_transient = False

    def send_video(self, *_args):
        sent = next(self.results)
        self.last_error = None if sent else "RequestError"
        self.last_transient = self.transient and not sent
        return sent


def test_telegram_transient_retries_and_state_is_preserved(settings):
    state = StateStore(settings.state_db)
    item = event()
    state.record_event(item)
    notifier = FakeNotifier([False, False, True])
    sleeps = []
    deliver_notification(item, state, notifier, "Test", sleep=sleeps.append)
    row = state.get_video(VIDEO_ID)
    assert (row["notification_attempts"], row["notification_sent"], sleeps) == (3, 1, [5, 20])


def test_permanent_failure_does_not_resend_duplicate(settings):
    state = StateStore(settings.state_db)
    notifier = FakeNotifier([False], transient=False)
    assert handle_detected_video(event(), state, notifier, {CHANNEL_ID: "Test"}) == "NEW"
    assert handle_detected_video(event(), state, FakeNotifier([True]), {CHANNEL_ID: "Test"}) == "DUPLICATE"
    assert state.get_video(VIDEO_ID)["notification_attempts"] == 1


def test_legacy_database_with_subscription_table_opens_without_mutation(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.executescript(
            """CREATE TABLE videos (video_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL,
               title TEXT NOT NULL, published_at TEXT NOT NULL, first_seen_at TEXT NOT NULL,
               last_seen_at TEXT NOT NULL, notification_sent INTEGER NOT NULL DEFAULT 0);
               CREATE TABLE subscriptions (topic TEXT PRIMARY KEY, mode TEXT NOT NULL,
               verified_at TEXT NOT NULL, lease_seconds INTEGER);"""
        )
        db.execute("INSERT INTO videos VALUES (?,?,?,?,?,?,?)", (VIDEO_ID, CHANNEL_ID, "old", "2026-01-01", "2026-01-01", "2026-01-01", 1))
        db.execute("INSERT INTO subscriptions VALUES (?,?,?,?)", ("legacy-topic", "subscribe", "2026-01-01", 1000))
    state = StateStore(path)
    assert state.get_video(VIDEO_ID)["notification_sent"] == 1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT topic FROM subscriptions").fetchone()[0] == "legacy-topic"


def test_status_has_no_secrets_or_removed_components(settings):
    snapshot = status_snapshot(settings, StateStore(settings.state_db), True)
    serialized = str(snapshot).lower()
    assert "secret-token" not in serialized
    assert "12345" not in serialized
    assert "websub" not in serialized
    assert "subscription" not in serialized
    assert "callback" not in serialized
    assert snapshot["poll_interval_seconds"] == 10


@pytest.mark.parametrize("status,transient", [(500, True), (429, True), (401, False), (403, False)])
def test_telegram_http_failure_classification(status, transient):
    from app.telegram import TelegramNotifier

    notifier = TelegramNotifier("secret", "123")
    response = Mock(status_code=status)
    with patch("app.telegram.httpx.post", return_value=response):
        assert notifier.send_message("test") is False
    assert notifier.last_transient is transient
