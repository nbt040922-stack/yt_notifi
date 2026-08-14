from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
TEAM_MEMBER_IDS = ("member_1", "member_2", "member_3", "member_4")


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    host: str = "127.0.0.1"
    port: int = 8787
    channels_file: Path = ROOT / "config" / "channels.json"
    team_members_file: Path = ROOT / "config" / "team_members.json"
    state_db: Path = ROOT / "state" / "yt_notifi.db"
    enable_background_tasks: bool = True
    ytdlp_path: str = ""
    poll_interval_seconds: int = 10
    poll_max_concurrency: int = 3
    notify_resolve_concurrency: int = 6
    nas_output_root: Path | None = None
    local_output_fallback_root: Path = Path(r"F:\ContentOpsFallback")
    local_fallback_min_free_gb: float = 20
    ytdownload_bridge_url: str = "http://127.0.0.1:8790"
    processing_work_root: Path | None = None
    silence_cutter_bridge_url: str = "http://127.0.0.1:8791"
    contentops_cleanup_dry_run: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(ROOT / ".env")
        nas_output_root = os.getenv("NAS_OUTPUT_ROOT", "").strip()
        processing_work_root = os.getenv("PROCESSING_WORK_ROOT", "").strip()
        fallback_root = os.getenv("LOCAL_OUTPUT_FALLBACK_ROOT", "").strip() or r"F:\ContentOpsFallback"
        return cls(
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
            host=os.getenv("YT_NOTIFI_BIND_HOST", os.getenv("HOST", "127.0.0.1")),
            port=int(os.getenv("YT_NOTIFI_PORT", os.getenv("PORT", "8787"))),
            channels_file=Path(os.getenv("CHANNELS_FILE", ROOT / "config" / "channels.json")),
            team_members_file=Path(os.getenv("TEAM_MEMBERS_FILE", ROOT / "config" / "team_members.json")),
            state_db=Path(os.getenv("STATE_DB", ROOT / "state" / "yt_notifi.db")),
            ytdlp_path=os.getenv("YTDLP_PATH", ""),
            poll_interval_seconds=max(1, int(os.getenv("POLL_INTERVAL_SECONDS", "10"))),
            poll_max_concurrency=max(1, int(os.getenv("POLL_MAX_CONCURRENCY", "3"))),
            notify_resolve_concurrency=max(1, min(12, int(os.getenv("NOTIFY_RESOLVE_CONCURRENCY", "6")))),
            nas_output_root=Path(nas_output_root) if nas_output_root else None,
            local_output_fallback_root=Path(fallback_root),
            local_fallback_min_free_gb=max(0, float(os.getenv("LOCAL_FALLBACK_MIN_FREE_GB", "20"))),
            ytdownload_bridge_url=os.getenv("YTDOWNLOAD_BRIDGE_URL", "http://127.0.0.1:8790").rstrip("/"),
            processing_work_root=Path(processing_work_root) if processing_work_root else None,
            silence_cutter_bridge_url=os.getenv("SILENCE_CUTTER_BRIDGE_URL", "http://127.0.0.1:8791").rstrip("/"),
            contentops_cleanup_dry_run=os.getenv(
                "CONTENTOPS_CLEANUP_DRY_RUN", "true"
            ).strip().lower() not in {"false", "0", "no"},
        )


@dataclass(frozen=True)
class Channel:
    channel_id: str
    name: str
    enabled: bool = True
    owner_id: str = "member_1"


@dataclass(frozen=True)
class TeamMember:
    id: str
    display_name: str
    nas_folder: str


def _validate_team_members(data: object) -> list[TeamMember]:
    if not isinstance(data, list) or len(data) != 4:
        raise ValueError("team_members.json must contain exactly 4 members")
    members = [
        TeamMember(
            str(item.get("id") or ""),
            str(item.get("display_name") or "").strip(),
            str(item.get("nas_folder") or "").strip(),
        )
        for item in data
    ]
    if len({member.id for member in members}) != 4:
        raise ValueError("Team member IDs must be unique")
    if tuple(member.id for member in members) != TEAM_MEMBER_IDS:
        raise ValueError("Team member IDs must remain member_1 through member_4")
    if any(not member.display_name or len(member.display_name) > 50 for member in members):
        raise ValueError("Team member display name must contain 1 to 50 characters")
    if any(
        not member.nas_folder
        or len(member.nas_folder) > 80
        or member.nas_folder in {".", ".."}
        or any(character in member.nas_folder for character in '\\/:')
        for member in members
    ):
        raise ValueError("Invalid team member NAS folder")
    return members


def load_team_members(path: Path = ROOT / "config" / "team_members.json") -> list[TeamMember]:
    return _validate_team_members(json.loads(path.read_text(encoding="utf-8")))


def load_channels(path: Path, owner_ids: set[str] | None = None, default_owner: str = "member_1") -> list[Channel]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("channels.json must contain a JSON array")
    channels = []
    for item in data:
        channel_id = str(item.get("channel_id", ""))
        if not CHANNEL_ID_RE.fullmatch(channel_id):
            raise ValueError(f"Invalid YouTube channel_id: {channel_id!r}")
        owner_id = str(item.get("owner_id") or default_owner)
        if owner_ids is not None and owner_id not in owner_ids:
            raise ValueError(f"Invalid owner_id: {owner_id!r}")
        channels.append(Channel(channel_id, str(item.get("name") or channel_id), bool(item.get("enabled", True)), owner_id))
    return channels


def enabled_channels(path: Path) -> list[Channel]:
    return [channel for channel in load_channels(path) if channel.enabled]


def find_ytdlp(settings: Settings) -> Path | None:
    candidates = [settings.ytdlp_path, ROOT / "tools" / "yt-dlp.exe", ROOT / "tools" / "yt-dlp"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate).resolve()
    found = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    return Path(found).resolve() if found else None
