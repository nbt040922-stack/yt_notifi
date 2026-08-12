from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .config import Settings
from .state import StateStore

logger = logging.getLogger("yt_notifi")
STATE_MAP = {
    "QUEUED": "DOWNLOAD_PENDING",
    "METADATA": "DOWNLOAD_PENDING",
    "DOWNLOADING": "DOWNLOADING",
    "MERGING": "DOWNLOADING",
    "VERIFYING": "DOWNLOADING",
}
BACKOFF_SECONDS = (5, 10, 20, 30, 60)


class DownloadHandoffWorker:
    def __init__(self, settings: Settings, state: StateStore, client=None):
        self.settings = settings
        self.state = state
        self.client = client or httpx.Client(timeout=5)
        parsed = urlsplit(settings.ytdownload_bridge_url)
        self.bridge_url = settings.ytdownload_bridge_url.rstrip("/")
        self.bridge_valid = (
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            and not parsed.username
            and not parsed.password
        )

    def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        for job in self.state.download_jobs_due(now.isoformat()):
            try:
                self._process(job, now)
            except Exception as exc:
                self._pending(job, now, type(exc).__name__)

    def _process(self, job, now: datetime) -> None:
        if not self.bridge_valid:
            return self._pending(job, now, "INVALID_BRIDGE_URL")
        external_id = job["download_external_id"]
        if external_id:
            response = self.client.get(f"{self.bridge_url}/api/download-jobs/{external_id}")
        else:
            work_dir = self._work_dir(job["id"])
            if not work_dir:
                return self._pending(job, now, "PROCESSING_WORK_ROOT_UNAVAILABLE")
            response = self.client.post(
                f"{self.bridge_url}/api/download-jobs",
                json={
                    "handoff_id": str(job["id"]),
                    "video_id": job["video_id"],
                    "video_url": job["video_url"],
                    "channel_name": job["channel_name"],
                    "work_dir": str(work_dir),
                    "final_output_dir": job["output_dir"],
                },
            )
        if response.status_code >= 500:
            return self._pending(job, now, "BRIDGE_UNAVAILABLE")
        if response.status_code >= 400:
            return self._failed(job, f"BRIDGE_HTTP_{response.status_code}")
        self._apply(job, response.json())

    def _work_dir(self, job_id: int) -> Path | None:
        root = self.settings.processing_work_root
        if not root or not root.is_absolute():
            return None
        try:
            work_dir = root / str(job_id)
            work_dir.mkdir(parents=True, exist_ok=True)
            return work_dir
        except OSError:
            return None

    def _apply(self, job, payload: dict) -> None:
        download_state = str(payload.get("state") or "")
        external_id = str(payload.get("external_id") or job["download_external_id"] or "") or None
        if download_state == "DONE":
            exact_path = str(payload.get("downloaded_file_path") or "")
            if not exact_path:
                return self._failed(job, "MISSING_DOWNLOADED_FILE_PATH", download_state)
            return self.state.update_download_job(
                job["id"], status="DOWNLOADED", external_id=external_id,
                download_state=download_state, progress=100, downloaded_file_path=exact_path,
            )
        if download_state in {"FAILED", "CANCELLED"}:
            return self._failed(job, str(payload.get("error") or download_state), download_state, external_id)
        status = STATE_MAP.get(download_state)
        if not status:
            return self._failed(job, "UNKNOWN_DOWNLOAD_STATE", download_state, external_id)
        self.state.update_download_job(
            job["id"], status=status, external_id=external_id, download_state=download_state,
            progress=float(payload.get("progress_percent") or 0),
        )

    def _pending(self, job, now: datetime, error: str) -> None:
        attempts = job["download_attempts"] + 1
        delay = BACKOFF_SECONDS[min(attempts - 1, len(BACKOFF_SECONDS) - 1)]
        self.state.update_download_job(
            job["id"], status="DOWNLOAD_PENDING", external_id=job["download_external_id"],
            download_state=job["download_state"], progress=job["download_progress"],
            download_error=error, attempts=attempts,
            next_attempt_at=(now + timedelta(seconds=delay)).isoformat(),
        )
        logger.info("DOWNLOAD_HANDOFF_PENDING job_id=%s retry_seconds=%s", job["id"], delay)

    def _failed(self, job, error: str, download_state: str | None = None, external_id: str | None = None) -> None:
        self.state.update_download_job(
            job["id"], status="FAILED", external_id=external_id,
            download_state=download_state, progress=job["download_progress"],
            download_error=error[:500],
        )
        logger.warning("DOWNLOAD_HANDOFF_FAILED job_id=%s", job["id"])

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await asyncio.to_thread(self.tick)
            try:
                await asyncio.wait_for(stop.wait(), timeout=2)
            except TimeoutError:
                pass
