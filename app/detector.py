from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from .jobs import create_processing_job
from .models import VideoEvent
from .state import StateStore
from .telegram import TelegramNotifier

logger = logging.getLogger("yt_notifi")
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def deliver_notification(
    event: VideoEvent,
    state: StateStore,
    notifier: TelegramNotifier,
    channel_name: str,
    sleep=time.sleep,
) -> None:
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
    nas_output_root: Path | None = None,
    create_job: bool = True,
) -> str:
    if not state.record_event(event, baseline=baseline):
        logger.debug("POLL_DUPLICATE video_id=%s", event.video_id)
        return "DUPLICATE"
    if baseline:
        return "BASELINE"
    channel_name = channel_names.get(event.channel_id, event.channel_id)
    if create_job:
        try:
            create_processing_job(state, event, channel_name, nas_output_root)
        except Exception as exc:
            logger.error("JOB_CREATE_FAILED video_id=%s error_type=%s", event.video_id, type(exc).__name__)
    deliver_notification(event, state, notifier, channel_name)
    return "NEW"


def resume_notifications(state: StateStore, notifier: TelegramNotifier, channel_names: dict[str, str]) -> None:
    for row in state.pending_notifications():
        event = VideoEvent(
            row["video_id"],
            row["channel_id"],
            row["title"],
            row["published_at"],
            "",
            f"https://www.youtube.com/watch?v={row['video_id']}",
        )
        deliver_notification(event, state, notifier, channel_names.get(event.channel_id, event.channel_id))
