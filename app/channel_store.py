from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from urllib.parse import urlsplit

from .config import CHANNEL_ID_RE, Channel, load_channels

CHANNEL_URL_RE = re.compile(r"^/channel/(UC[A-Za-z0-9_-]{22})/?$")


class ChannelStoreError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def parse_channel_id(value: str) -> str:
    value = value.strip()
    if CHANNEL_ID_RE.fullmatch(value):
        return value
    parsed = urlsplit(value)
    match = CHANNEL_URL_RE.fullmatch(parsed.path)
    if parsed.scheme == "https" and not parsed.username and not parsed.password and parsed.hostname in {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
    } and not parsed.query and not parsed.fragment and match:
        return match.group(1)
    raise ChannelStoreError("INVALID_CHANNEL_ID", "ID kênh YouTube không hợp lệ.")


class ChannelStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._generation = 0

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def _load_unlocked(self) -> list[Channel]:
        if not self.path.exists():
            return []
        try:
            channels = load_channels(self.path)
        except Exception as exc:
            raise ChannelStoreError(
                "CONFIG_INVALID",
                "channels.json bị lỗi; hãy sửa file trước khi thay đổi kênh.",
                500,
            ) from exc
        ids = [channel.channel_id for channel in channels]
        if len(ids) != len(set(ids)):
            raise ChannelStoreError("CONFIG_INVALID", "channels.json chứa Channel ID trùng nhau.", 500)
        return channels

    def list(self) -> list[Channel]:
        with self._lock:
            return self._load_unlocked()

    def enabled(self) -> list[Channel]:
        return [channel for channel in self.list() if channel.enabled]

    def _save_unlocked(self, channels: list[Channel]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        payload = [
            {"channel_id": channel.channel_id, "name": channel.name, "enabled": channel.enabled}
            for channel in channels
        ]
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()
        self._generation += 1

    def add(self, value: str, name: str | None = None, enabled: bool = True) -> Channel:
        channel_id = parse_channel_id(value)
        with self._lock:
            channels = self._load_unlocked()
            if any(channel.channel_id == channel_id for channel in channels):
                raise ChannelStoreError("CHANNEL_ALREADY_EXISTS", "Kênh này đã có trong danh sách theo dõi.", 409)
            display_name = (name or "").strip() or f"Channel {channel_id[:8]}..."
            channel = Channel(channel_id, display_name, enabled)
            self._save_unlocked([*channels, channel])
            return channel

    def update(self, channel_id: str, enabled: bool) -> tuple[Channel, bool]:
        channel_id = parse_channel_id(channel_id)
        with self._lock:
            channels = self._load_unlocked()
            for index, channel in enumerate(channels):
                if channel.channel_id == channel_id:
                    changed_to_enabled = not channel.enabled and enabled
                    updated = Channel(channel.channel_id, channel.name, enabled)
                    channels[index] = updated
                    self._save_unlocked(channels)
                    return updated, changed_to_enabled
        raise ChannelStoreError("CHANNEL_NOT_FOUND", "Không tìm thấy kênh.", 404)

    def remove(self, channel_id: str) -> Channel:
        channel_id = parse_channel_id(channel_id)
        with self._lock:
            channels = self._load_unlocked()
            for channel in channels:
                if channel.channel_id == channel_id:
                    self._save_unlocked([item for item in channels if item.channel_id != channel_id])
                    return channel
        raise ChannelStoreError("CHANNEL_NOT_FOUND", "Không tìm thấy kênh.", 404)
