from __future__ import annotations

import json
import subprocess
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.channel_resolver import ChannelResolveError, ResolvedChannel, resolve_channel
from app.config import load_channels
from app.main import create_app

CHANNEL_ID = "UCaaaaaaaaaaaaaaaaaaaaaa"
OTHER_ID = "UCbbbbbbbbbbbbbbbbbbbbbb"


def test_direct_channel_url_resolves_without_ytdlp(settings):
    result = resolve_channel(settings, f"https://www.youtube.com/channel/{CHANNEL_ID}")
    assert result.channel_id == CHANNEL_ID
    assert result.canonical_url.endswith(CHANNEL_ID)


def test_handle_resolves_with_mocked_ytdlp(settings, tmp_path, monkeypatch):
    executable = tmp_path / "yt-dlp.exe"
    executable.touch()
    settings = replace(settings, ytdlp_path=str(executable))

    def fake_run(command, **kwargs):
        assert command[-1] == "https://youtube.com/@example"
        assert kwargs["timeout"] == 20
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"channel_id": CHANNEL_ID, "channel": "Detected title"}),
            stderr="",
        )

    monkeypatch.setattr("app.channel_resolver.subprocess.run", fake_run)
    result = resolve_channel(settings, "https://youtube.com/@example")
    assert result == ResolvedChannel(
        CHANNEL_ID,
        f"https://www.youtube.com/channel/{CHANNEL_ID}",
        "Detected title",
    )


def test_direct_channel_can_resolve_official_title(settings, tmp_path, monkeypatch):
    executable = tmp_path / "yt-dlp.exe"
    executable.touch()
    settings = replace(settings, ytdlp_path=str(executable))
    monkeypatch.setattr(
        "app.channel_resolver.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, stdout=json.dumps({"channel_id": CHANNEL_ID, "channel": "Official"}), stderr=""
        ),
    )
    result = resolve_channel(
        settings, f"https://www.youtube.com/channel/{CHANNEL_ID}", resolve_title=True
    )
    assert result.title == "Official"


@pytest.mark.parametrize("path", ["@example", "c/example", "user/example"])
def test_supported_alias_urls_use_ytdlp(settings, tmp_path, monkeypatch, path):
    executable = tmp_path / "yt-dlp.exe"
    executable.touch()
    settings = replace(settings, ytdlp_path=str(executable))
    monkeypatch.setattr(
        "app.channel_resolver.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, stdout=json.dumps({"channel_id": CHANNEL_ID}), stderr=""
        ),
    )
    assert resolve_channel(settings, f"https://www.youtube.com/{path}").channel_id == CHANNEL_ID


@pytest.mark.parametrize(
    "url",
    [
        "not a url",
        "http://youtube.com/@example",
        "https://example.com/@example",
        "https://youtube.com/watch?v=x",
        "https://youtube.com/@example?bad=1",
    ],
)
def test_invalid_url_is_rejected(settings, url):
    with pytest.raises(ChannelResolveError):
        resolve_channel(settings, url)


def test_unresolved_channel_is_rejected(settings, tmp_path, monkeypatch):
    executable = tmp_path / "yt-dlp.exe"
    executable.touch()
    settings = replace(settings, ytdlp_path=str(executable))
    monkeypatch.setattr(
        "app.channel_resolver.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 1, stdout="", stderr="private"),
    )
    with pytest.raises(ChannelResolveError):
        resolve_channel(settings, "https://www.youtube.com/@missing")


def test_resolve_api_returns_channel_and_rejects_duplicate(settings):
    client = TestClient(
        create_app(
            settings,
            channel_resolver=lambda _settings, _url: ResolvedChannel(
                OTHER_ID,
                f"https://www.youtube.com/channel/{OTHER_ID}",
                "Detected",
            ),
        )
    )
    resolved = client.post("/api/channels/resolve", json={"url": "https://youtube.com/@new"})
    assert resolved.status_code == 200
    assert resolved.json()["channel_id"] == OTHER_ID
    client.post("/api/channels", json={"channel_id": OTHER_ID, "name": "Internal"})
    duplicate = client.post("/api/channels/resolve", json={"url": "https://youtube.com/@new"})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"] == "CHANNEL_ALREADY_EXISTS"


def test_duplicate_display_names_are_allowed(settings):
    client = TestClient(create_app(settings))
    first_name = client.get("/api/channels").json()[0]["name"]
    response = client.post("/api/channels", json={"channel_id": OTHER_ID, "name": first_name})
    assert response.status_code == 201
    assert response.json()["name"] == first_name


def test_old_channels_json_remains_compatible(settings):
    settings.channels_file.write_text(json.dumps([{"channel_id": CHANNEL_ID}]), encoding="utf-8")
    channel = load_channels(settings.channels_file)[0]
    assert channel.name == CHANNEL_ID
    assert channel.enabled is True


def test_add_button_stays_disabled_until_name_and_resolve(settings):
    html = TestClient(create_app(settings)).get("/").text
    assert 'id="submit-add" type="submit" disabled' in html
    assert "submitAdd.disabled = !(nameInput.value.trim() && resolvedId)" in html
    assert "/api/channels/resolve" in html
    assert "nameInput.value =" not in html
