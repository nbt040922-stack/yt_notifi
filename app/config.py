from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    public_callback_url: str = ""
    webhook_path: str = "/youtube/websub"
    host: str = "127.0.0.1"
    port: int = 8787
    channels_file: Path = ROOT / "config" / "channels.json"
    state_db: Path = ROOT / "state" / "yt_notifi.db"

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(ROOT / ".env")
        path = os.getenv("WEBHOOK_PATH", "/youtube/websub")
        if not path.startswith("/"):
            raise ValueError("WEBHOOK_PATH must start with /")
        return cls(
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            public_callback_url=os.getenv("PUBLIC_CALLBACK_URL", "").rstrip("/"),
            webhook_path=path,
            host=os.getenv("HOST", "127.0.0.1"),
            port=int(os.getenv("PORT", "8787")),
            channels_file=Path(os.getenv("CHANNELS_FILE", ROOT / "config" / "channels.json")),
            state_db=Path(os.getenv("STATE_DB", ROOT / "state" / "yt_notifi.db")),
        )


@dataclass(frozen=True)
class Channel:
    channel_id: str
    name: str
    enabled: bool = True


def load_channels(path: Path) -> list[Channel]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("channels.json must contain a JSON array")
    channels = []
    for item in data:
        channel_id = str(item.get("channel_id", ""))
        if not CHANNEL_ID_RE.fullmatch(channel_id):
            raise ValueError(f"Invalid YouTube channel_id: {channel_id!r}")
        channels.append(Channel(channel_id, str(item.get("name") or channel_id), bool(item.get("enabled", True))))
    return channels


def enabled_channels(path: Path) -> list[Channel]:
    return [channel for channel in load_channels(path) if channel.enabled]
