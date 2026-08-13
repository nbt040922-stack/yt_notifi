from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .config import CHANNEL_ID_RE, Channel, Settings, enabled_channels, find_ytdlp
from .detector import VIDEO_ID_RE, handle_detected_video
from .models import VideoEvent
from .state import StateStore
from .telegram import TelegramNotifier

logger = logging.getLogger("yt_notifi")


def channel_videos_url(channel_id: str) -> str:
    if not CHANNEL_ID_RE.fullmatch(channel_id):
        raise ValueError("Invalid YouTube channel ID")
    return f"https://www.youtube.com/channel/{channel_id}/videos"


def parse_ytdlp_result(payload: str, channel: Channel) -> list[VideoEvent]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("yt-dlp returned invalid JSON") from exc
    events = []
    for item in (data.get("entries") or [])[:3]:
        video_id = str(item.get("id") or "")
        if not VIDEO_ID_RE.fullmatch(video_id):
            continue
        timestamp = item.get("timestamp") or item.get("release_timestamp")
        published = datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if isinstance(timestamp, (int, float)) else ""
        events.append(
            VideoEvent(
                video_id=video_id,
                channel_id=channel.channel_id,
                title=str(item.get("title") or video_id),
                published=published,
                updated="",
                url=f"https://www.youtube.com/watch?v={video_id}",
                source="poll",
            )
        )
    return events


def probe_channel(executable: Path, channel: Channel, runner=subprocess.run) -> list[VideoEvent]:
    command = [
        str(executable),
        "--flat-playlist",
        "--playlist-end", "3",
        "--dump-single-json",
        "--no-warnings",
        "--skip-download",
        channel_videos_url(channel.channel_id),
    ]
    result = runner(command, capture_output=True, text=True, timeout=20, check=True)
    return parse_ytdlp_result(result.stdout, channel)


class ChannelPoller:
    def __init__(
        self,
        settings: Settings,
        state: StateStore,
        notifier: TelegramNotifier,
        channels: list[Channel] | None = None,
        runner=subprocess.run,
        channel_loader: Callable[[], list[Channel]] | None = None,
        processing_channel_loader: Callable[[], set[str]] | None = None,
    ):
        self.settings = settings
        self.state = state
        self.notifier = notifier
        self.channel_loader = channel_loader or (lambda: enabled_channels(settings.channels_file) if channels is None else channels)
        self.processing_channel_loader = processing_channel_loader
        self.channels: list[Channel] = []
        self.names: dict[str, str] = {}
        self.refresh_channels()
        self.executable = find_ytdlp(settings)
        self.runner = runner
        self.semaphore = asyncio.Semaphore(settings.poll_max_concurrency)

    def refresh_channels(self) -> None:
        try:
            channels = self.channel_loader()
            processing_ids = (
                self.processing_channel_loader() if self.processing_channel_loader
                else {channel.channel_id for channel in channels}
            )
        except Exception as exc:
            logger.error("CHANNEL_CONFIG_FAILED error_type=%s", type(exc).__name__)
            return
        self.channels = channels
        self.names = {channel.channel_id: channel.name for channel in channels}
        self.processing_ids = processing_ids

    def poll_channel(self, channel: Channel) -> list[tuple[VideoEvent, str]]:
        logger.debug("POLL_START channel_id=%s", channel.channel_id)
        events = probe_channel(self.executable, channel, self.runner)  # type: ignore[arg-type]
        poll_state = self.state.get_poll_state(channel.channel_id)
        baseline = not poll_state or not poll_state["initialized"]
        results = []
        for event in reversed(events):
            classification = handle_detected_video(
                event, self.state, self.notifier, self.names,
                baseline=baseline, nas_output_root=self.settings.nas_output_root,
                create_job=channel.channel_id in self.processing_ids,
            )
            results.append((event, classification))
            if classification == "NEW":
                logger.info("POLL_NEW_VIDEO video_id=%s", event.video_id)
        latest = events[0].video_id if events else None
        recovered = self.state.record_poll_success(channel.channel_id, latest)
        if baseline:
            logger.info("POLL_BASELINE channel_id=%s videos=%s", channel.channel_id, len(events))
        elif recovered:
            logger.info("POLL_RECOVERED channel_id=%s", channel.channel_id)
        else:
            logger.debug("POLL_SUCCESS channel_id=%s", channel.channel_id)
        return results

    async def _poll_guarded(self, channel: Channel) -> list[tuple[VideoEvent, str]]:
        async with self.semaphore:
            try:
                return await asyncio.to_thread(self.poll_channel, channel)
            except Exception as exc:
                delay = self.state.record_poll_failure(
                    channel.channel_id, type(exc).__name__, self.settings.poll_interval_seconds
                )
                logger.warning(
                    "POLL_FAILED channel_id=%s error_type=%s retry_seconds=%s",
                    channel.channel_id, type(exc).__name__, delay,
                )
                return []

    async def poll_once(self, *, force: bool = True) -> list[tuple[Channel, list[tuple[VideoEvent, str]]]]:
        self.refresh_channels()
        if not self.executable:
            logger.warning("POLL_FAILED yt-dlp unavailable")
            return []
        due = [channel for channel in self.channels if force or self.state.poll_due(channel.channel_id)]
        results = await asyncio.gather(*(self._poll_guarded(channel) for channel in due))
        return list(zip(due, results))

    async def run(self, stop: asyncio.Event) -> None:
        if not self.executable:
            logger.error("POLL_FAILED yt-dlp unavailable")
            await stop.wait()
            return
        while not stop.is_set():
            await self.poll_once(force=False)
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.settings.poll_interval_seconds)
            except TimeoutError:
                pass


async def poll_once_command() -> int:
    settings = Settings.from_env()
    state = StateStore(settings.state_db)
    channels = enabled_channels(settings.channels_file)
    poller = ChannelPoller(settings, state, TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id), channels)
    if not poller.executable:
        print("yt-dlp: MISSING")
        return 1
    print(f"yt-dlp: AVAILABLE ({poller.executable})")
    results = await poller.poll_once()
    for channel, entries in results:
        print(f"\n{channel.name} ({channel.channel_id})")
        if not entries:
            print("  No public videos found or probe failed")
        for event, classification in entries:
            print(f"  {classification:9} {event.video_id}  {event.title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(poll_once_command()))
