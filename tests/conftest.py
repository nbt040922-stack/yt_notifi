from pathlib import Path

import pytest

from app.config import Settings


CHANNEL_ID = "UC_x5XG1OV2P6uZZ5FSM9Ttw"
VIDEO_ID = "dQw4w9WgXcQ"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    channels = tmp_path / "channels.json"
    channels.write_text(
        '[{"channel_id":"UC_x5XG1OV2P6uZZ5FSM9Ttw","name":"Test Channel","enabled":true}]',
        encoding="utf-8",
    )
    return Settings(
        telegram_bot_token="secret-token",
        telegram_chat_id="12345",
        channels_file=channels,
        state_db=tmp_path / "state.db",
        enable_background_tasks=False,
    )
