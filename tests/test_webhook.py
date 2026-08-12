from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import VideoEvent
from app.state import StateStore
from app.webhook import parse_atom, process_event
from tests.conftest import CHANNEL_ID, FIXTURE, VIDEO_ID

UNKNOWN_CHANNEL_ID = "UCaaaaaaaaaaaaaaaaaaaaaa"
UNKNOWN_VIDEO_ID = "abcdefghijk"


def mixed_feed() -> bytes:
    unknown_entry = f"""
  <entry>
    <yt:videoId>{UNKNOWN_VIDEO_ID}</yt:videoId>
    <yt:channelId>{UNKNOWN_CHANNEL_ID}</yt:channelId>
    <title>Unknown Channel Video</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v={UNKNOWN_VIDEO_ID}"/>
    <published>2026-08-12T07:00:00+00:00</published>
    <updated>2026-08-12T07:01:00+00:00</updated>
  </entry>
"""
    return FIXTURE.read_text(encoding="utf-8").replace("</feed>", unknown_entry + "</feed>").encode()


def test_health_does_not_expose_secrets(settings):
    response = TestClient(create_app(settings)).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "YT_NOTIFI", "enabled_channels": 1}
    assert "secret-token" not in response.text


def test_websub_challenge(settings):
    topic = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
    response = TestClient(create_app(settings)).get(
        settings.webhook_path,
        params={"hub.mode": "subscribe", "hub.topic": topic, "hub.challenge": "challenge-123", "hub.lease_seconds": "86400"},
    )
    assert response.status_code == 200
    assert response.text == "challenge-123"


def test_websub_rejects_unknown_topic(settings):
    response = TestClient(create_app(settings)).get(
        settings.webhook_path,
        params={"hub.mode": "subscribe", "hub.topic": "https://evil.example/feed", "hub.challenge": "x"},
    )
    assert response.status_code == 400


def test_valid_atom_parsing():
    event = parse_atom(FIXTURE.read_bytes())[0]
    assert event.video_id == VIDEO_ID
    assert event.channel_id == CHANNEL_ID
    assert event.title == "Phase 1 Test Video"
    assert event.url.endswith(VIDEO_ID)


@pytest.mark.parametrize("payload", [b"<not-closed", b"<!DOCTYPE x [<!ENTITY y 'bad'>]><feed>&y;</feed>"])
def test_malformed_or_unsafe_xml(payload):
    with pytest.raises(ValueError):
        parse_atom(payload)


def test_new_video_then_duplicate(settings):
    state = StateStore(settings.state_db)
    notifier = Mock()
    notifier.send_video.return_value = True
    event = parse_atom(FIXTURE.read_bytes())[0]

    process_event(event, state, notifier, {CHANNEL_ID: "Test Channel"})
    process_event(event, state, notifier, {CHANNEL_ID: "Test Channel"})

    notifier.send_video.assert_called_once()
    assert state.get_video(VIDEO_ID)["notification_sent"] == 1


def test_persistent_state_survives_reopen(settings):
    event = parse_atom(FIXTURE.read_bytes())[0]
    assert StateStore(settings.state_db).record_event(event) is True
    assert StateStore(settings.state_db).record_event(event) is False


def test_telegram_failure_does_not_crash_processing(settings):
    state = StateStore(settings.state_db)
    notifier = Mock()
    notifier.send_video.return_value = False
    event = VideoEvent(VIDEO_ID, CHANNEL_ID, "Title", "2026-08-12T00:00:00Z", "", f"https://www.youtube.com/watch?v={VIDEO_ID}")

    process_event(event, state, notifier, {})

    assert state.get_video(VIDEO_ID)["notification_sent"] == 0


def test_post_returns_success_and_deduplicates(settings):
    notifier = Mock()
    notifier.send_video.return_value = True
    client = TestClient(create_app(settings, notifier=notifier))

    assert client.post(settings.webhook_path, content=FIXTURE.read_bytes()).status_code == 202
    assert client.post(settings.webhook_path, content=FIXTURE.read_bytes()).status_code == 202
    notifier.send_video.assert_called_once()
    assert StateStore(settings.state_db).get_video(VIDEO_ID) is not None


def test_unknown_channel_is_ignored(settings, caplog):
    notifier = Mock()
    state = StateStore(settings.state_db)
    client = TestClient(create_app(settings, state=state, notifier=notifier))
    payload = FIXTURE.read_text(encoding="utf-8").replace(CHANNEL_ID, UNKNOWN_CHANNEL_ID).replace(VIDEO_ID, UNKNOWN_VIDEO_ID).encode()

    with caplog.at_level("WARNING", logger="yt_notifi"):
        response = client.post(settings.webhook_path, content=payload)

    assert response.status_code == 202
    assert state.get_video(UNKNOWN_VIDEO_ID) is None
    notifier.send_video.assert_not_called()
    assert "WEBSUB_EVENT_REJECTED_UNKNOWN_CHANNEL" in caplog.text


def test_disabled_channel_is_ignored(settings):
    settings.channels_file.write_text(
        f'[{ {"channel_id": CHANNEL_ID, "name": "Disabled", "enabled": False} }]'.replace("'", '"').replace("False", "false"),
        encoding="utf-8",
    )
    notifier = Mock()
    state = StateStore(settings.state_db)
    response = TestClient(create_app(settings, state=state, notifier=notifier)).post(
        settings.webhook_path, content=FIXTURE.read_bytes()
    )

    assert response.status_code == 202
    assert state.get_video(VIDEO_ID) is None
    notifier.send_video.assert_not_called()


def test_mixed_feed_processes_only_allowed_channel(settings):
    notifier = Mock()
    notifier.send_video.return_value = True
    state = StateStore(settings.state_db)
    response = TestClient(create_app(settings, state=state, notifier=notifier)).post(
        settings.webhook_path, content=mixed_feed()
    )

    assert response.status_code == 202
    assert state.get_video(VIDEO_ID) is not None
    assert state.get_video(UNKNOWN_VIDEO_ID) is None
    notifier.send_video.assert_called_once()


def test_post_rejects_bad_xml(settings):
    response = TestClient(create_app(settings)).post(settings.webhook_path, content=b"bad")
    assert response.status_code == 400
