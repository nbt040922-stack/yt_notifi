from __future__ import annotations

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
                """CREATE TABLE IF NOT EXISTS processing_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    video_id TEXT NOT NULL UNIQUE,
                    video_url TEXT NOT NULL,
                    video_title TEXT NOT NULL,
                    source_channel_id TEXT NOT NULL,
                    channel_name TEXT NOT NULL,
                    output_dir TEXT NOT NULL,
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
                    process_error TEXT,
                    process_attempts INTEGER NOT NULL DEFAULT 0,
                    next_process_attempt_at TEXT
                )"""
            )
            job_columns = {row[1] for row in db.execute("PRAGMA table_info(processing_jobs)")}
            job_additions = {
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
                "process_error": "TEXT",
                "process_attempts": "INTEGER NOT NULL DEFAULT 0",
                "next_process_attempt_at": "TEXT",
            }
            for name, sql_type in job_additions.items():
                if name not in job_columns:
                    db.execute(f"ALTER TABLE processing_jobs ADD COLUMN {name} {sql_type}")
            db.execute("UPDATE processing_jobs SET updated_at=created_at WHERE updated_at IS NULL")

    def record_event(self, event: VideoEvent, baseline: bool = False) -> bool:
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
                    detected_at, detection_latency_seconds, detection_source, baseline)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (event.video_id, event.channel_id, event.title, event.published, now_text, now_text, now_text, latency, event.source, int(baseline)),
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
            return list(db.execute("SELECT * FROM videos WHERE notification_sent=0 AND notification_attempts BETWEEN 1 AND 2"))

    def create_processing_job(
        self,
        event: VideoEvent,
        channel_name: str,
        output_dir: str,
        status: str,
        error: str | None,
    ) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                """INSERT OR IGNORE INTO processing_jobs
                   (created_at, status, video_id, video_url, video_title, source_channel_id,
                    channel_name, output_dir, error, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (utc_now(), status, event.video_id, event.url, event.title, event.channel_id,
                 channel_name, output_dir, error, utc_now()),
            )
            return cursor.rowcount == 1

    def processing_jobs(self) -> list[sqlite3.Row]:
        with self._connect() as db:
            return list(db.execute("SELECT * FROM processing_jobs ORDER BY id DESC"))

    def download_jobs_due(self, now: str) -> list[sqlite3.Row]:
        with self._connect() as db:
            return list(db.execute(
                """SELECT * FROM processing_jobs
                   WHERE status IN ('QUEUED', 'DOWNLOAD_PENDING', 'DOWNLOADING')
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
        download_error: str | None = None,
        attempts: int = 0,
        next_attempt_at: str | None = None,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE processing_jobs SET status=?,
                   download_external_id=COALESCE(?, download_external_id), download_state=?,
                   download_progress=?, downloaded_file_path=COALESCE(?, downloaded_file_path),
                   download_error=?, updated_at=?, download_attempts=?, next_download_attempt_at=?
                   WHERE id=?""",
                (status, external_id, download_state, progress, downloaded_file_path,
                 download_error, utc_now(), attempts, next_attempt_at, job_id),
            )

    def process_jobs_due(self, now: str) -> list[sqlite3.Row]:
        with self._connect() as db:
            return list(db.execute(
                """SELECT * FROM processing_jobs
                   WHERE status IN ('DOWNLOADED', 'PROCESS_PENDING', 'PROCESSING')
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
        process_error: str | None = None,
        attempts: int = 0,
        next_attempt_at: str | None = None,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """UPDATE processing_jobs SET status=?,
                   process_external_id=COALESCE(?, process_external_id), process_state=?,
                   process_progress=?, processed_file_path=COALESCE(?, processed_file_path),
                   process_error=?, updated_at=?, process_attempts=?, next_process_attempt_at=?
                   WHERE id=?""",
                (status, external_id, process_state, progress, processed_file_path,
                 process_error, utc_now(), attempts, next_attempt_at, job_id),
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
