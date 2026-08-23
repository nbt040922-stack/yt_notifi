from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings, ensure_user_data_layout, user_data_root
from app.main import create_app


def test_packaged_settings_use_local_appdata_and_seed_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("YT_NOTIFI_PACKAGED", "1")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("YT_NOTIFI_DATA_DIR", raising=False)
    root = user_data_root()
    ensure_user_data_layout(root)
    assert root == (tmp_path / "YT_NOTIFI").resolve()
    assert (root / "config" / "channels.json").is_file()
    monkeypatch.setenv("STATE_DB", str(root / "state" / "custom.db"))
    settings = Settings.from_env()
    assert settings.state_db == root / "state" / "custom.db"
    assert settings.channels_file == root / "config" / "channels.json"


def test_setup_page_and_status_are_available(settings, tmp_path, monkeypatch):
    monkeypatch.setenv("YT_NOTIFI_PACKAGED", "1")
    monkeypatch.setenv("YT_NOTIFI_DATA_DIR", str(tmp_path / "data"))
    app = create_app(settings=settings)
    with TestClient(app) as client:
        assert client.get("/setup").status_code == 200
        payload = client.get("/api/setup/status").json()
        assert payload["silence_cutter"] == "EXTERNAL_OPTIONAL"


def test_setup_persists_and_applies_output_directory(settings, tmp_path, monkeypatch):
    monkeypatch.setenv("YT_NOTIFI_PACKAGED", "1")
    monkeypatch.setenv("YT_NOTIFI_DATA_DIR", str(tmp_path / "data"))
    app = create_app(settings=settings)
    output = tmp_path / "videos"
    with TestClient(app) as client:
        html = client.get("/setup").text
        assert "output-dir" in html
        response = client.post("/api/setup/output", json={"output_dir": str(output)})
        assert response.status_code == 200
        assert response.json()["output_dir"] == str(output)
    assert settings.nas_output_root == output.resolve()


def test_saved_lan_profile_survives_reload_when_environment_is_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("YT_NOTIFI_PACKAGED", "1")
    monkeypatch.setenv("YT_NOTIFI_DATA_DIR", str(tmp_path / "data"))
    root = user_data_root()
    root.mkdir(parents=True)
    (root / ".env").write_text(
        "SILENCE_CUTTER_LAN_URL=http://192.168.88.19:8780\n"
        "SILENCE_CUTTER_LAN_TOKEN=persisted-token\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SILENCE_CUTTER_LAN_URL", "")
    monkeypatch.setenv("SILENCE_CUTTER_LAN_TOKEN", "")
    settings = Settings.from_env()
    assert settings.silence_cutter_lan_url == "http://192.168.88.19:8780"
    assert settings.silence_cutter_lan_token == "persisted-token"
