from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .config import Settings
from .state import StateStore


logger = logging.getLogger("yt_notifi")
BACKOFF_SECONDS = (30, 60, 120, 300)


def probe_video(path: Path) -> None:
    executable = shutil.which("ffprobe")
    if not executable:
        raise RuntimeError("FFPROBE_MISSING")
    result = subprocess.run(
        [executable, "-v", "error", "-show_entries", "stream=codec_type:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    if result.returncode:
        raise RuntimeError("FFPROBE_FAILED")
    payload = json.loads(result.stdout)
    if not any(item.get("codec_type") == "video" for item in payload.get("streams") or []):
        raise RuntimeError("VIDEO_STREAM_MISSING")
    if float((payload.get("format") or {}).get("duration") or 0) <= 0:
        raise RuntimeError("INVALID_DURATION")


def validate_workspace(
    root: Path | None, workspace: Path, job_id: int, *protected_roots: Path | None,
) -> Path:
    if not root or not root.is_absolute():
        raise ValueError("UNSAFE_CLEANUP_PATH")
    resolved_root = root.resolve()
    resolved = workspace.resolve()
    drive_root = Path(resolved.anchor)
    if (
        resolved_root == Path(resolved_root.anchor)
        or resolved == resolved_root
        or resolved == drive_root
        or resolved.parent != resolved_root
        or resolved.name != str(job_id)
    ):
        raise ValueError("UNSAFE_CLEANUP_PATH")
    for protected in protected_roots:
        if protected:
            protected = protected.resolve()
            if (
                resolved == protected
                or resolved.is_relative_to(protected)
                or protected.is_relative_to(resolved)
            ):
                raise ValueError("UNSAFE_CLEANUP_PATH")
    return resolved


class CleanupWorker:
    def __init__(
        self, settings: Settings, state: StateStore,
        probe: Callable[[Path], None] = probe_video,
    ) -> None:
        self.settings = settings
        self.state = state
        self.probe = probe

    def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        job = self.state.cleanup_job_due(now.isoformat())
        if not job:
            return
        try:
            self._cleanup(job, now)
        except ValueError as exc:
            self._retry(job, now, "FAILED", str(exc), "CLEANUP_BLOCKED")
        except Exception as exc:
            self._retry(
                job, now, "VERIFY_FAILED", str(exc) or type(exc).__name__, "CLEANUP_PENDING"
            )

    def _cleanup(self, job, now: datetime) -> None:
        if job["process_state"] != "DONE":
            return self._retry(job, now, "PENDING", "PROCESS_NOT_DONE", "CLEANUP_PENDING")
        try:
            outputs = json.loads(job["processed_files_json"] or "null")
        except json.JSONDecodeError:
            outputs = None
        if not isinstance(outputs, list) or not outputs:
            return self._retry(job, now, "PENDING", "MISSING_PROCESSED_FILES", "CLEANUP_PENDING")

        output_root = Path(job["output_dir"]).resolve()
        paths = [Path(value).resolve() for value in outputs if isinstance(value, str) and value]
        if len(paths) != len(outputs):
            return self._retry(job, now, "VERIFY_FAILED", "INVALID_PROCESSED_FILES", "CLEANUP_PENDING")
        logger.info("CLEANUP_VERIFY job_id=%s", job["id"])
        self.state.update_cleanup_job(job["id"], state="VERIFYING")
        for path in paths:
            if not path.is_relative_to(output_root) or not path.is_file() or path.stat().st_size <= 0:
                return self._retry(
                    job, now, "VERIFY_FAILED", "OUTPUT_MISSING_EMPTY_OR_OUTSIDE", "CLEANUP_PENDING"
                )
            self.probe(path)

        root = self.settings.processing_work_root
        workspace = validate_workspace(
            root, (root / str(job["id"])) if root else Path("."), job["id"],
            self.settings.nas_output_root, self.settings.local_output_fallback_root, output_root,
        )
        if not workspace.is_dir():
            return self._retry(job, now, "FAILED", "WORKSPACE_MISSING", "CLEANUP_BLOCKED")
        size = sum(path.stat().st_size for path in workspace.rglob("*") if path.is_file())
        if self.settings.contentops_cleanup_dry_run:
            logger.info("CLEANUP_DRY_RUN job_id=%s path=%s", job["id"], workspace)
            self.state.update_cleanup_job(
                job["id"], state="PENDING",
                next_attempt_at=(now + timedelta(seconds=300)).isoformat(),
            )
            return
        shutil.rmtree(workspace)
        if workspace.exists():
            raise OSError("WORKSPACE_DELETE_FAILED")
        self.state.update_cleanup_job(
            job["id"], state="CLEANED", cleanup_at=now.isoformat(),
            source_deleted=True, bytes_freed=size,
        )
        logger.info("CLEANUP_SUCCESS job_id=%s bytes_freed=%s", job["id"], size)

    def _retry(self, job, now: datetime, state: str, error: str, event: str) -> None:
        attempts = int(job["cleanup_attempts"] or 0) + 1
        delay = BACKOFF_SECONDS[min(attempts - 1, len(BACKOFF_SECONDS) - 1)]
        self.state.update_cleanup_job(
            job["id"], state=state, error=error[:500], attempts=attempts,
            next_attempt_at=(now + timedelta(seconds=delay)).isoformat(),
        )
        logger.info("%s job_id=%s reason=%s", event, job["id"], error)

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await asyncio.to_thread(self.tick)
            try:
                await asyncio.wait_for(stop.wait(), timeout=5)
            except TimeoutError:
                pass
