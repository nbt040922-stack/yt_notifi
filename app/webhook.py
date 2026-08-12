from __future__ import annotations

import logging
import re
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


def process_event(event: VideoEvent, state: StateStore, notifier: TelegramNotifier, channel_names: dict[str, str]) -> None:
    logger.info("WEBSUB_EVENT video_id=%s", event.video_id)
    if not state.record_event(event):
        logger.info("DUPLICATE_VIDEO video_id=%s", event.video_id)
        return
    logger.info("NEW_VIDEO video_id=%s", event.video_id)
    sent = notifier.send_video(event, channel_names.get(event.channel_id, event.channel_id))
    state.mark_notification(event.video_id, sent)
