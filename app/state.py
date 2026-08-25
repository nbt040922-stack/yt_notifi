from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import VideoEvent


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def job_handoff_id(job) -> str:
    attempt = int(job["manual_retry_count"] or 0)
    return str(job["id"]) if not attempt else f"{job['id']}-retry-{attempt}"


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS videos (
                    video_id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    notification_sent INTEGER NOT NULL DEFAULT 0
                )"""
            )
            video_columns = {row[1] for row in db.execute("PRAGMA table_info(videos)")}
            additions = {
                "detected_at": "TEXT",
                "detection_latency_seconds": "REAL",
                "notification_attempts": "INTEGER NOT NULL DEFAULT 0",
                "notification_last_error": "TEXT",
                "detection_source": "TEXT NOT NULL DEFAULT 'poll'",
                "baseline": "INTEGER NOT NULL DEFAULT 0",
                "notification_ready": "INTEGER NOT NULL DEFAULT 1",
            }
            for name, sql_type in additions.items():
                if name not in video_columns:
                    db.execute(f"ALTER TABLE videos ADD COLUMN {name} {sql_type}")
            if "detected_at" not in video_columns:
                db.execute("UPDATE videos SET detected_at=first_seen_at")
            if "notification_attempts" not in video_columns:
                db.execute("UPDATE videos SET notification_attempts=1")

            db.execute(
                """CREATE TABLE IF NOT EXISTS channel_poll_state (
                    channel_id TEXT PRIMARY KEY,
                    initialized INTEGER NOT NULL DEFAULT 0,
                    last_poll_at TEXT,
                    last_success_at TEXT,
                    last_error TEXT,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    latest_seen_video_id TEXT,
                    next_poll_at TEXT
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS notify_channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    cut_enabled INTEGER NOT NULL DEFAULT 0,
                    owner_id TEXT
                )"""
            )
            notify_columns = {row[1] for row in db.execute("PRAGMA table_info(notify_channels)")}
            if "cut_enabled" not in notify_columns:
                db.execute("ALTER TABLE notify_channels ADD COLUMN cut_enabled INTEGER NOT NULL DEFAULT 0")
            if "owner_id" not in notify_columns:
                db.execute("ALTER TABLE notify_channels ADD COLUMN owner_id TEXT")
            db.execute(
                """CREATE TABLE IF NOT EXISTS processing_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    video_id TEXT NOT NULL UNIQUE,
                    video_url TEXT NOT NULL,
                    video_title TEXT NOT NULL,
                    source_channel_id TEXT NOT NULL,
                    channel_name TEXT NOT NULL,
                    owner_id TEXT,
                    minha_profile_id TEXT,
                    output_dir TEXT NOT NULL,
                    intended_output_dir TEXT,
                    processing_output_dir TEXT,
                    nas_sync_state TEXT,
                    nas_sync_attempts INTEGER NOT NULL DEFAULT 0,
                    nas_sync_error TEXT,
                    nas_sync_next_attempt_at TEXT,
                    nas_synced_at TEXT,
                    fallback_cleanup_at TEXT,
                    error TEXT,
                    download_external_id TEXT,
                    download_state TEXT,
                    download_progress REAL NOT NULL DEFAULT 0,
                    downloaded_file_path TEXT,
                    download_error TEXT,
                    updated_at TEXT,
                    download_attempts INTEGER NOT NULL DEFAULT 0,
                    next_download_attempt_at TEXT,
                    process_external_id TEXT,
                    process_state TEXT,
                    process_progress REAL NOT NULL DEFAULT 0,
                    processed_file_path TEXT,
                    processed_files_json TEXT,
                    process_error TEXT,
                    process_attempts INTEGER NOT NULL DEFAULT 0,
                    next_process_attempt_at TEXT,
                    cleanup_state TEXT,
                    cleanup_error TEXT,
                    cleanup_at TEXT,
                    source_deleted INTEGER NOT NULL DEFAULT 0,
                    cleanup_bytes_freed INTEGER,
                    cleanup_attempts INTEGER NOT NULL DEFAULT 0,
                    next_cleanup_attempt_at TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    cancelled_at TEXT,
                    cancel_reason TEXT,
                    manual_retry_count INTEGER NOT NULL DEFAULT 0,
                    last_manual_retry_at TEXT
                )"""
            )
            job_columns = {row[1] for row in db.execute("PRAGMA table_info(processing_jobs)")}
            job_additions = {
                "owner_id": "TEXT",
                "minha_profile_id": "TEXT",
                "intended_output_dir": "TEXT",
                "processing_output_dir": "TEXT",
                "nas_sync_state": "TEXT",
                "nas_sync_attempts": "INTEGER NOT NULL DEFAULT 0",
                "nas_sync_error": "TEXT",
                "nas_sync_next_attempt_at": "TEXT",
                "nas_synced_at": "TEXT",
                "fallback_cleanup_at": "TEXT",
                "download_external_id": "TEXT",
                "download_state": "TEXT",
                "download_progress": "REAL NOT NULL DEFAULT 0",
                "downloaded_file_path": "TEXT",
                "download_error": "TEXT",
                "updated_at": "TEXT",
                "download_attempts": "INTEGER NOT NULL DEFAULT 0",
                "next_download_attempt_at": "TEXT",
                "process_external_id": "TEXT",
                "process_state": "TEXT",
                "process_progress": "REAL NOT NULL DEFAULT 0",
                "processed_file_path": "TEXT",
                "processed_files_json": "TEXT",
                "process_error": "TEXT",
                "process_attempts": "INTEGER NOT NULL DEFAULT 0",
                "next_process_attempt_at": "TEXT",
                "cleanup_state": "TEXT",
                "cleanup_error": "TEXT",
                "cleanup_at": "TEXT",
                "source_deleted": "INTEGER NOT NULL DEFAULT 0",
                "cleanup_bytes_freed": "INTEGER",
                "cleanup_attempts": "INTEGER NOT NULL DEFAULT 0",
                "next_cleanup_attempt_at": "TEXT",
                "cancel_requested": "INTEGER NOT NULL DEFAULT 0",
                "cancelled_at": "TEXT",
                "cancel_reason": "TEXT",
                "manual_retry_count": "INTEGER NOT NULL DEFAULT 0",
                "last_manual_retry_at": "TEXT",
            }
            for name, sql_type in job_additions.items():
                if name not in job_columns:
                    db.execute(f"ALTER TABLE processing_jobs ADD COLUMN {name} {sql_type}")
            db.execute("UPDATE processing_jobs SET updated_at=created_at WHERE updated_at IS NULL")
            db.execute("UPDATE processing_jobs SET intended_output_dir=output_dir WHERE intended_output_dir IS NULL")
            db.execute("UPDATE processing_jobs SET processing_output_dir=output_dir WHERE processing_output_dir IS NULL AND status IN ('COMPLETED','FAILED')")
            db.execute("UPDATE processing_jobs SET nas_sync_state='NOT_REQUIRED' WHERE nas_sync_state IS NULL AND status IN ('COMPLETED','FAILED')")

    def record_event(self, event: VideoEvent, baseline: bool = False, notification_ready: bool = True) -> bool:
        now = datetime.now(timezone.utc)
        published = parse_utc(event.published)
        latency = (now - published).total_seconds() if published else None
        if latency is not None and latency < 0:
            latency = None
        now_text = now.isoformat()
        with self._connect() as db:
            cursor = db.execute(
                """INSERT OR IGNORE INTO videos
                   (video_id, channel_id, title, published_at, first_seen_at, last_seen_at,
                    detected_at, detection_latency_seconds, detection_source, baseline, notification_ready)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (event.video_id, event.channel_id, event.title, event.published, now_text, now_text,
                 now_text, latency, event.source, int(baseline), int(notification_ready)),
            )
            is_new = cursor.rowcount == 1
            if not is_new:
                db.execute("UPDATE videos SET title = ?, last_seen_at = ? WHERE video_id = ?", (event.title, now_text, event.video_id))
        return is_new

    def record_notification_attempt(self, video_id: str, sent: bool, error: str | None) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE videos SET notification_attempts = notification_attempts + 1,
                   notification_sent = ?, notification_last_error = ? WHERE video_id = ?""",
                (int(sent), None if sent else error, video_id),
            )

    def mark_notification(self, video_id: str, sent: bool) -> None:
        self.record_notification_attempt(video_id, sent, None if sent else "delivery failed")

    def get_video(self, video_id: str) -> sqlite3.Row | None:
        with self._connect() as db:
            return db.execute("SELECT * FROM videos WHERE video_id = ?", (video_id,)).fetchone()

    def pending_notifications(self) -> list[sqlite3.Row]:
        with self._connect() as db:
            return list(db.execute("SELECT * FROM videos WHERE notification_ready=1 AND notification_sent=0 AND notification_attempts BETWEEN 1 AND 2"))

    def release_notification(self, video_id: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE videos SET notification_ready=1 WHERE video_id=?", (video_id,))

    def create_processing_job(
        self,
        event: VideoEvent,
        channel_name: str,
        output_dir: str,
        status: str,
        error: str | None,
        owner_id: str | None = None,
        minha_profile_id: str | None = None,
    ) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                """INSERT OR IGNORE INTO processing_jobs
                   (created_at, status, video_id, video_url, video_title, source_channel_id,
                    channel_name, owner_id, minha_profile_id, output_dir,
                    intended_output_dir, error, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (utc_now(), status, event.video_id, event.url, event.title, event.channel_id,
                 channel_name, owner_id, minha_profile_id, output_dir, output_dir, error, utc_now()),
            )
            return cursor.rowcount == 1

    def processing_jobs(self, limit: int | None = None) -> list[sqlite3.Row]:
        query = "SELECT * FROM processing_jobs ORDER BY id DESC"
        parameters = ()
        if limit is not None:
            query += " LIMIT ?"
            parameters = (limit,)
        with self._connect() as db:
            return list(db.execute(query, parameters))

    def processing_job(self, job_id: int) -> sqlite3.Row | None:
        with self._connect() as db:
            return db.execute("SELECT * FROM processing_jobs WHERE id=?", (job_id,)).fetchone()

    def clear_completed_jobs(self) -> int:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            candidates = db.execute(
                """SELECT * FROM processing_jobs
                   WHERE status IN ('DONE','COMPLETED')
                     AND process_state='DONE'
                     AND nas_sync_state IN ('DONE','NOT_REQUIRED')
                     AND cleanup_state='CLEANED'
                     AND source_deleted=1
                     AND cancel_requested=0
                     AND nas_sync_next_attempt_at IS NULL
                     AND next_download_attempt_at IS NULL
                     AND next_process_attempt_at IS NULL
                     AND next_cleanup_attempt_at IS NULL
                     AND (nas_sync_state!='DONE'
                          OR processing_output_dir=intended_output_dir
                          OR fallback_cleanup_at IS NOT NULL)"""
            ).fetchall()
            clearable = []
            for job in candidates:
                try:
                    values = json.loads(job["processed_files_json"] or "null")
                    outputs = [Path(value) for value in values] if isinstance(values, list) else []
                    verified = outputs and all(
                        path.is_file() and path.stat().st_size > 0 for path in outputs
                    )
                    fallback = Path(job["processing_output_dir"] or "")
                    fallback_clean = (
                        job["processing_output_dir"] == job["intended_output_dir"]
                        or not fallback.exists()
                    )
                except (OSError, TypeError, json.JSONDecodeError):
                    verified, fallback_clean = False, False
                if verified and fallback_clean:
                    clearable.append((job["id"],))
            if clearable:
                db.executemany("DELETE FROM processing_jobs WHERE id=?", clearable)
            return len(clearable)

    def clear_failed_job(self, job_id: int) -> str:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT status FROM processing_jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                return "JOB_NOT_FOUND"
            if job["status"] not in {"FAILED", "CANCELLED"}:
                return "JOB_NOT_CLEARABLE"
            db.execute("DELETE FROM processing_jobs WHERE id=?", (job_id,))
            return "CLEARED"

    def clear_failed_jobs(self) -> int:
        """Remove only terminal processing history; active jobs stay untouched."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT id FROM processing_jobs WHERE status IN ('FAILED','CANCELLED')"
            ).fetchall()
            ids = [row["id"] for row in rows]
            if ids:
                marks = ",".join("?" for _ in ids)
                db.execute(f"DELETE FROM processing_jobs WHERE id IN ({marks})", ids)
            return len(ids)

    def clear_history(self) -> int:
        """Clear all non-active processing history in one operation."""
        active = (
            "QUEUED", "DOWNLOAD_PENDING", "DOWNLOADING", "DOWNLOADED",
            "PROCESS_PENDING", "PROCESSING", "CLEANUP_PENDING", "CLEANING",
        )
        marks = ",".join("?" for _ in active)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                f"SELECT id FROM processing_jobs WHERE status NOT IN ({marks})", active
            ).fetchall()
            ids = [row["id"] for row in rows]
            if ids:
                id_marks = ",".join("?" for _ in ids)
                db.execute(f"DELETE FROM processing_jobs WHERE id IN ({id_marks})", ids)
            return len(ids)

    def cancel_processing_job(self, job_id: int) -> str:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT status FROM processing_jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                return "JOB_NOT_FOUND"
            if job["status"] == "CANCELLED":
                return "CANCELLED"
            if job["status"] in {"FAILED", "COMPLETED", "DONE"}:
                return "JOB_NOT_CANCELLABLE"
            now = utc_now()
            db.execute(
                """UPDATE processing_jobs SET status='CANCELLED', cancel_requested=1,
                   cancelled_at=?, cancel_reason='USER_REQUEST', updated_at=?,
                   next_download_attempt_at=NULL, next_process_attempt_at=NULL
                   WHERE id=?""",
                (now, now, job_id),
            )
            return "CANCELLED"

    def retry_processing_job(self, job_id: int, *, source_exists: bool) -> str:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT * FROM processing_jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                return "JOB_NOT_FOUND"
            if job["status"] not in {"FAILED", "CANCELLED"}:
                if job["status"] == "COMPLETED" and job["nas_sync_state"] in {"FAILED_RETRY", "CONFLICT"}:
                    now = utc_now()
                    db.execute(
                        """UPDATE processing_jobs SET nas_sync_state='PENDING', nas_sync_error=NULL,
                           nas_sync_next_attempt_at=NULL, manual_retry_count=manual_retry_count+1,
                           last_manual_retry_at=?, updated_at=? WHERE id=?""",
                        (now, now, job_id),
                    )
                    return "NAS_PENDING"
                if job["status"] == "COMPLETED" and job["nas_sync_state"] == "PENDING":
                    return "JOB_ALREADY_RUNNING"
                if job["status"] in {
                    "QUEUED", "DOWNLOAD_PENDING", "DOWNLOADING", "DOWNLOADED",
                    "PROCESS_PENDING", "PROCESSING",
                }:
                    return "JOB_ALREADY_RUNNING"
                return "JOB_NOT_RETRYABLE"

            now = utc_now()
            common = """cancel_requested=0, cancelled_at=NULL, cancel_reason=NULL, error=NULL,
                        manual_retry_count=manual_retry_count+1, last_manual_retry_at=?, updated_at=?"""
            if source_exists:
                db.execute(
                    f"""UPDATE processing_jobs SET status='PROCESS_PENDING', {common},
                        process_external_id=NULL, process_state=NULL, process_progress=0,
                        processed_file_path=NULL, processed_files_json=NULL, process_error=NULL,
                        next_process_attempt_at=NULL,
                        cleanup_state=NULL, cleanup_error=NULL, cleanup_at=NULL,
                        source_deleted=0, cleanup_bytes_freed=NULL,
                        next_cleanup_attempt_at=NULL WHERE id=?""",
                    (now, now, job_id),
                )
                return "PROCESS_PENDING"
            db.execute(
                f"""UPDATE processing_jobs SET status='QUEUED', {common},
                    download_external_id=NULL, download_state=NULL, download_progress=0,
                    downloaded_file_path=NULL, download_error=NULL,
                    next_download_attempt_at=NULL, process_external_id=NULL, process_state=NULL,
                    process_progress=0, processed_file_path=NULL, processed_files_json=NULL,
                    process_error=NULL, next_process_attempt_at=NULL,
                    cleanup_state=NULL, cleanup_error=NULL,
                    cleanup_at=NULL, source_deleted=0, cleanup_bytes_freed=NULL,
                    next_cleanup_attempt_at=NULL WHERE id=?""",
                (now, now, job_id),
            )
            return "QUEUED"

    def notify_channels(self, *, enabled_only: bool = False) -> list[sqlite3.Row]:
        query = "SELECT * FROM notify_channels"
        if enabled_only:
            query += " WHERE enabled=1"
        with self._connect() as db:
            return list(db.execute(query + " ORDER BY id"))

    def add_notify_channel(self, channel_id: str, name: str, source_url: str) -> tuple[sqlite3.Row, bool]:
        with self._connect() as db:
            cursor = db.execute(
                """INSERT OR IGNORE INTO notify_channels
                   (channel_id, name, source_url, created_at, enabled)
                   VALUES (?, ?, ?, ?, 1)""",
                (channel_id, name, source_url, utc_now()),
            )
            row = db.execute("SELECT * FROM notify_channels WHERE channel_id=?", (channel_id,)).fetchone()
            return row, cursor.rowcount == 1

    def update_notify_channel(
        self,
        channel_id: str,
        enabled: bool | None = None,
        cut_enabled: bool | None = None,
        owner_id: str | None = None,
    ) -> sqlite3.Row | None:
        with self._connect() as db:
            cursor = db.execute(
                """UPDATE notify_channels SET enabled=COALESCE(?, enabled),
                   cut_enabled=COALESCE(?, cut_enabled), owner_id=COALESCE(?, owner_id)
                   WHERE channel_id=?""",
                (
                    int(enabled) if enabled is not None else None,
                    int(cut_enabled) if cut_enabled is not None else None,
                    owner_id,
                    channel_id,
                ),
            )
            if not cursor.rowcount:
                return None
            return db.execute("SELECT * FROM notify_channels WHERE channel_id=?", (channel_id,)).fetchone()

    def delete_notify_channel(self, channel_id: str) -> bool:
        with self._connect() as db:
            return db.execute("DELETE FROM notify_channels WHERE channel_id=?", (channel_id,)).rowcount == 1

    def download_jobs_due(self, now: str) -> list[sqlite3.Row]:
        with self._connect() as db:
            return list(db.execute(
                """SELECT * FROM processing_jobs
                   WHERE status IN ('QUEUED', 'DOWNLOAD_PENDING', 'DOWNLOADING')
                     AND cancel_requested=0
                     AND (next_download_attempt_at IS NULL OR next_download_attempt_at <= ?)
                   ORDER BY id""",
                (now,),
            ))

    def update_download_job(
        self,
        job_id: int,
        *,
        status: str,
        external_id: str | None = None,
        download_state: str | None = None,
        progress: float = 0,
        downloaded_file_path: str | None = None,
        video_title: str | None = None,
        download_error: str | None = None,
        attempts: int = 0,
        next_attempt_at: str | None = None,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE processing_jobs SET status=?,
                   download_external_id=COALESCE(?, download_external_id), download_state=?,
                   download_progress=?, downloaded_file_path=COALESCE(?, downloaded_file_path),
                   video_title=COALESCE(?, video_title),
                   download_error=?, updated_at=?, download_attempts=?, next_download_attempt_at=?
                   WHERE id=? AND cancel_requested=0 AND status!='CANCELLED'""",
                (status, external_id, download_state, progress, downloaded_file_path,
                 video_title, download_error, utc_now(), attempts, next_attempt_at, job_id),
            )

    def process_jobs_due(self, now: str) -> list[sqlite3.Row]:
        with self._connect() as db:
            return list(db.execute(
                """SELECT * FROM processing_jobs
                   WHERE status IN ('DOWNLOADED', 'PROCESS_PENDING', 'PROCESSING')
                     AND cancel_requested=0
                     AND (next_process_attempt_at IS NULL OR next_process_attempt_at <= ?)
                   ORDER BY id""",
                (now,),
            ))

    def update_process_job(
        self,
        job_id: int,
        *,
        status: str,
        external_id: str | None = None,
        process_state: str | None = None,
        progress: float = 0,
        processed_file_path: str | None = None,
        processed_files_json: str | None = None,
        process_error: str | None = None,
        attempts: int = 0,
        next_attempt_at: str | None = None,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE processing_jobs SET status=?,
                   process_external_id=COALESCE(?, process_external_id), process_state=?,
                   process_progress=?, processed_file_path=COALESCE(?, processed_file_path),
                   processed_files_json=COALESCE(?, processed_files_json),
                   process_error=?, updated_at=?, process_attempts=?, next_process_attempt_at=?
                   WHERE id=? AND cancel_requested=0 AND status!='CANCELLED'""",
                (status, external_id, process_state, progress, processed_file_path, processed_files_json,
                 process_error, utc_now(), attempts, next_attempt_at, job_id),
            )

    def pause_process_job(self, job_id: int, reason: str) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE processing_jobs SET status='PROCESS_PENDING', process_error=?,
                   next_process_attempt_at=NULL, updated_at=?
                   WHERE id=? AND cancel_requested=0 AND status!='CANCELLED'""",
                (reason, utc_now(), job_id),
            )

    def active_process_job_count(self) -> int:
        with self._connect() as db:
            return int(db.execute(
                """SELECT COUNT(*) FROM processing_jobs
                   WHERE process_external_id IS NOT NULL
                     AND status IN ('PROCESS_PENDING','PROCESSING')"""
            ).fetchone()[0])

    def waiting_process_job_count(self) -> int:
        with self._connect() as db:
            return int(db.execute(
                """SELECT COUNT(*) FROM processing_jobs
                   WHERE process_external_id IS NULL
                     AND status IN ('DOWNLOADED','PROCESS_PENDING')"""
            ).fetchone()[0])

    def set_processing_route(self, job_id: int, path: str, nas_sync_state: str) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE processing_jobs SET processing_output_dir=?, nas_sync_state=?,
                   nas_sync_error=NULL, updated_at=?
                   WHERE id=? AND cancel_requested=0 AND status!='CANCELLED'""",
                (path, nas_sync_state, utc_now(), job_id),
            )

    def nas_sync_jobs_due(self, now: str) -> list[sqlite3.Row]:
        with self._connect() as db:
            return list(db.execute(
                """SELECT * FROM processing_jobs
                   WHERE status='COMPLETED' AND nas_sync_state IN ('PENDING','FAILED_RETRY','SYNCING')
                     AND (nas_sync_next_attempt_at IS NULL OR nas_sync_next_attempt_at <= ?)
                   ORDER BY id LIMIT 1""",
                (now,),
            ))

    def update_nas_sync(
        self,
        job_id: int,
        state: str,
        *,
        attempts: int | None = None,
        error: str | None = None,
        next_attempt_at: str | None = None,
        synced_at: str | None = None,
        processed_file_path: str | None = None,
        processed_files_json: str | None = None,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE processing_jobs SET nas_sync_state=?,
                   nas_sync_attempts=COALESCE(?, nas_sync_attempts), nas_sync_error=?,
                   nas_sync_next_attempt_at=?, nas_synced_at=COALESCE(?, nas_synced_at),
                   processed_file_path=COALESCE(?, processed_file_path),
                   processed_files_json=COALESCE(?, processed_files_json), updated_at=?
                   WHERE id=? AND cancel_requested=0 AND status!='CANCELLED'""",
                (state, attempts, error, next_attempt_at, synced_at, processed_file_path,
                 processed_files_json, utc_now(), job_id),
            )

    def nas_fallback_cleanup_jobs(self) -> list[sqlite3.Row]:
        with self._connect() as db:
            return list(db.execute(
                """SELECT * FROM processing_jobs
                   WHERE nas_sync_state='DONE' AND fallback_cleanup_at IS NULL
                     AND processing_output_dir IS NOT NULL
                     AND processing_output_dir != intended_output_dir
                   ORDER BY id"""
            ))

    def mark_fallback_cleaned(self, job_id: int, cleaned_at: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE processing_jobs SET fallback_cleanup_at=?, updated_at=? WHERE id=?",
                (cleaned_at, utc_now(), job_id),
            )

    def retry_nas_sync(self, job_id: int) -> bool:
        with self._connect() as db:
            return db.execute(
                """UPDATE processing_jobs SET nas_sync_state='PENDING',
                   nas_sync_error=NULL, nas_sync_next_attempt_at=NULL, updated_at=?
                   WHERE id=? AND nas_sync_state IN ('PENDING','FAILED_RETRY')""",
                (utc_now(), job_id),
            ).rowcount == 1

    def cleanup_job_due(self, now: str) -> sqlite3.Row | None:
        with self._connect() as db:
            return db.execute(
                """SELECT * FROM processing_jobs
                   WHERE status='COMPLETED' AND COALESCE(cleanup_state, '') != 'CLEANED'
                     AND COALESCE(nas_sync_state, 'NOT_REQUIRED') IN ('DONE','NOT_REQUIRED')
                     AND (next_cleanup_attempt_at IS NULL OR next_cleanup_attempt_at <= ?)
                   ORDER BY id LIMIT 1""",
                (now,),
            ).fetchone()

    def update_cleanup_job(
        self,
        job_id: int,
        *,
        state: str,
        error: str | None = None,
        cleanup_at: str | None = None,
        source_deleted: bool = False,
        bytes_freed: int | None = None,
        attempts: int = 0,
        next_attempt_at: str | None = None,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE processing_jobs SET cleanup_state=?, cleanup_error=?, cleanup_at=?,
                   source_deleted=?, cleanup_bytes_freed=COALESCE(?, cleanup_bytes_freed),
                   cleanup_attempts=?, next_cleanup_attempt_at=?, updated_at=? WHERE id=?""",
                (state, error, cleanup_at, int(source_deleted), bytes_freed, attempts,
                 next_attempt_at, utc_now(), job_id),
            )

    def get_poll_state(self, channel_id: str) -> sqlite3.Row | None:
        with self._connect() as db:
            return db.execute("SELECT * FROM channel_poll_state WHERE channel_id=?", (channel_id,)).fetchone()

    def poll_states(self) -> list[sqlite3.Row]:
        with self._connect() as db:
            return list(db.execute("SELECT * FROM channel_poll_state ORDER BY channel_id"))

    def reset_poll_baseline(self, channel_id: str) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO channel_poll_state (channel_id, initialized, consecutive_failures)
                   VALUES (?, 0, 0)
                   ON CONFLICT(channel_id) DO UPDATE SET initialized=0, last_error=NULL,
                     consecutive_failures=0, next_poll_at=NULL""",
                (channel_id,),
            )

    def record_poll_success(self, channel_id: str, latest_video_id: str | None, initialized: bool = True) -> bool:
        now = utc_now()
        previous = self.get_poll_state(channel_id)
        recovered = bool(previous and previous["consecutive_failures"])
        with self._connect() as db:
            db.execute(
                """INSERT INTO channel_poll_state
                   (channel_id, initialized, last_poll_at, last_success_at, consecutive_failures,
                    latest_seen_video_id, next_poll_at)
                   VALUES (?, ?, ?, ?, 0, ?, ?)
                   ON CONFLICT(channel_id) DO UPDATE SET initialized=excluded.initialized,
                     last_poll_at=excluded.last_poll_at, last_success_at=excluded.last_success_at,
                     last_error=NULL, consecutive_failures=0,
                     latest_seen_video_id=COALESCE(excluded.latest_seen_video_id, latest_seen_video_id),
                     next_poll_at=excluded.next_poll_at""",
                (channel_id, int(initialized), now, now, latest_video_id, now),
            )
        return recovered

    def record_poll_failure(self, channel_id: str, error: str, interval: int, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        current = self.get_poll_state(channel_id)
        failures = (current["consecutive_failures"] if current else 0) + 1
        delay = min(60, interval * (1, 2, 3, 6)[min(failures - 1, 3)])
        with self._connect() as db:
            db.execute(
                """INSERT INTO channel_poll_state
                   (channel_id, last_poll_at, last_error, consecutive_failures, next_poll_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(channel_id) DO UPDATE SET last_poll_at=excluded.last_poll_at,
                     last_error=excluded.last_error, consecutive_failures=excluded.consecutive_failures,
                     next_poll_at=excluded.next_poll_at""",
                (channel_id, now.isoformat(), error, failures, (now + timedelta(seconds=delay)).isoformat()),
            )
        return delay

    def poll_due(self, channel_id: str, now: datetime | None = None) -> bool:
        row = self.get_poll_state(channel_id)
        next_poll = parse_utc(row["next_poll_at"]) if row else None
        return not next_poll or next_poll <= (now or datetime.now(timezone.utc))

    def latest_activity(self) -> dict[str, str | None]:
        with self._connect() as db:
            video = db.execute("SELECT MAX(first_seen_at) FROM videos WHERE baseline=0").fetchone()[0]
        return {"last_new_video": video}
