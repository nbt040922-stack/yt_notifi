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
    owner_id: str,
    members=None,
    nas_output_root: Path | None = None,
    minha_profile_id: str | None = None,
    remote_processing: bool = False,
) -> bool:
    member = next((item for item in (members or []) if item.id == owner_id), None)
    if members and not member:
        output_dir = None
        status, error = "FAILED", "OWNER_CONFIG_MISSING"
    else:
        member_root = nas_output_root / member.nas_folder if nas_output_root and member else None
        output_root = member_root if member else nas_output_root
        output_dir = output_root / sanitize_folder_name(channel_name) if output_root else None
        status, error = "QUEUED", None
    if status == "QUEUED" and not output_dir and not remote_processing:
        status, error = "FAILED", "NAS_UNAVAILABLE"
    elif output_dir and output_dir.parent.is_dir():
        try:
            output_dir.mkdir(exist_ok=True)
        except OSError:
            pass

    created = state.create_processing_job(
        event=event,
        channel_name=channel_name,
        owner_id=owner_id,
        output_dir=str(output_dir) if output_dir else "",
        status=status,
        error=error,
        minha_profile_id=minha_profile_id,
    )
    if created:
        logger.info("JOB_%s video_id=%s", status, event.video_id)
    return created
