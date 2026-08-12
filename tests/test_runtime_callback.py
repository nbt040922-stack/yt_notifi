from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from app.callback import ActiveCallback
from app.config import Channel, enabled_channels, validate_public_origin
from app.main import create_app
from app.state import StateStore
from app.status import status_snapshot
from app.websub import HUB_URL, ensure_subscriptions, subscription_data, topic_for
from tests.conftest import CHANNEL_ID

TOKEN = "launcher-only-token"
ORIGIN_A = "https://alpha-test.trycloudflare.com"
ORIGIN_B = "https://beta-test.trycloudflare.com"


def runtime_settings(settings):
    return replace(settings, launcher_runtime_token=TOKEN)


def local_client(app, token=TOKEN):
    return TestClient(
        app,
        client=("127.0.0.1", 50000),
        headers={"X-YT-Notifi-Runtime-Token": token},
    )


def test_runtime_callback_overrides_static(settings):
    callback = ActiveCallback(settings)
    assert callback.callback_url == "https://tunnel.example/youtube/websub"
    assert callback.source == "static"
    assert callback.set_runtime(ORIGIN_A) is True
    assert callback.callback_url == ORIGIN_A + settings.webhook_path
    assert callback.source == "runtime"


def test_static_callback_still_works(settings):
    callback = ActiveCallback(settings)
    assert callback.origin == "https://tunnel.example"
    assert callback.callback_url.count(settings.webhook_path) == 1


@pytest.mark.parametrize(
    "origin",
    [
        "http://alpha.trycloudflare.com",
        "https://alpha.trycloudflare.com/path",
        "https://alpha.trycloudflare.com?query=1",
        "https://alpha.trycloudflare.com#fragment",
        "https://user:pass@alpha.trycloudflare.com",
        "https://localhost",
        "not-a-url",
    ],
)
def test_invalid_runtime_callback_rejected(settings, origin):
    callback = ActiveCallback(settings)
    with pytest.raises(ValueError):
        callback.set_runtime(origin)


def test_runtime_callback_same_value_is_idempotent(settings):
    callback = ActiveCallback(settings)
    assert callback.set_runtime(ORIGIN_A) is True
    assert callback.set_runtime(ORIGIN_A + "/") is False


@patch("app.main.ensure_subscriptions", return_value=[])
def test_internal_endpoint_rejects_non_loopback(ensure, settings):
    app = create_app(runtime_settings(settings))
    response = TestClient(
        app,
        client=("198.51.100.10", 50000),
        headers={"X-YT-Notifi-Runtime-Token": TOKEN},
    ).post("/internal/runtime-callback", json={"public_origin": ORIGIN_A})
    assert response.status_code == 403
    ensure.assert_not_called()


@patch("app.main.ensure_subscriptions", return_value=[])
def test_internal_endpoint_rejects_wrong_token(ensure, settings):
    response = local_client(create_app(runtime_settings(settings)), "wrong").post(
        "/internal/runtime-callback", json={"public_origin": ORIGIN_A}
    )
    assert response.status_code == 403
    ensure.assert_not_called()


@patch("app.main.ensure_subscriptions", return_value=[])
def test_internal_endpoint_rejects_malformed_origin(ensure, settings):
    response = local_client(create_app(runtime_settings(settings))).post(
        "/internal/runtime-callback", json={"public_origin": "https://host.example/path"}
    )
    assert response.status_code == 400
    ensure.assert_not_called()


@patch("app.main.ensure_subscriptions", return_value=[(Mock(), True, "callback_changed")])
def test_changed_callback_refreshes_immediately(ensure, settings):
    response = local_client(create_app(runtime_settings(settings))).post(
        "/internal/runtime-callback", json={"public_origin": ORIGIN_A}
    )
    assert response.status_code == 200
    assert response.json() == {"status": "updated", "changed": True, "requested": 1}
    assert ensure.call_args.kwargs["callback"] == ORIGIN_A + settings.webhook_path


@patch("app.main.ensure_subscriptions", return_value=[])
def test_same_callback_does_not_refresh_twice(ensure, settings):
    client = local_client(create_app(runtime_settings(settings)))
    first = client.post("/internal/runtime-callback", json={"public_origin": ORIGIN_A})
    second = client.post("/internal/runtime-callback", json={"public_origin": ORIGIN_A})
    assert first.json()["changed"] is True
    assert second.json() == {"status": "unchanged", "changed": False, "requested": 0}
    ensure.assert_called_once()


@patch("app.main.ensure_subscriptions", return_value=[])
def test_quick_tunnel_restart_new_url_refreshes(ensure, settings):
    client = local_client(create_app(runtime_settings(settings)))
    client.post("/internal/runtime-callback", json={"public_origin": ORIGIN_A})
    client.post("/internal/runtime-callback", json={"public_origin": ORIGIN_B})
    assert ensure.call_count == 2
    assert ensure.call_args.kwargs["callback"] == ORIGIN_B + settings.webhook_path


@patch("app.main.ensure_subscriptions", return_value=[])
def test_all_enabled_channels_pass_to_refresh(ensure, settings):
    settings.channels_file.write_text(
        json.dumps(
            [
                {"channel_id": CHANNEL_ID, "name": "One", "enabled": True},
                {"channel_id": "UCaaaaaaaaaaaaaaaaaaaaaa", "name": "Two", "enabled": True},
                {"channel_id": "UCbbbbbbbbbbbbbbbbbbbbbb", "name": "Off", "enabled": False},
            ]
        ),
        encoding="utf-8",
    )
    response = local_client(create_app(runtime_settings(settings))).post(
        "/internal/runtime-callback", json={"public_origin": ORIGIN_A}
    )
    assert response.status_code == 200
    channels = ensure.call_args.args[2]
    assert [channel.name for channel in channels] == ["One", "Two"]


def test_one_subscription_failure_does_not_block_others(settings):
    channels = [Channel(CHANNEL_ID, "One"), Channel("UCaaaaaaaaaaaaaaaaaaaaaa", "Two")]
    state = StateStore(settings.state_db)
    accepted = Mock(status_code=202)
    accepted.raise_for_status.return_value = None
    client = Mock()
    client.post.side_effect = [RuntimeError("hub down"), accepted]
    results = ensure_subscriptions(
        settings, state, channels, client, callback=ORIGIN_A + settings.webhook_path, force=True
    )
    assert [(result[0].name, result[1]) for result in results] == [("One", False), ("Two", True)]
    assert client.post.call_count == 2


def test_callback_change_marks_existing_subscription_requested(settings):
    state = StateStore(settings.state_db)
    topic = topic_for(CHANNEL_ID)
    state.activate_subscription(topic, "subscribe", 10_000, ORIGIN_A + settings.webhook_path)
    accepted = Mock(status_code=202)
    accepted.raise_for_status.return_value = None
    client = Mock()
    client.post.return_value = accepted
    results = ensure_subscriptions(
        settings,
        state,
        enabled_channels(settings.channels_file),
        client,
        callback=ORIGIN_B + settings.webhook_path,
    )
    assert results[0][2] == "callback_changed"
    assert state.get_subscription(topic)["status"] == "REQUESTED"
    assert state.get_subscription(topic)["callback_url"] == ORIGIN_B + settings.webhook_path


def test_stale_runtime_file_is_not_loaded_by_backend(settings, tmp_path):
    (tmp_path / "runtime.json").write_text(json.dumps({"callback_origin": ORIGIN_A}), encoding="utf-8")
    callback = ActiveCallback(settings)
    assert callback.source == "static"
    assert callback.origin == "https://tunnel.example"


def test_runtime_state_shape_contains_no_secrets():
    state = {
        "tunnel_url": ORIGIN_A,
        "callback_origin": ORIGIN_A,
        "callback_url": ORIGIN_A + "/youtube/websub",
        "callback_updated_at": "2026-08-12T00:00:00Z",
        "callback_generation": 1,
    }
    serialized = json.dumps(state)
    assert "token" not in serialized.lower()
    assert "chat" not in serialized.lower()


def test_subscription_payload_uses_callback_exactly_once():
    channel = Channel(CHANNEL_ID, "Test")
    callback = validate_public_origin(ORIGIN_A + "/") + "/youtube/websub"
    assert subscription_data(channel, callback)["hub.callback"] == callback
    assert callback.count("/youtube/websub") == 1
    assert HUB_URL.startswith("https://")


def test_status_health_uses_runtime_callback(monkeypatch, settings):
    from app import status

    checked = []
    monkeypatch.setenv("YT_NOTIFI_RUNTIME_CALLBACK", ORIGIN_A + settings.webhook_path)
    monkeypatch.setattr(status.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(status, "health_ok", lambda url: checked.append(url) or True)
    monkeypatch.setattr("sys.argv", ["status"])

    assert status.main() == 0
    assert ORIGIN_A + "/health" in checked


def test_status_ignores_disabled_channel_subscription(monkeypatch, settings):
    state = StateStore(settings.state_db)
    callback = ORIGIN_A + settings.webhook_path
    state.activate_subscription(topic_for(CHANNEL_ID), "subscribe", 10_000, callback)
    state.activate_subscription(topic_for("UCaaaaaaaaaaaaaaaaaaaaaa"), "subscribe", 10_000, ORIGIN_B + settings.webhook_path)
    monkeypatch.setenv("YT_NOTIFI_RUNTIME_CALLBACK", callback)

    snapshot = status_snapshot(settings, state, True, True)

    assert [item["channel_id"] for item in snapshot["subscriptions"]] == [CHANNEL_ID]
    assert snapshot["websub"] == "ACTIVE"
