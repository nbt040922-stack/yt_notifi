from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .config import Settings
from .nas_sync_worker import ensure_writable_directory, prepare_fallback
from .state import StateStore, job_handoff_id


logger = logging.getLogger("yt_notifi")
STATE_MAP = {"QUEUED": "PROCESS_PENDING", "PROCESSING": "PROCESSING", "FINALIZING": "PROCESSING"}
BACKOFF_SECONDS = (5, 10, 20, 30, 60)


class ProcessHandoffWorker:
    def __init__(self, settings: Settings, state: StateStore, client=None, control=None):
        self.settings = settings
        self.state = state
        self.client = client or httpx.Client(timeout=5)
        self.control = control
        parsed = urlsplit(settings.silence_cutter_bridge_url)
        self.bridge_url = settings.silence_cutter_bridge_url.rstrip("/")
        self.bridge_valid = (
            parsed.scheme == "http"
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            and not parsed.username and not parsed.password
        )

    def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        for job in self.state.process_jobs_due(now.isoformat()):
            try:
                self._process(job, now)
            except Exception as exc:
                self._pending(job, now, type(exc).__name__)

    def _process(self, job, now: datetime) -> None:
        if not self.bridge_valid:
            return self._pending(job, now, "INVALID_BRIDGE_URL")
        external_id = job["process_external_id"]
        if external_id:
            response = self.client.get(f"{self.bridge_url}/api/process-jobs/{external_id}")
        else:
            if self.control and not self.control.is_ready():
                self.state.pause_process_job(job["id"], self.control.pause_reason())
                return
            try:
                processing_output_dir = self._processing_output_dir(job)
            except RuntimeError as exc:
                return self._pending(job, now, str(exc))
            response = self.client.post(
                f"{self.bridge_url}/api/process-jobs",
                json={
                    "handoff_id": job_handoff_id(job),
                    "source_file": job["downloaded_file_path"],
                    "channel_name": job["channel_name"],
                    "output_dir": str(processing_output_dir),
                    "video_id": job["video_id"],
                    "video_title": job["video_title"],
                    "enhanced_content_selection": True,
                },
            )
        if response.status_code >= 500:
            return self._pending(job, now, "BRIDGE_UNAVAILABLE")
        if response.status_code >= 400:
            try:
                error = str(response.json().get("error") or f"BRIDGE_HTTP_{response.status_code}")
            except Exception:
                error = f"BRIDGE_HTTP_{response.status_code}"
            return self._failed(job, error)
        self._apply(job, response.json())

    def _processing_output_dir(self, job) -> Path:
        existing = job["processing_output_dir"]
        if existing:
            return Path(existing)
        intended = Path(job["intended_output_dir"] or job["output_dir"])
        if ensure_writable_directory(intended):
            self.state.set_processing_route(job["id"], str(intended), "NOT_REQUIRED")
            return intended
        fallback = prepare_fallback(self.settings, job)
        self.state.set_processing_route(job["id"], str(fallback), "PENDING")
        return fallback

    def _apply(self, job, payload: dict) -> None:
        process_state = str(payload.get("state") or "")
        external_id = str(payload.get("external_id") or job["process_external_id"] or "") or None
        if process_state == "DONE":
            exact_files = [str(value) for value in payload.get("processed_files") or [] if str(value)]
            if not exact_files or not all(Path(value).is_file() for value in exact_files):
                return self._failed(job, "MISSING_PROCESSED_FILES", process_state, external_id)
            return self.state.update_process_job(
                job["id"], status="COMPLETED", external_id=external_id,
                process_state=process_state, progress=100,
                processed_file_path=exact_files[0],
                processed_files_json=json.dumps(exact_files, ensure_ascii=False),
            )
        if process_state == "FAILED":
            return self._failed(job, str(payload.get("error") or "PROCESSING_FAILED"), process_state, external_id)
        status = STATE_MAP.get(process_state)
        if not status:
            return self._failed(job, "UNKNOWN_PROCESS_STATE", process_state, external_id)
        self.state.update_process_job(
            job["id"], status=status, external_id=external_id,
            process_state=process_state, progress=float(payload.get("progress_percent") or 0),
        )

    def _pending(self, job, now: datetime, error: str) -> None:
        attempts = job["process_attempts"] + 1
        delay = BACKOFF_SECONDS[min(attempts - 1, len(BACKOFF_SECONDS) - 1)]
        self.state.update_process_job(
            job["id"], status="PROCESS_PENDING", external_id=job["process_external_id"],
            process_state=job["process_state"], progress=job["process_progress"],
            process_error=error, attempts=attempts,
            next_attempt_at=(now + timedelta(seconds=delay)).isoformat(),
        )
        logger.info("PROCESS_HANDOFF_PENDING job_id=%s retry_seconds=%s", job["id"], delay)

    def _failed(self, job, error: str, process_state: str | None = None, external_id: str | None = None) -> None:
        self.state.update_process_job(
            job["id"], status="FAILED", external_id=external_id,
            process_state=process_state, progress=job["process_progress"],
            process_error=error[:500],
        )
        logger.warning("PROCESS_HANDOFF_FAILED job_id=%s", job["id"])

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await asyncio.to_thread(self.tick)
            try:
                await asyncio.wait_for(stop.wait(), timeout=2)
            except TimeoutError:
                pass
