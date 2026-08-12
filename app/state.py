from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

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

    @staticmethod
    def _create_subscriptions(db: sqlite3.Connection) -> None:
        db.execute(
            """CREATE TABLE IF NOT EXISTS subscriptions (
                topic TEXT PRIMARY KEY,
                channel_id TEXT NOT NULL,
                callback_url TEXT NOT NULL,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                requested_at TEXT,
                verified_at TEXT,
                lease_seconds INTEGER,
                expires_at TEXT,
                last_renewal_attempt_at TEXT,
                last_error TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                next_retry_at TEXT
            )"""
        )

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
                "detection_source": "TEXT NOT NULL DEFAULT 'websub'",
                "baseline": "INTEGER NOT NULL DEFAULT 0",
            }
            for name, sql_type in additions.items():
                if name not in video_columns:
                    db.execute(f"ALTER TABLE videos ADD COLUMN {name} {sql_type}")
            if "detected_at" not in video_columns:
                db.execute("UPDATE videos SET detected_at=first_seen_at")
            if "notification_attempts" not in video_columns:
                db.execute("UPDATE videos SET notification_attempts=1")

            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='subscriptions'").fetchone()
            if not exists:
                self._create_subscriptions(db)
            else:
                columns = {row[1] for row in db.execute("PRAGMA table_info(subscriptions)")}
                if "status" not in columns:
                    old_rows = list(db.execute("SELECT topic, mode, verified_at, lease_seconds FROM subscriptions"))
                    db.execute("ALTER TABLE subscriptions RENAME TO subscriptions_phase1")
                    self._create_subscriptions(db)
                    for row in old_rows:
                        channel_id = parse_qs(urlsplit(row["topic"]).query).get("channel_id", [""])[0]
                        verified = row["verified_at"]
                        lease = row["lease_seconds"]
                        expires = (parse_utc(verified) + timedelta(seconds=lease)).isoformat() if verified and lease else None
                        db.execute(
                            """INSERT INTO subscriptions
                               (topic, channel_id, callback_url, mode, status, requested_at,
                                verified_at, lease_seconds, expires_at)
                               VALUES (?, ?, '', ?, 'ACTIVE', ?, ?, ?, ?)""",
                            (row["topic"], channel_id, row["mode"], verified, verified, lease, expires),
                        )
                    db.execute("DROP TABLE subscriptions_phase1")
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

    def mark_subscription_requested(self, channel_id: str, topic: str, callback: str, preserve_active: bool = False) -> None:
        now = utc_now()
        current = self.get_subscription(topic)
        status = "ACTIVE" if preserve_active and current and current["status"] == "ACTIVE" else "REQUESTED"
        with self._connect() as db:
            db.execute(
                """INSERT INTO subscriptions
                   (topic, channel_id, callback_url, mode, status, requested_at, last_renewal_attempt_at)
                   VALUES (?, ?, ?, 'subscribe', ?, ?, ?)
                   ON CONFLICT(topic) DO UPDATE SET channel_id=excluded.channel_id,
                     callback_url=excluded.callback_url, mode='subscribe', status=?,
                     requested_at=excluded.requested_at,
                     last_renewal_attempt_at=excluded.last_renewal_attempt_at,
                     last_error=NULL""",
                (topic, channel_id, callback, status, now, now, status),
            )

    def activate_subscription(self, topic: str, mode: str, lease_seconds: int | None, callback: str) -> None:
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=lease_seconds) if lease_seconds else None
        status = "UNSUBSCRIBED" if mode == "unsubscribe" else "ACTIVE"
        channel_id = parse_qs(urlsplit(topic).query).get("channel_id", [""])[0]
        with self._connect() as db:
            db.execute(
                """INSERT INTO subscriptions
                   (topic, channel_id, callback_url, mode, status, verified_at, lease_seconds, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(topic) DO UPDATE SET mode=excluded.mode, status=excluded.status,
                     callback_url=excluded.callback_url, verified_at=excluded.verified_at,
                     lease_seconds=excluded.lease_seconds, expires_at=excluded.expires_at,
                     last_error=NULL, retry_count=0, next_retry_at=NULL""",
                (topic, channel_id, callback, mode, status, now.isoformat(), lease_seconds, expires.isoformat() if expires else None),
            )

    def record_subscription_failure(self, topic: str, error: str, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        row = self.get_subscription(topic)
        if not row:
            return
        retry_count = row["retry_count"] + 1
        delay = (60, 300, 900, 1800)[min(retry_count - 1, 3)]
        expires = parse_utc(row["expires_at"])
        status = "ACTIVE" if row["status"] == "ACTIVE" and (not expires or expires > now) else "FAILED"
        with self._connect() as db:
            db.execute(
                """UPDATE subscriptions SET status=?, last_error=?, retry_count=?,
                   next_retry_at=? WHERE topic=?""",
                (status, error, retry_count, (now + timedelta(seconds=delay)).isoformat(), topic),
            )

    def get_subscription(self, topic: str) -> sqlite3.Row | None:
        with self._connect() as db:
            return db.execute("SELECT * FROM subscriptions WHERE topic=?", (topic,)).fetchone()

    def subscriptions(self) -> list[sqlite3.Row]:
        with self._connect() as db:
            return list(db.execute("SELECT * FROM subscriptions ORDER BY channel_id"))

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

    def record_subscription(self, topic: str, mode: str, lease_seconds: int | None) -> None:
        self.activate_subscription(topic, mode, lease_seconds, "")

    def latest_activity(self) -> dict[str, str | None]:
        with self._connect() as db:
            webhook = db.execute("SELECT MAX(last_seen_at) FROM videos WHERE detection_source='websub'").fetchone()[0]
            video = db.execute("SELECT MAX(first_seen_at) FROM videos WHERE baseline=0").fetchone()[0]
        return {"last_webhook": webhook, "last_new_video": video}
