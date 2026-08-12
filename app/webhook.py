from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET

from .models import VideoEvent
from .state import StateStore
from .telegram import TelegramNotifier

logger = logging.getLogger("yt_notifi")
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}


def parse_atom(payload: bytes) -> list[VideoEvent]:
    upper = payload.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError("DTD and entities are not allowed")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError("Malformed Atom XML") from exc
    events = []
    for entry in root.findall("atom:entry", NS):
        video_id = (entry.findtext("yt:videoId", default="", namespaces=NS)).strip()
        channel_id = (entry.findtext("yt:channelId", default="", namespaces=NS)).strip()
        title = (entry.findtext("atom:title", default="", namespaces=NS)).strip()
        published = (entry.findtext("atom:published", default="", namespaces=NS)).strip()
        updated = (entry.findtext("atom:updated", default="", namespaces=NS)).strip()
        if not VIDEO_ID_RE.fullmatch(video_id) or not CHANNEL_ID_RE.fullmatch(channel_id):
            raise ValueError("Invalid video or channel ID")
        link = entry.find("atom:link[@rel='alternate']", NS)
        url = link.get("href", "") if link is not None else ""
        canonical = f"https://www.youtube.com/watch?v={video_id}"
        if not url.startswith(("https://www.youtube.com/watch?", "https://youtu.be/")):
            url = canonical
        events.append(VideoEvent(video_id, channel_id, title, published, updated, url))
    if not events:
        raise ValueError("Atom feed contains no video entries")
    return events


def deliver_notification(event: VideoEvent, state: StateStore, notifier: TelegramNotifier, channel_name: str, sleep=time.sleep) -> None:
    row = state.get_video(event.video_id)
    attempts = row["notification_attempts"] if row else 0
    for delay in (0, 5, 20)[attempts:]:
        if delay:
            logger.info("TELEGRAM_RETRY video_id=%s", event.video_id)
            sleep(delay)
        row = state.get_video(event.video_id)
        sent = notifier.send_video(event, channel_name, row["detected_at"], row["detection_latency_seconds"])
        error = getattr(notifier, "last_error", None)
        if not isinstance(error, str):
            error = None if sent else "delivery failed"
        state.record_notification_attempt(event.video_id, sent, error)
        if sent or getattr(notifier, "last_transient", False) is not True:
            return


def handle_detected_video(
    event: VideoEvent,
    state: StateStore,
    notifier: TelegramNotifier,
    channel_names: dict[str, str],
    *,
    baseline: bool = False,
) -> str:
    if not state.record_event(event, baseline=baseline):
        logger.info("%s video_id=%s", "POLL_DUPLICATE" if event.source == "poll" else "DUPLICATE_VIDEO", event.video_id)
        return "DUPLICATE"
    if baseline:
        return "BASELINE"
    logger.info("NEW_VIDEO video_id=%s", event.video_id)
    deliver_notification(event, state, notifier, channel_names.get(event.channel_id, event.channel_id))
    return "NEW"


def process_event(event: VideoEvent, state: StateStore, notifier: TelegramNotifier, channel_names: dict[str, str]) -> str:
    logger.info("WEBSUB_EVENT video_id=%s", event.video_id)
    return handle_detected_video(event, state, notifier, channel_names)


def resume_notifications(state: StateStore, notifier: TelegramNotifier, channel_names: dict[str, str]) -> None:
    for row in state.pending_notifications():
        event = VideoEvent(
            row["video_id"], row["channel_id"], row["title"], row["published_at"], "",
            f"https://www.youtube.com/watch?v={row['video_id']}",
        )
        deliver_notification(event, state, notifier, channel_names.get(event.channel_id, event.channel_id))
