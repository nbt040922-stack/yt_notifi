from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import VideoEvent


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS videos (
                    video_id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    notification_sent INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS subscriptions (
                    topic TEXT PRIMARY KEY,
                    mode TEXT NOT NULL,
                    verified_at TEXT NOT NULL,
                    lease_seconds INTEGER
                );
                """
            )

    def record_event(self, event: VideoEvent) -> bool:
        now = utc_now()
        with self._connect() as db:
            cursor = db.execute(
                """INSERT OR IGNORE INTO videos
                   (video_id, channel_id, title, published_at, first_seen_at, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (event.video_id, event.channel_id, event.title, event.published, now, now),
            )
            is_new = cursor.rowcount == 1
            if not is_new:
                db.execute(
                    "UPDATE videos SET title = ?, last_seen_at = ? WHERE video_id = ?",
                    (event.title, now, event.video_id),
                )
        return is_new

    def mark_notification(self, video_id: str, sent: bool) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE videos SET notification_sent = ? WHERE video_id = ?",
                (int(sent), video_id),
            )

    def get_video(self, video_id: str) -> sqlite3.Row | None:
        with self._connect() as db:
            return db.execute("SELECT * FROM videos WHERE video_id = ?", (video_id,)).fetchone()

    def record_subscription(self, topic: str, mode: str, lease_seconds: int | None) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO subscriptions (topic, mode, verified_at, lease_seconds)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(topic) DO UPDATE SET mode=excluded.mode,
                     verified_at=excluded.verified_at, lease_seconds=excluded.lease_seconds""",
                (topic, mode, utc_now(), lease_seconds),
            )
