from pathlib import Path

import pytest

from app.config import Settings


CHANNEL_ID = "UC_x5XG1OV2P6uZZ5FSM9Ttw"
VIDEO_ID = "dQw4w9WgXcQ"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    channels = tmp_path / "channels.json"
    team_members = tmp_path / "team_members.json"
    channels.write_text(
        '[{"channel_id":"UC_x5XG1OV2P6uZZ5FSM9Ttw","name":"Test Channel","enabled":true}]',
        encoding="utf-8",
    )
    team_members.write_text(
        '[{"id":"member_1","display_name":"Member 1","nas_folder":"Member_1"},'
        '{"id":"member_2","display_name":"Member 2","nas_folder":"Member_2"},'
        '{"id":"member_3","display_name":"Member 3","nas_folder":"Member_3"},'
        '{"id":"member_4","display_name":"Member 4","nas_folder":"Member_4"}]',
        encoding="utf-8",
    )
    return Settings(
        telegram_bot_token="secret-token",
        telegram_chat_id="12345",
        channels_file=channels,
        team_members_file=team_members,
        state_db=tmp_path / "state.db",
        processing_control_file=tmp_path / "processing-control.json",
        production_runtime_file=tmp_path / "production-runtime.json",
        local_output_fallback_root=tmp_path / "fallback",
        local_fallback_min_free_gb=0,
        silence_cutter_root=tmp_path / "Silence_cutter",
        enable_background_tasks=False,
    )
