import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import Channel, Settings, enabled_channels, public_callback
from app.main import create_app
from app.state import StateStore, parse_utc
from app.status import status_snapshot
from app.webhook import deliver_notification, parse_atom, process_event
from app.websub import ensure_subscriptions, renewal_due, request_subscription, topic_for
from tests.conftest import CHANNEL_ID, FIXTURE, VIDEO_ID


def test_requested_then_get_verification_marks_active(settings):
    state = StateStore(settings.state_db)
    channel = Channel(CHANNEL_ID, "Test")
    response = Mock(status_code=202)
    response.raise_for_status.return_value = None
    client = Mock()
    client.post.return_value = response
    callback = public_callback(settings)

    assert request_subscription(channel, callback, state, client, "missing") is True
    assert state.get_subscription(topic_for(CHANNEL_ID))["status"] == "REQUESTED"

    verify = TestClient(create_app(settings, state=state)).get(
        settings.webhook_path,
        params={
            "hub.mode": "subscribe",
            "hub.topic": topic_for(CHANNEL_ID),
            "hub.challenge": "ok",
            "hub.lease_seconds": "1000",
        },
    )
    row = state.get_subscription(topic_for(CHANNEL_ID))
    assert verify.text == "ok"
    assert row["status"] == "ACTIVE"
    assert 990 <= (parse_utc(row["expires_at"]) - parse_utc(row["verified_at"])).total_seconds() <= 1010


def test_renewal_due_calculation():
    now = datetime.now(timezone.utc)
    assert renewal_due({"expires_at": (now + timedelta(seconds=200)).isoformat(), "lease_seconds": 1000}, now)
    assert not renewal_due({"expires_at": (now + timedelta(seconds=300)).isoformat(), "lease_seconds": 1000}, now)


def test_active_subscription_not_unnecessarily_renewed(settings):
    state = StateStore(settings.state_db)
    topic = topic_for(CHANNEL_ID)
    callback = public_callback(settings)
    state.activate_subscription(topic, "subscribe", 10_000, callback)
    client = Mock()

    assert ensure_subscriptions(settings, state, enabled_channels(settings.channels_file), client) == []
    client.post.assert_not_called()


@pytest.mark.parametrize("change", ["expired", "callback"])
def test_expired_or_callback_change_requests_subscription(settings, change):
    state = StateStore(settings.state_db)
    topic = topic_for(CHANNEL_ID)
    callback = public_callback(settings)
    state.activate_subscription(topic, "subscribe", 10_000, "https://old.example/youtube/websub" if change == "callback" else callback)
    if change == "expired":
        with state._connect() as db:
            db.execute("UPDATE subscriptions SET expires_at=? WHERE topic=?", ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), topic))
    response = Mock(status_code=202)
    response.raise_for_status.return_value = None
    client = Mock()
    client.post.return_value = response
    channels = enabled_channels(settings.channels_file)

    results = ensure_subscriptions(settings, state, channels, client)
    assert len(results) == 1
    client.post.assert_called_once()


def test_renewal_failure_preserves_active_and_backoff_is_bounded(settings):
    state = StateStore(settings.state_db)
    topic = topic_for(CHANNEL_ID)
    callback = public_callback(settings)
    state.activate_subscription(topic, "subscribe", 1000, callback)
    with state._connect() as db:
        db.execute("UPDATE subscriptions SET expires_at=? WHERE topic=?", ((datetime.now(timezone.utc) + timedelta(seconds=100)).isoformat(), topic))
    channel = Channel(CHANNEL_ID, "Test")
    client = Mock()
    client.post.side_effect = RuntimeError("down")

    assert request_subscription(channel, callback, state, client, "renewal") is False
    assert state.get_subscription(topic)["status"] == "ACTIVE"

    now = datetime.now(timezone.utc)
    for _ in range(5):
        state.record_subscription_failure(topic, "down", now)
    retry = parse_utc(state.get_subscription(topic)["next_retry_at"])
    assert retry == now + timedelta(minutes=30)


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


def test_telegram_transient_retries_then_succeeds(settings):
    state = StateStore(settings.state_db)
    event = parse_atom(FIXTURE.read_bytes())[0]
    state.record_event(event)
    notifier = FakeNotifier([False, False, True])
    sleeps = []

    deliver_notification(event, state, notifier, "Test", sleep=sleeps.append)
    row = state.get_video(VIDEO_ID)
    assert row["notification_attempts"] == 3
    assert row["notification_sent"] == 1
    assert sleeps == [5, 20]


def test_telegram_permanent_failure_stops_and_success_never_resends(settings):
    state = StateStore(settings.state_db)
    event = parse_atom(FIXTURE.read_bytes())[0]
    permanent = FakeNotifier([False], transient=False)
    process_event(event, state, permanent, {CHANNEL_ID: "Test"})
    assert state.get_video(VIDEO_ID)["notification_attempts"] == 1

    successful = FakeNotifier([True])
    process_event(event, state, successful, {CHANNEL_ID: "Test"})
    assert state.get_video(VIDEO_ID)["notification_attempts"] == 1


def test_phase1_database_migration_preserves_data(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        db.executescript(
            """CREATE TABLE videos (video_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL,
               title TEXT NOT NULL, published_at TEXT NOT NULL, first_seen_at TEXT NOT NULL,
               last_seen_at TEXT NOT NULL, notification_sent INTEGER NOT NULL DEFAULT 0);
               CREATE TABLE subscriptions (topic TEXT PRIMARY KEY, mode TEXT NOT NULL,
               verified_at TEXT NOT NULL, lease_seconds INTEGER);"""
        )
        db.execute("INSERT INTO videos VALUES (?,?,?,?,?,?,?)", (VIDEO_ID, CHANNEL_ID, "old", "2026-01-01", "2026-01-01", "2026-01-01", 1))
        db.execute("INSERT INTO subscriptions VALUES (?,?,?,?)", (topic_for(CHANNEL_ID), "subscribe", "2026-01-01T00:00:00+00:00", 1000))

    state = StateStore(path)
    assert state.get_video(VIDEO_ID)["notification_sent"] == 1
    assert state.get_video(VIDEO_ID)["notification_attempts"] == 1
    assert state.get_video(VIDEO_ID)["detected_at"] == "2026-01-01"
    assert state.get_subscription(topic_for(CHANNEL_ID))["status"] == "ACTIVE"


@pytest.mark.parametrize(
    "url",
    ["http://public.example", "https://localhost", "https://127.0.0.1", "https://host.example/youtube/websub"],
)
def test_public_callback_validation_rejects_unsafe_urls(settings, url):
    bad = Settings(**{**settings.__dict__, "public_callback_url": url})
    with pytest.raises(ValueError):
        public_callback(bad)


def test_status_snapshot_has_no_secrets(settings):
    snapshot = status_snapshot(settings, StateStore(settings.state_db), True, True)
    serialized = str(snapshot)
    assert "secret-token" not in serialized
    assert "12345" not in serialized


@pytest.mark.parametrize("status,transient", [(500, True), (429, True), (401, False), (403, False)])
def test_telegram_http_failure_classification(status, transient):
    from app.telegram import TelegramNotifier

    notifier = TelegramNotifier("secret", "123")
    response = Mock(status_code=status)
    with patch("app.telegram.httpx.post", return_value=response):
        assert notifier.send_message("test") is False
    assert notifier.last_transient is transient
