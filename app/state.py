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

    def get_poll_state(self, channel_id: str) -> sqlite3.Row | None:
        with self._connect() as db:
            return db.execute("SELECT * FROM channel_poll_state WHERE channel_id=?", (channel_id,)).fetchone()

    def poll_states(self) -> list[sqlite3.Row]:
        with self._connect() as db:
            return list(db.execute("SELECT * FROM channel_poll_state ORDER BY channel_id"))

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
