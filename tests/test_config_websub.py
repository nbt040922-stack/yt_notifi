from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from app.config import Channel, enabled_channels, load_channels
from app.websub import HUB_URL, subscribe_all, subscription_data, topic_for
from tests.conftest import CHANNEL_ID


def test_channel_config_loading(settings):
    channels = load_channels(settings.channels_file)
    assert channels == [Channel(CHANNEL_ID, "Test Channel", True)]


def test_disabled_channels_ignored(settings):
    settings.channels_file.write_text(
        f'[{ {"channel_id": CHANNEL_ID, "name": "Off", "enabled": False} }]'.replace("'", '"').replace("False", "false"),
        encoding="utf-8",
    )
    assert enabled_channels(settings.channels_file) == []


def test_invalid_channel_config_rejected(tmp_path: Path):
    path = tmp_path / "channels.json"
    path.write_text('[{"channel_id":"bad","name":"Bad"}]', encoding="utf-8")
    with pytest.raises(ValueError):
        load_channels(path)


def test_topic_generation():
    assert topic_for(CHANNEL_ID) == f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"


def test_subscription_request_construction():
    channel = Channel(CHANNEL_ID, "Test")
    assert subscription_data(channel, "https://tunnel.example/youtube/websub") == {
        "hub.mode": "subscribe",
        "hub.topic": topic_for(CHANNEL_ID),
        "hub.callback": "https://tunnel.example/youtube/websub",
    }


@patch("app.websub.httpx.Client")
def test_subscribe_all_posts_expected_request(client_class, settings):
    response = Mock(status_code=202)
    response.raise_for_status.return_value = None
    client = client_class.return_value.__enter__.return_value
    client.post.return_value = response

    assert subscribe_all(settings) is True
    client.post.assert_called_once_with(
        HUB_URL,
        data=subscription_data(Channel(CHANNEL_ID, "Test Channel"), "https://tunnel.example/youtube/websub"),
    )


@patch("app.telegram.httpx.post")
def test_telegram_success_is_mocked(post):
    from app.telegram import TelegramNotifier

    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"ok": True}
    post.return_value = response
    assert TelegramNotifier("secret", "123").send_message("test") is True


@patch("app.telegram.httpx.post", side_effect=RuntimeError("network down at secret-token"))
def test_telegram_failure_is_caught_without_logging_secret(_post, caplog):
    from app.telegram import TelegramNotifier

    with caplog.at_level("ERROR", logger="yt_notifi"):
        assert TelegramNotifier("secret-token", "123").send_message("test") is False
    assert "secret-token" not in caplog.text


def test_telegram_missing_secret_is_not_serialized(caplog):
    from app.telegram import TelegramNotifier

    with caplog.at_level("ERROR", logger="yt_notifi"):
        assert TelegramNotifier("", "").send_message("test") is False
    assert "bot" not in caplog.text.lower()
