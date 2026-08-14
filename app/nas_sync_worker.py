from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Settings
from .jobs import sanitize_folder_name
from .state import StateStore


logger = logging.getLogger("yt_notifi")
BACKOFF_SECONDS = (30, 60, 120, 300, 600)


def ensure_writable_directory(path: Path, *, parents: bool = False) -> bool:
    probe = None
    try:
        path.mkdir(parents=parents, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, prefix=".write-test-", delete=False) as file:
            probe = Path(file.name)
            file.write(b"ok")
            file.flush()
            os.fsync(file.fileno())
        return True
    except OSError:
        return False
    finally:
        if probe:
            probe.unlink(missing_ok=True)


def prepare_fallback(settings: Settings, job) -> Path:
    root = settings.local_output_fallback_root
    path = root / str(job["owner_id"] or "unknown") / sanitize_folder_name(job["channel_name"]) / str(job["id"])
    if not ensure_writable_directory(path, parents=True):
        raise RuntimeError("LOCAL_FALLBACK_UNAVAILABLE")
    try:
        free_bytes = shutil.disk_usage(path).free
    except OSError as exc:
        raise RuntimeError("LOCAL_FALLBACK_UNAVAILABLE") from exc
    if free_bytes < settings.local_fallback_min_free_gb * 1024**3:
        raise RuntimeError("LOCAL_FALLBACK_DISK_LOW")
    return path


class NasSyncWorker:
    def __init__(self, settings: Settings, state: StateStore):
        self.settings = settings
        self.state = state

    def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        for completed in self.state.nas_fallback_cleanup_jobs():
            try:
                self._remove_fallback(Path(completed["processing_output_dir"]), completed["id"])
                self.state.mark_fallback_cleaned(completed["id"], now.isoformat())
            except OSError:
                logger.info("FALLBACK_CLEANUP_RETRY job_id=%s", completed["id"])
        jobs = self.state.nas_sync_jobs_due(now.isoformat())
        if not jobs:
            return
        job = jobs[0]
        try:
            self._sync(job, now)
        except Exception as exc:
            self._retry(job, now, str(exc) or type(exc).__name__)

    def _sync(self, job, now: datetime) -> None:
        intended = Path(job["intended_output_dir"] or job["output_dir"])
        processing = Path(job["processing_output_dir"] or "")
        if not ensure_writable_directory(intended):
            return self._retry(job, now, "NAS_UNAVAILABLE")
        try:
            source_values = json.loads(job["processed_files_json"] or "null")
        except json.JSONDecodeError:
            source_values = None
        if not isinstance(source_values, list) or not source_values:
            return self._retry(job, now, "LOCAL_OUTPUT_MISSING")
        sources = [Path(value) for value in source_values if isinstance(value, str) and value]
        if len(sources) != len(source_values):
            return self._retry(job, now, "LOCAL_OUTPUT_MISSING")
        processing_resolved = processing.resolve()
        if any(
            not source.resolve().is_relative_to(processing_resolved)
            or not source.is_file()
            or source.stat().st_size <= 0
            for source in sources
        ):
            return self._retry(job, now, "LOCAL_OUTPUT_MISSING")

        self.state.update_nas_sync(job["id"], "SYNCING", attempts=job["nas_sync_attempts"])
        destinations = []
        for source in sources:
            destination = intended / source.name
            if destination.exists():
                if destination.is_file() and destination.stat().st_size == source.stat().st_size:
                    destinations.append(destination)
                    continue
                self.state.update_nas_sync(
                    job["id"], "CONFLICT", error=f"DESTINATION_CONFLICT:{source.name}",
                )
                return
            temporary = destination.with_name(destination.name + ".syncing")
            try:
                temporary.unlink(missing_ok=True)
                shutil.copyfile(source, temporary)
                with temporary.open("r+b") as file:
                    os.fsync(file.fileno())
                if temporary.stat().st_size != source.stat().st_size:
                    raise OSError("SIZE_MISMATCH")
                if destination.exists():
                    if destination.is_file() and destination.stat().st_size == source.stat().st_size:
                        destinations.append(destination)
                        continue
                    self.state.update_nas_sync(
                        job["id"], "CONFLICT", error=f"DESTINATION_CONFLICT:{source.name}",
                    )
                    return
                temporary.rename(destination)
            finally:
                temporary.unlink(missing_ok=True)
            if not destination.is_file() or destination.stat().st_size != source.stat().st_size:
                raise OSError("SIZE_MISMATCH")
            destinations.append(destination)

        serialized = json.dumps([str(path) for path in destinations], ensure_ascii=False)
        self.state.update_nas_sync(
            job["id"], "DONE", attempts=job["nas_sync_attempts"], synced_at=now.isoformat(),
            processed_file_path=str(destinations[0]), processed_files_json=serialized,
        )
        try:
            self._remove_fallback(processing, job["id"])
            self.state.mark_fallback_cleaned(job["id"], now.isoformat())
        except OSError:
            logger.info("FALLBACK_CLEANUP_RETRY job_id=%s", job["id"])
        logger.info("NAS_SYNC_DONE job_id=%s files=%s", job["id"], len(destinations))

    def _remove_fallback(self, path: Path, job_id: int) -> None:
        root = self.settings.local_output_fallback_root.resolve()
        resolved = path.resolve()
        if resolved.is_relative_to(root) and resolved.name == str(job_id) and resolved.is_dir():
            shutil.rmtree(resolved)

    def _retry(self, job, now: datetime, error: str) -> None:
        attempts = int(job["nas_sync_attempts"] or 0) + 1
        delay = BACKOFF_SECONDS[min(attempts - 1, len(BACKOFF_SECONDS) - 1)]
        self.state.update_nas_sync(
            job["id"], "FAILED_RETRY", attempts=attempts, error=error[:500],
            next_attempt_at=(now + timedelta(seconds=delay)).isoformat(),
        )
        logger.info("NAS_SYNC_RETRY job_id=%s retry_seconds=%s reason=%s", job["id"], delay, error)

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await asyncio.to_thread(self.tick)
            try:
                await asyncio.wait_for(stop.wait(), timeout=5)
            except TimeoutError:
                pass
