from __future__ import annotations

import logging
import json
import re
import time
from pathlib import Path

from .jobs import create_processing_job
from .models import VideoEvent
from .state import StateStore
from .telegram import TelegramNotifier

logger = logging.getLogger("yt_notifi")
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _processing_output_paths(job) -> list[str]:
    try:
        values = json.loads(job["processed_files_json"] or "null")
    except (TypeError, json.JSONDecodeError):
        values = None
    if isinstance(values, list):
        paths = [str(value) for value in values if str(value)]
        if paths:
            return paths
    fallback = job["processed_file_path"] or job["processing_output_dir"] or job["output_dir"]
    return [str(fallback)] if fallback else []


def deliver_processing_notification(
    job,
    state: StateStore,
    notifier: TelegramNotifier,
    channel_name: str,
    sleep=time.sleep,
) -> None:
    """Notify a cut-enabled channel only after its edit job is complete."""
    if not job:
        return
    row = state.get_video(job["video_id"])
    attempts = row["notification_attempts"] if row else 0
    paths = _processing_output_paths(job)
    for delay in (0, 5, 20)[attempts:]:
        if delay:
            logger.info("TELEGRAM_RETRY video_id=%s", job["video_id"])
            sleep(delay)
        sent = notifier.send_processing_complete(
            job["video_title"], paths, job["channel_name"],
            job["source_channel_id"], job["video_url"],
        )
        error = getattr(notifier, "last_error", None)
        if not isinstance(error, str):
            error = None if sent else "delivery failed"
        state.record_notification_attempt(job["video_id"], sent, error)
        if sent or getattr(notifier, "last_transient", False) is not True:
            return


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
    owner_id: str | None = None,
    team_members=None,
    minha_profile_id: str | None = None,
    defer_notification: bool = False,
    remote_processing: bool = False,
) -> str:
    if not state.record_event(event, baseline=baseline, notification_ready=not defer_notification):
        logger.debug("POLL_DUPLICATE video_id=%s", event.video_id)
        return "DUPLICATE"
    if baseline:
        return "BASELINE"
    channel_name = channel_names.get(event.channel_id, event.channel_id)
    if create_job:
        try:
            create_processing_job(
                state, event, channel_name, owner_id or "", team_members or [], nas_output_root,
                minha_profile_id,
                remote_processing=remote_processing,
            )
        except Exception as exc:
            logger.error("JOB_CREATE_FAILED video_id=%s error_type=%s", event.video_id, type(exc).__name__)
    if not defer_notification:
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


def resume_processed_notifications(
    state: StateStore, notifier: TelegramNotifier, channel_names: dict[str, str], cut_channel_ids: set[str],
) -> None:
    """Release cut-job notifications after a restart if processing already completed."""
    for job in state.processing_jobs():
        if job["source_channel_id"] not in cut_channel_ids or job["status"] not in {"COMPLETED", "DONE"}:
            continue
        video = state.get_video(job["video_id"])
        if not video or video["notification_ready"] or video["notification_sent"]:
            continue
        state.release_notification(job["video_id"])
        deliver_processing_notification(
            job, state, notifier, channel_names.get(job["source_channel_id"], job["channel_name"]),
        )
