from __future__ import annotations

import logging
import re
from pathlib import Path

from .models import VideoEvent
from .state import StateStore

logger = logging.getLogger("yt_notifi")
INVALID_WINDOWS_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED_WINDOWS_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def sanitize_folder_name(name: str) -> str:
    safe = INVALID_WINDOWS_NAME.sub("", name).rstrip(" .")
    if not safe:
        return "Channel"
    if safe.split(".", 1)[0].upper() in RESERVED_WINDOWS_NAMES:
        safe += "_"
    return safe


def create_processing_job(
    state: StateStore,
    event: VideoEvent,
    channel_name: str,
    nas_output_root: Path | None,
) -> bool:
    output_dir = nas_output_root / sanitize_folder_name(channel_name) if nas_output_root else None
    status, error = "QUEUED", None
    try:
        if not nas_output_root or not nas_output_root.is_dir():
            raise OSError("NAS root unavailable")
        output_dir.mkdir(exist_ok=True)
    except OSError:
        status, error = "FAILED", "NAS_UNAVAILABLE"

    created = state.create_processing_job(
        event=event,
        channel_name=channel_name,
        output_dir=str(output_dir) if output_dir else "",
        status=status,
        error=error,
    )
    if created:
        logger.info("JOB_%s video_id=%s", status, event.video_id)
    return created
