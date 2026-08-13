from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from urllib.parse import urlsplit

from .config import CHANNEL_ID_RE, Settings, find_ytdlp

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
SUPPORTED_PATH_RE = re.compile(r"^/(?:@[^/]+|c/[^/]+|user/[^/]+|channel/UC[A-Za-z0-9_-]{22})/?$")
DIRECT_CHANNEL_RE = re.compile(r"^/channel/(UC[A-Za-z0-9_-]{22})/?$")


class ChannelResolveError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedChannel:
    channel_id: str
    canonical_url: str
    title: str | None = None


def _validated_url(value: str) -> tuple[str, str | None]:
    url = value.strip()
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.hostname not in YOUTUBE_HOSTS
        or parsed.query
        or parsed.fragment
        or not SUPPORTED_PATH_RE.fullmatch(parsed.path)
    ):
        raise ChannelResolveError("Could not resolve YouTube channel ID.")
    direct = DIRECT_CHANNEL_RE.fullmatch(parsed.path)
    return url, direct.group(1) if direct else None


def resolve_channel(settings: Settings, value: str, *, resolve_title: bool = False) -> ResolvedChannel:
    url, direct_id = _validated_url(value)
    if direct_id and not resolve_title:
        return ResolvedChannel(direct_id, f"https://www.youtube.com/channel/{direct_id}")
    executable = find_ytdlp(settings)
    if not executable:
        if direct_id:
            return ResolvedChannel(direct_id, f"https://www.youtube.com/channel/{direct_id}")
        raise ChannelResolveError("Could not resolve YouTube channel ID.")

    try:
        result = subprocess.run(
            [
                str(executable),
                "--dump-single-json",
                "--flat-playlist",
                "--playlist-end",
                "1",
                "--no-warnings",
                "--skip-download",
                url,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
        data = json.loads(result.stdout) if result.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise ChannelResolveError("Could not resolve YouTube channel ID.") from exc

    channel_id = next(
        (str(data.get(key)) for key in ("channel_id", "uploader_id") if CHANNEL_ID_RE.fullmatch(str(data.get(key, "")))),
        direct_id,
    )
    if not channel_id:
        raise ChannelResolveError("Could not resolve YouTube channel ID.")
    title = next((str(data.get(key)).strip() for key in ("channel", "uploader", "title") if data.get(key)), None)
    return ResolvedChannel(channel_id, f"https://www.youtube.com/channel/{channel_id}", title)
