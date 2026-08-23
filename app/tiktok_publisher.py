from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .channel_store import ChannelStore, ChannelStoreError
from .minha import MinHaClient, MinHaUnavailable
from .state import StateStore, utc_now


logger = logging.getLogger("yt_notifi")
RUNNING_STATUSES = {
    "VALIDATING", "IDENTITY_CHECK", "OPENING_BROWSER", "UPLOADING",
    "CONFIGURING_POST", "POSTING", "VERIFYING", "READY_TO_POST",
    "READY_FOR_POST", "WAITING_TIKTOK_UPLOAD", "WAITING_FOR_TIKTOK_PROCESSING",
    "NORMALIZING_UPLOAD_PAGE", "ATTACHING_FILE",
}
POST_UNCERTAIN_STATUSES = {"POSTING", "VERIFYING"}
PRIVATE_VISIBILITY = "ONLY_YOU"


class PublishError(RuntimeError):
    def __init__(self, code: str, message: str = "", status_code: int = 409):
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.status_code = status_code


def _error_text(exc: PublishError) -> str:
    return exc.code if exc.message == exc.code else f"{exc.code}: {exc.message}"


def _path_identity(value: str) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(value)))


def _idempotency_key(processing_job_id: int, video_path: str, profile_id: str) -> str:
    raw = f"{processing_job_id}\0{_path_identity(video_path)}\0{profile_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class PublishStore:
    def __init__(self, state: StateStore):
        self.state = state
        self.path = state.path
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS publish_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    processing_job_id INTEGER NOT NULL,
                    channel_id TEXT NOT NULL,
                    minha_profile_id TEXT NOT NULL,
                    video_path TEXT NOT NULL,
                    caption TEXT NOT NULL,
                    visibility TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    failure_reason TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    current_step TEXT,
                    progress_percent REAL NOT NULL DEFAULT 0,
                    expected_tiktok_uid TEXT,
                    current_tiktok_uid TEXT,
                    tiktok_username TEXT,
                    pre_publish_check_json TEXT,
                    file_size INTEGER,
                    upload_attach_count INTEGER NOT NULL DEFAULT 0,
                    post_click_count INTEGER NOT NULL DEFAULT 0,
                    post_attempted_at TEXT,
                    post_verification_method TEXT,
                    tiktok_post_id TEXT,
                    tiktok_post_url TEXT,
                    FOREIGN KEY(processing_job_id) REFERENCES processing_jobs(id)
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS publish_receipts (
                    publish_job_id INTEGER PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    processing_job_id INTEGER NOT NULL,
                    channel_id TEXT NOT NULL,
                    minha_profile_id TEXT NOT NULL,
                    expected_tiktok_uid TEXT NOT NULL,
                    current_tiktok_uid TEXT NOT NULL,
                    tiktok_username TEXT,
                    video_path TEXT NOT NULL,
                    caption TEXT NOT NULL,
                    visibility TEXT NOT NULL,
                    posted_at TEXT NOT NULL,
                    tiktok_post_id TEXT,
                    tiktok_post_url TEXT,
                    verification_method TEXT NOT NULL,
                    FOREIGN KEY(publish_job_id) REFERENCES publish_jobs(id)
                )"""
            )
            columns = {row[1] for row in db.execute("PRAGMA table_info(publish_jobs)")}
            for name, sql_type in {
                "current_step": "TEXT",
                "progress_percent": "REAL NOT NULL DEFAULT 0",
                "file_size": "INTEGER",
                "upload_attach_count": "INTEGER NOT NULL DEFAULT 0",
                "post_click_count": "INTEGER NOT NULL DEFAULT 0",
                "post_attempted_at": "TEXT",
                "post_verification_method": "TEXT",
                "tiktok_post_id": "TEXT",
                "tiktok_post_url": "TEXT",
            }.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE publish_jobs ADD COLUMN {name} {sql_type}")
            placeholders = ",".join("?" for _ in POST_UNCERTAIN_STATUSES)
            db.execute(
                f"""UPDATE publish_jobs SET status='POST_RESULT_UNCERTAIN',
                    failure_reason='PROCESS_RESTART_AFTER_POST_CLICK', updated_at=?
                    WHERE status IN ({placeholders})""",
                (utc_now(), *POST_UNCERTAIN_STATUSES),
            )
            pre_post = RUNNING_STATUSES - POST_UNCERTAIN_STATUSES
            placeholders = ",".join("?" for _ in pre_post)
            db.execute(
                f"""UPDATE publish_jobs SET status='FAILED_PRE_POST',
                    failure_reason='PROCESS_RESTART_BEFORE_POST', updated_at=?
                    WHERE status IN ({placeholders})""",
                (utc_now(), *pre_post),
            )

    def list(self, limit: int = 200) -> list[sqlite3.Row]:
        with self._connect() as db:
            return list(db.execute(
                "SELECT * FROM publish_jobs ORDER BY id DESC LIMIT ?", (limit,),
            ))

    def get(self, job_id: int) -> sqlite3.Row | None:
        with self._connect() as db:
            return db.execute("SELECT * FROM publish_jobs WHERE id=?", (job_id,)).fetchone()

    def by_idempotency_key(self, key: str) -> sqlite3.Row | None:
        with self._connect() as db:
            return db.execute(
                "SELECT * FROM publish_jobs WHERE idempotency_key=?", (key,),
            ).fetchone()

    def receipt(self, job_id: int) -> sqlite3.Row | None:
        with self._connect() as db:
            return db.execute(
                "SELECT * FROM publish_receipts WHERE publish_job_id=?", (job_id,),
            ).fetchone()

    def dashboard_rows(self, processing_job_ids: list[int]) -> dict[int, dict[str, Any]]:
        if not processing_job_ids:
            return {}
        placeholders = ",".join("?" for _ in processing_job_ids)
        with self._connect() as db:
            rows = db.execute(
                f"""SELECT p.*, r.posted_at AS receipt_posted_at,
                    r.tiktok_post_id AS receipt_post_id,
                    r.tiktok_post_url AS receipt_post_url,
                    r.verification_method AS verification_method
                    FROM publish_jobs p
                    LEFT JOIN publish_receipts r ON r.publish_job_id=p.id
                    WHERE p.processing_job_id IN ({placeholders})
                    ORDER BY p.id DESC""",
                tuple(processing_job_ids),
            ).fetchall()
        result = {}
        for row in rows:
            result.setdefault(row["processing_job_id"], dict(row))
        return result

    def clear_terminal(self, statuses: tuple[str, ...]) -> int:
        """Delete terminal rows without receipts; receipts remain idempotency evidence."""
        placeholders = ",".join("?" for _ in statuses)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                f"""SELECT p.id FROM publish_jobs p
                    LEFT JOIN publish_receipts r ON r.publish_job_id=p.id
                    WHERE p.status IN ({placeholders}) AND r.publish_job_id IS NULL""",
                statuses,
            ).fetchall()
            ids = [row["id"] for row in rows]
            if ids:
                marks = ",".join("?" for _ in ids)
                db.execute(f"DELETE FROM publish_receipts WHERE publish_job_id IN ({marks})", ids)
                db.execute(f"DELETE FROM publish_jobs WHERE id IN ({marks})", ids)
            return len(ids)

    def create(
        self, processing_job_id: int, channel_id: str, profile_id: str,
        video_path: str, caption: str,
    ) -> sqlite3.Row:
        key = _idempotency_key(processing_job_id, video_path, profile_id)
        now = utc_now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT * FROM publish_jobs WHERE idempotency_key=?", (key,),
            ).fetchone()
            if existing:
                raise PublishError(
                    "PUBLISH_JOB_ALREADY_EXISTS",
                    f"Publish job {existing['id']} already owns this processed output.",
                )
            cursor = db.execute(
                """INSERT INTO publish_jobs
                   (idempotency_key, processing_job_id, channel_id, minha_profile_id,
                    video_path, caption, visibility, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'QUEUED', ?, ?)""",
                (key, processing_job_id, channel_id, profile_id, video_path, caption,
                 PRIVATE_VISIBILITY, now, now),
            )
            return db.execute(
                "SELECT * FROM publish_jobs WHERE id=?", (cursor.lastrowid,),
            ).fetchone()

    def claim(self, job_id: int) -> sqlite3.Row:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT * FROM publish_jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                raise PublishError("PUBLISH_JOB_NOT_FOUND", status_code=404)
            if job["status"] != "QUEUED":
                raise PublishError("PUBLISH_JOB_NOT_RUNNABLE")
            if int(job["attempt_count"] or 0) >= 1:
                logger.info("RETRY_REASON publish_job_id=%s reason=MAX_ATTEMPTS_1", job_id)
                raise PublishError("PUBLISH_ATTEMPT_LIMIT")
            placeholders = ",".join("?" for _ in RUNNING_STATUSES)
            running = db.execute(
                f"SELECT id FROM publish_jobs WHERE id!=? AND status IN ({placeholders}) LIMIT 1",
                (job_id, *RUNNING_STATUSES),
            ).fetchone()
            if running:
                raise PublishError("PUBLISHER_BUSY")
            now = utc_now()
            db.execute(
                """UPDATE publish_jobs SET status='VALIDATING', started_at=?, updated_at=?,
                   failure_reason=NULL, attempt_count=attempt_count+1 WHERE id=?""",
                (now, now, job_id),
            )
            return db.execute("SELECT * FROM publish_jobs WHERE id=?", (job_id,)).fetchone()

    def update(self, job_id: int, status: str, **values: Any) -> sqlite3.Row:
        allowed = {
            "failure_reason", "expected_tiktok_uid", "current_tiktok_uid",
            "tiktok_username", "pre_publish_check_json", "completed_at",
            "current_step", "progress_percent", "file_size", "upload_attach_count",
            "post_click_count", "post_attempted_at", "post_verification_method",
            "tiktok_post_id", "tiktok_post_url",
        }
        fields = {key: value for key, value in values.items() if key in allowed}
        fields.update(status=status, updated_at=utc_now())
        assignments = ", ".join(f"{key}=?" for key in fields)
        with self._connect() as db:
            db.execute(
                f"UPDATE publish_jobs SET {assignments} WHERE id=?",
                (*fields.values(), job_id),
            )
            return db.execute("SELECT * FROM publish_jobs WHERE id=?", (job_id,)).fetchone()

    def running_count(self, exclude_id: int | None = None) -> int:
        placeholders = ",".join("?" for _ in RUNNING_STATUSES)
        query = f"SELECT COUNT(*) FROM publish_jobs WHERE status IN ({placeholders})"
        parameters: tuple[Any, ...] = tuple(RUNNING_STATUSES)
        if exclude_id is not None:
            query += " AND id!=?"
            parameters += (exclude_id,)
        with self._connect() as db:
            return int(db.execute(query, parameters).fetchone()[0])

    def complete_with_receipt(
        self, job_id: int, *, verification_method: str,
        tiktok_post_id: str | None = None, tiktok_post_url: str | None = None,
    ) -> sqlite3.Row:
        if not verification_method.strip():
            raise PublishError("POST_VERIFICATION_MISSING")
        if not verification_method.startswith(("SUCCESS_TEXT:", "POST_RESPONSE_SUCCESS:")):
            raise PublishError("POST_VERIFICATION_WEAK")
        now = utc_now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            job = db.execute("SELECT * FROM publish_jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                raise PublishError("PUBLISH_JOB_NOT_FOUND", status_code=404)
            if job["status"] != "VERIFYING":
                raise PublishError("PUBLISH_JOB_NOT_VERIFYING")
            if not job["expected_tiktok_uid"] or not job["current_tiktok_uid"]:
                raise PublishError("IDENTITY_RECEIPT_MISSING")
            db.execute(
                """INSERT INTO publish_receipts
                   (publish_job_id, idempotency_key, processing_job_id, channel_id,
                    minha_profile_id, expected_tiktok_uid, current_tiktok_uid,
                    tiktok_username, video_path, caption, visibility, posted_at,
                    tiktok_post_id, tiktok_post_url, verification_method)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (job_id, job["idempotency_key"], job["processing_job_id"], job["channel_id"],
                 job["minha_profile_id"], job["expected_tiktok_uid"],
                 job["current_tiktok_uid"], job["tiktok_username"], job["video_path"],
                 job["caption"], job["visibility"], now, tiktok_post_id,
                 tiktok_post_url, verification_method.strip()),
            )
            db.execute(
                """UPDATE publish_jobs SET status='DONE', completed_at=?, updated_at=?,
                   failure_reason=NULL, post_verification_method=?,
                   tiktok_post_id=?, tiktok_post_url=? WHERE id=?""",
                (now, now, verification_method.strip(), tiktok_post_id, tiktok_post_url, job_id),
            )
            return db.execute("SELECT * FROM publish_jobs WHERE id=?", (job_id,)).fetchone()


@dataclass(frozen=True)
class PreparedPost:
    upload_completed: bool
    visibility: str
    visibility_evidence: str


@dataclass(frozen=True)
class PostedResult:
    verification_method: str
    tiktok_post_id: str | None = None
    tiktok_post_url: str | None = None


class TikTokUploadAutomation:
    UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?from=webapp"
    FILE_INPUT = "input[type='file']"
    CAPTION_SELECTORS = (
        "[data-e2e='caption-editor'] [contenteditable='true']",
        "div[contenteditable='true'][role='textbox']",
    )
    PRIVACY_BUTTON_NAMES = ("Who can watch this video", "Ai có thể xem video này")
    PRIVATE_NAMES = ("Only you", "Chỉ mình tôi", "Private", "Riêng tư")
    POST_BUTTON_NAMES = (
        "Post", "Đăng", "Veröffentlichen", "Publier", "Publicar", "Pubblicare",
        "Опубликовать", "发布", "게시", "نشر", "प्रकाशित करें", "Yayınla",
    )
    SUCCESS_TEXT = re.compile(
        r"(?:video has been )?(?:published|posted)|"
        r"video (?:was )?published successfully|"
        r"successfully published|"
        r"video đã được đăng",
        re.IGNORECASE,
    )

    def __init__(self, minha_client: MinHaClient):
        self.minha = minha_client
        self.publish_job_id = None
        self.progress_callback = None
        self.attempt_number = 1
        self.upload_only = os.getenv("TIKTOK_DEBUG_UPLOAD_ONLY", "0") == "1"
        self.privacy_debug_only = os.getenv("TIKTOK_DEBUG_PRIVACY_ONLY", "0") == "1"
        self.ready_only = os.getenv("TIKTOK_DEBUG_READY_ONLY", "0") == "1"
        self.post_click_callback = None
        self._last_url = "<unavailable>"
        self._last_title = "<unavailable>"
        self._last_file_count = "unknown"

    def _emit(self, step: str, *, progress: float | None = None, **details) -> None:
        if details.get("url"):
            self._last_url = details["url"]
        if details.get("title"):
            self._last_title = details["title"]
        if self.progress_callback:
            self.progress_callback(step, progress, details)
        fields = " ".join(
            f"{key}={str(value)[:500]}" for key, value in details.items()
            if value is not None
        )
        logger.info(
            "TIKTOK_PUBLISH step=%s publish_job_id=%s attempt=%s %s",
            step, self.publish_job_id, self.attempt_number, fields,
        )

    def prepare(self, profile_id: str, video_path: str, caption: str) -> PreparedPost:
        return self._execute(profile_id, video_path, caption)

    def publish(
        self, profile_id: str, video_path: str, caption: str,
        before_post, after_post,
    ) -> PostedResult:
        return self._execute(
            profile_id, video_path, caption, real_post=True,
            before_post=before_post, after_post=after_post,
        )

    def publish_ready(self, profile_id: str, before_post, after_post) -> PostedResult:
        """Publish an already-uploaded READY_TO_POST Studio page once."""
        from playwright.sync_api import sync_playwright
        endpoint = f"{self.minha.base_url}/api/profiles/{quote(profile_id, safe='')}/cdp"
        headers = {"Authorization": f"Bearer {self.minha.auth_token}"} if self.minha.auth_token else None
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(endpoint, headers=headers)
            self._emit("CDP_CONNECTED", url=endpoint)
            context = browser.contexts[0]
            page = next((p for p in context.pages if self.UPLOAD_URL.split("?")[0] in p.url), None)
            if page is None:
                raise PublishError("READY_TARGET_NOT_FOUND")
            self._set_private(page)
            evidence = self._private_evidence(page)
            button = self._post_button(page)
            self._emit("PRE_POST_READY", url=page.url, visible=button.is_visible(), enabled=button.is_enabled())
            before_post(PreparedPost(True, PRIVATE_VISIBILITY, evidence))
            post_response = {"status": None, "url": None}
            def on_response(response):
                if response.request.method == "POST" and any(token in response.url.lower() for token in ("publish", "upload", "video", "content")):
                    post_response.update(status=response.status, url=response.url)
                    self._emit("POST_REQUEST_OBSERVED", url=response.url, method="POST")
                    self._emit("POST_RESPONSE", url=response.url, status=response.status)
            page.on("response", on_response)
            self._emit("POST_CLICK_START", text=button.inner_text())
            if self.post_click_callback:
                self.post_click_callback()
            button.click(timeout=30_000)
            self._emit("POST_CLICK_DONE", url=page.url)
            after_post()
            result = self._verify_success(page)
            if post_response["status"] and 200 <= post_response["status"] < 300:
                self._emit("POST_SUCCESS_SIGNAL", status=post_response["status"])
            return result

    def _execute(
        self, profile_id: str, video_path: str, caption: str, *,
        real_post: bool = False, before_post=None, after_post=None,
    ) -> PreparedPost | PostedResult:
        page = None
        step = "CONNECT_CDP"
        try:
            self._emit(step)
            from playwright.sync_api import sync_playwright

            headers = (
                {"Authorization": f"Bearer {self.minha.auth_token}"}
                if self.minha.auth_token else None
            )
            endpoint = (
                f"{self.minha.base_url}/api/profiles/{quote(profile_id, safe='')}/cdp"
            )
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(endpoint, headers=headers)
                self._emit("CDP_CONNECTED", url=endpoint)
                context = browser.contexts[0]
                pages = context.pages
                page_info = []
                for candidate in pages:
                    try:
                        child_count = candidate.locator("body > *").count()
                        page_info.append({
                            "url": candidate.url, "title": candidate.title(),
                            "body_child_count": child_count,
                        })
                    except Exception as inspect_error:
                        page_info.append({"url": "<error>", "error": type(inspect_error).__name__})
                self._emit("PAGE_LIST", pages=page_info)
                page = next(
                    (candidate for candidate in pages
                     if self.UPLOAD_URL.split("?")[0] in candidate.url
                     and candidate.locator("body > *").count()),
                    next((candidate for candidate in pages if self.UPLOAD_URL.split("?")[0] in candidate.url), None),
                ) or (pages[0] if pages else context.new_page())
                self._emit("PAGE_FOUND", url=page.url, title=page.title())
                target_reused = self.UPLOAD_URL.split("?")[0] in page.url
                self._emit("TARGET_FOUND", url=page.url, target_reused=target_reused)
                if target_reused:
                    self._emit("TARGET_REUSED", url=page.url)
                if not target_reused:
                    # Open upload through TikTok's own UI, never by navigating
                    # an existing CDP-proxied target.
                    upload_button = page.locator("[data-tt='Sidebar_UploadEntrance_WideButton']").first
                    if not upload_button.count():
                        raise PublishError("UPLOAD_TARGET_NOT_FOUND")
                    upload_button.click(timeout=30_000)
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        candidate = next((item for item in context.pages if self.UPLOAD_URL.split("?")[0] in item.url), None)
                        if candidate is not None:
                            page = candidate
                            target_reused = False
                            break
                        page.wait_for_timeout(500)
                    else:
                        raise PublishError("UPLOAD_TARGET_NOT_FOUND")
                self._emit("NO_GOTO_USED", value=True, current_url=page.url, target_reused=target_reused)
                self._emit("UPLOAD_PAGE_OPEN", url=page.url, title=page.title())
                try:
                    page.locator("body > *").first.wait_for(state="attached", timeout=30_000)
                except Exception:
                    page.wait_for_timeout(5_000)
                step = "WAIT_FILE_INPUT"
                self._emit(step)
                page = self._normalize_upload_target(page)
                self._emit("UPLOAD_STATE", state=self._classify_upload_target(page))
                file_input, frame, dom = self._find_file_input(page)
                self._emit("FRESH_FILE_INPUT_FOUND", matching_elements=dom["count"], frame_url=frame.url)
                self._emit(
                    "FILE_INPUT_FOUND", frame_url=frame.url, selector=self.FILE_INPUT,
                    matching_elements=dom["count"], hidden=dom["hidden"],
                    accept=dom["accept"], disabled=dom["disabled"],
                    data_attributes=dom["data_attributes"],
                )
                resolved = Path(video_path).expanduser().resolve(strict=False)
                exists, is_file, size = resolved.exists(), resolved.is_file(), None
                if is_file:
                    size = resolved.stat().st_size
                if not resolved.is_absolute() or not is_file or not size:
                    raise PublishError(
                        "VIDEO_PATH_INVALID",
                        f"resolved={resolved} exists={exists} is_file={is_file} size={size}",
                    )
                step = "SET_INPUT_FILES_START"
                self._emit(
                    step, selector=self.FILE_INPUT, matching_elements=dom["count"],
                    video_path=str(resolved), file_exists=exists, file_size=size,
                )
                try:
                    file_input.set_input_files(str(resolved))
                except Exception as upload_error:
                    # CDP marks this browser as remote and rejects localPaths
                    # above 50 MiB. The native input also accepts a payload;
                    # use it only for this proven transport limitation.
                    if "larger than 50Mb" not in str(upload_error):
                        raise
                    self._emit(
                        "SET_INPUT_FILES_RETRY_CDP",
                        video_path=str(resolved), file_size=size,
                    )
                    self._set_input_files_via_cdp(page, frame, str(resolved))
                self._emit("SET_INPUT_FILES_DONE", video_path=str(resolved), file_size=size)
                self._emit("FILE_ATTACHED", video_path=str(resolved), file_size=size)
                self._emit("UPLOAD_STARTED", progress=0, url=page.url, title=page.title())
                self._emit("TIKTOK_UPLOAD_STARTED", progress=0)
                step = "WAITING_TIKTOK_UPLOAD"
                page = self._wait_upload_complete(page, profile_id, endpoint)
                if self.upload_only:
                    return PreparedPost(True, "", "UPLOAD_COMPLETE_REAL")
                if self.ready_only:
                    self._emit("READY_TO_POST_HOLD", hold_seconds=60, waiting_for_confirmation=True)
                    page.wait_for_timeout(60_000)
                    return PreparedPost(True, "", "READY_TO_POST")
                step = "CONFIGURING_POST"
                self._emit(step)
                if self.privacy_debug_only:
                    before = self._privacy_screenshot(page, "before")
                    self._dump_privacy_dom(page)
                    self._set_private(page)
                    after = self._privacy_screenshot(page, "after")
                    evidence = self._private_evidence(page)
                    self._emit("VISIBILITY_ONLY_YOU_CONFIRMED", before=before, after=after, evidence=evidence)
                    return PreparedPost(True, PRIVATE_VISIBILITY, evidence)
                self._set_private(page)
                evidence = self._private_evidence(page)
                prepared = PreparedPost(True, PRIVATE_VISIBILITY, evidence)
                if not real_post:
                    return prepared
                button = self._post_button(page)
                before_post(prepared)
                def on_response(response):
                    request = response.request
                    if request.method == "POST" and any(token in response.url.lower() for token in ("publish", "upload", "video", "content")):
                        self._emit("POST_REQUEST_OBSERVED", url=response.url, method=request.method)
                        self._emit("POST_RESPONSE_STATUS", url=response.url, status=response.status)
                page.on("response", on_response)
                if self.post_click_callback:
                    self.post_click_callback()
                button.click(timeout=30_000)
                self._emit("POST_CLICKED", url=page.url, title=page.title())
                after_post()
                return self._verify_success(page)
        except Exception as exc:
            self._log_failure(exc, step, page, video_path)
            raise
        finally:
            self._emit("CDP_DISCONNECTED")

    def _normalize_upload_target(self, page):
        state = self._classify_upload_target(page)
        self._emit("STALE_STATE", state=state)
        self._emit("TARGET_STATE", state=state, url=page.url)
        if state == "UPLOAD_IN_PROGRESS":
            raise PublishError("STALE_UPLOAD_IN_PROGRESS")
        if state not in {"STALE_UPLOAD", "UPLOAD_COMPLETE", "POST_FORM"}:
            return page
        self._emit("STALE_UPLOAD_DETECTED", state=state)
        discard = None
        for name in ("Verwerfen", "Discard", "Hủy", "Abandon"):
            candidate = page.get_by_role("button", name=name, exact=True).first
            if candidate.count() and candidate.is_visible() and candidate.is_enabled():
                discard = candidate
                break
        if discard is None:
            buttons = page.locator("button")
            for index in range(min(buttons.count(), 100)):
                candidate = buttons.nth(index)
                if not candidate.is_visible() or not candidate.is_enabled():
                    continue
                text = unicodedata.normalize("NFKD", candidate.inner_text()).encode("ascii", "ignore").decode().lower()
                if any(token in text for token in ("verwerf", "discard", "abandon", "huy")):
                    discard = candidate
                    break
        if discard is None:
            raise PublishError("STALE_UPLOAD_RESET_CONTROL_NOT_FOUND")
        self._emit("DISCARD_FOUND", text=discard.inner_text(), selector="role=button")
        self._emit("DISCARD_CONTROL_FOUND", text=discard.inner_text(), selector="role=button")
        discard.click()
        self._emit("DISCARD_CLICKED")
        confirmed = self._confirm_discard_dialog(page)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            dialog_visible = any(page.locator("[role='dialog']").nth(i).is_visible() for i in range(min(page.locator("[role='dialog']").count(), 20)))
            fresh_count = self._fresh_file_input_count(page)
            self._emit("FRESH_INPUT_COUNT", count=fresh_count)
            if not dialog_visible:
                self._emit("DIALOG_CLOSED")
            if not dialog_visible and fresh_count:
                self._emit("OLD_UPLOAD_GONE", state=self._classify_upload_target(page))
                self._emit("STALE_UPLOAD_CLEARED")
                self._emit("FRESH_FILE_INPUT_FOUND", matching_elements=fresh_count)
                self._emit("CDP_CONNECTED")
                return page
            page.wait_for_timeout(500)
        if not confirmed and self._fresh_file_input_count(page):
            return page
        raise PublishError("STALE_UPLOAD_CONFIRMATION_FAILED")

    def _fresh_file_input_count(self, page) -> int:
        total = 0
        for frame in page.frames:
            try:
                total += frame.locator(self.FILE_INPUT).count()
            except Exception:
                continue
        return total

    def _confirm_discard_dialog(self, page) -> bool:
        """Confirm discard using only the active dialog's own primary action."""
        deadline = time.monotonic() + 5
        dialog = None
        while time.monotonic() < deadline:
            dialogs = page.locator("[role='dialog']")
            for index in range(min(dialogs.count(), 20)):
                candidate = dialogs.nth(index)
                if candidate.is_visible():
                    dialog = candidate
                    break
            if dialog is not None:
                break
            prompt = page.get_by_text("Diesen Beitrag verwerfen?", exact=False).first
            if prompt.count() and prompt.is_visible():
                candidate = prompt.locator("xpath=ancestor::*[.//button][1]")
                if candidate.count() and candidate.is_visible():
                    dialog = candidate
                    break
            page.wait_for_timeout(200)
        if dialog is None:
            self._emit("DIALOG_FOUND", dialog_count=0, found=False)
            return False
        self._emit("DIALOG_FOUND", dialog_count=1, found=True, role=dialog.get_attribute("role") or "unknown")
        buttons = dialog.locator("button")
        details = []
        confirm = None
        for index in range(min(buttons.count(), 30)):
            button = buttons.nth(index)
            attrs = button.evaluate(
                "el => ({text:(el.innerText||'').trim(), aria:el.getAttribute('aria-label'), "
                "disabled:el.disabled, ariaDisabled:el.getAttribute('aria-disabled'), "
                "outer:el.outerHTML.slice(0,800)})"
            )
            details.append(attrs)
            if not button.is_visible() or button.is_disabled() or attrs.get("ariaDisabled") == "true":
                continue
            text = unicodedata.normalize("NFKD", f"{attrs.get('text') or ''} {attrs.get('aria') or ''}").encode("ascii", "ignore").decode().lower()
            if any(token in text for token in ("cancel", "abbrechen", "weiter bearbeiten", "continue", "zuruck")):
                continue
            if any(token in text for token in ("verwerf", "discard", "abandon", "delete", "loschen")):
                confirm = button
                break
        screenshot = Path("state") / "tiktok_debug" / f"publish_{self.publish_job_id}_discard_dialog.png"
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot), full_page=True)
        self._emit(
            "DISCARD_DIALOG_DOM", dialog_count=dialogs.count(), role=dialog.get_attribute("role") or "unknown",
            selector="[role='dialog']" if dialog.get_attribute("role") == "dialog" else "text=Diesen Beitrag verwerfen? >> xpath=ancestor::*[.//button][1]",
            buttons=details, screenshot=str(screenshot.resolve()),
        )
        self._emit("DIALOG_BUTTONS", buttons=details)
        if confirm is None:
            return False
        self._emit("DISCARD_CONFIRM_BUTTON_FOUND", text=confirm.inner_text(), selector="dialog button")
        confirm.click()
        self._emit("CONFIRM_CLICKED", text=confirm.inner_text())
        self._emit("DISCARD_CONFIRMED")
        return True

    def _classify_upload_target(self, page) -> str:
        try:
            body = page.locator("body").inner_text().lower()
            file_count = self._fresh_file_input_count(page)
            progress_count = sum(
                page.locator(selector).count()
                for selector in ("[role=progressbar]", "[aria-valuenow]", "[data-e2e*=progress]")
            )
            has_preview = page.locator("video, [data-testid*=preview], [data-e2e*=preview]").count() > 0
            has_discard = any(token in body for token in ("verwerfen", "discard", "abandon", "hủy"))
            has_replace = any(token in body for token in ("ersetzen", "replace", "thay thế"))
            if progress_count or any(token in body for token in ("uploading", "hochladen", "processing", "wird verarbeitet")) and has_preview:
                return "UPLOAD_IN_PROGRESS"
            if file_count and not has_preview and not progress_count:
                return "UPLOAD_EMPTY"
            if has_discard or has_replace or has_preview:
                return "POST_FORM" if has_replace and not has_discard else "STALE_UPLOAD"
            return "UNKNOWN"
        except Exception:
            return "UNKNOWN"

    def _find_file_input(self, page):
        # TikTok hydrates the upload micro-frontend asynchronously.  The body
        # is attached before its native file input exists, so poll the real
        # input instead of failing on the first DOM snapshot.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            for frame in list(page.frames):
                try:
                    locator = frame.locator(self.FILE_INPUT)
                    count = locator.count()
                    self._last_file_count = count
                    if not count:
                        continue
                    first = locator.first
                    dom = {
                        "count": count,
                        "hidden": not first.is_visible(),
                        "accept": first.get_attribute("accept"),
                        "disabled": first.is_disabled(),
                        "data_attributes": first.evaluate(
                            "el => Object.fromEntries([...el.attributes].filter(a => a.name.startsWith('data-')).map(a => [a.name, a.value]))"
                        ),
                    }
                    self._emit("FILE_INPUT_FOUND", frame_url=frame.url, **dom)
                    return first, frame, dom
                except Exception as frame_error:
                    # TikTok replaces the micro-frontend frame while hydrating;
                    # retry against the next live frame instead of masking the
                    # real upload failure as a detached-frame error.
                    logger.debug("TIKTOK_PUBLISH frame_probe_retry error=%s", frame_error)
            page.wait_for_timeout(500)
        self._inspect_upload_dom(page)
        raise PublishError("FILE_INPUT_NOT_FOUND", f"selector={self.FILE_INPUT}")

    def _set_input_files_via_cdp(self, page, frame, path: str) -> None:
        """Set a local path in the browser process, bypassing CDP transfer caps."""
        if frame != page.main_frame:
            raise PublishError("FILE_INPUT_REMOTE_FRAME_UNSUPPORTED")
        session = page.context.new_cdp_session(page)
        session.send("DOM.enable")
        document = session.send("DOM.getDocument", {"depth": -1})
        node = session.send("DOM.querySelector", {
            "nodeId": document["root"]["nodeId"], "selector": self.FILE_INPUT,
        })
        node_id = node.get("nodeId")
        if not node_id:
            raise PublishError("FILE_INPUT_NOT_FOUND", f"selector={self.FILE_INPUT}")
        session.send("DOM.setFileInputFiles", {"nodeId": node_id, "files": [path]})

    def _inspect_upload_dom(self, page) -> None:
        for frame in page.frames:
            files = frame.locator("input[type='file']")
            file_count = files.count()
            inputs = frame.locator("input")
            buttons = frame.locator("button")
            role_buttons = frame.locator("[role='button']")
            data_nodes = frame.locator("[data-e2e], [data-testid], [aria-label]")
            file_attrs = []
            for index in range(min(file_count, 10)):
                item = files.nth(index)
                file_attrs.append(item.evaluate(
                    "el => ({type: el.type, accept: el.accept, disabled: el.disabled, "
                    "hidden: el.hidden, aria: el.getAttribute('aria-label'), "
                    "data: Object.fromEntries([...el.attributes].filter(a => a.name.startsWith('data-')).map(a => [a.name, a.value]))})"
                ))
            button_attrs = buttons.evaluate_all(
                "els => els.slice(0, 40).map(el => ({text: (el.innerText || '').trim().slice(0,120), "
                "aria: el.getAttribute('aria-label'), type: el.getAttribute('type'), "
                "data: Object.fromEntries([...el.attributes].filter(a => a.name.startsWith('data-')).map(a => [a.name, a.value]))}))"
            )
            role_button_attrs = role_buttons.evaluate_all(
                "els => els.slice(0, 40).map(el => ({text: (el.innerText || '').trim().slice(0,120), "
                "aria: el.getAttribute('aria-label'), role: el.getAttribute('role'), "
                "data: Object.fromEntries([...el.attributes].filter(a => a.name.startsWith('data-')).map(a => [a.name, a.value]))}))"
            )
            data_node_attrs = data_nodes.evaluate_all(
                "els => els.slice(0, 80).map(el => ({tag: el.tagName, text: (el.innerText || '').trim().slice(0,120), "
                "aria: el.getAttribute('aria-label'), dataE2e: el.getAttribute('data-e2e'), "
                "testid: el.getAttribute('data-testid')}))"
            )
            body_text = frame.locator("body").inner_text()[:2000]
            body_html = frame.locator("body").evaluate("el => el.innerHTML.slice(0, 8000)")
            self._emit(
                "DOM_INSPECT", frame_url=frame.url, input_count=inputs.count(),
                file_input_count=file_count, file_attributes=file_attrs,
                button_candidates=button_attrs, role_button_candidates=role_button_attrs,
                data_nodes=data_node_attrs, body_text=body_text, body_html=body_html,
            )

    def _log_failure(self, exc, step, page, video_path):
        resolved = Path(video_path).expanduser().resolve(strict=False)
        exists, is_file, size = resolved.exists(), resolved.is_file(), None
        if is_file:
            size = resolved.stat().st_size
        url, title = self._last_url, self._last_title
        if page is not None:
            try: url = page.url
            except Exception: pass
            try: title = page.title()
            except Exception: pass
        logger.exception(
            "TIKTOK_PUBLISH_FAILURE publish_job_id=%s step=%s current_url=%s "
            "exception_type=%s exception=%s selector=%s matching_elements=%s "
            "video_path=%s file_exists=%s file_size=%s page_title=%s",
            self.publish_job_id, step, url, type(exc).__name__, str(exc),
            self.FILE_INPUT, self._last_file_count, str(resolved), exists, size, title,
        )

    def _set_caption(self, page, caption: str) -> None:
        for selector in self.CAPTION_SELECTORS:
            editor = page.locator(selector).first
            if editor.count():
                editor.click()
                editor.press("Control+A")
                editor.fill(caption)
                return
        raise PublishError("CAPTION_EDITOR_NOT_FOUND")

    def _set_private(self, page) -> None:
        controls = []
        for frame in page.frames:
            visibility_control = frame.locator("[data-e2e='video_visibility_container'] [role='combobox']").first
            if visibility_control.count() and visibility_control.is_visible():
                self._emit("PRIVACY_CONTROL_FOUND", selector="[data-e2e='video_visibility_container'] [role='combobox']", role="combobox", tag="BUTTON", text=visibility_control.inner_text(), visible=True, enabled=visibility_control.is_enabled())
                controls.append((visibility_control, frame, {"text": "Wer kann diesen Beitrag sehen"}))
            for selector in ("[role=combobox]", "[aria-haspopup]", "button"):
                locator = frame.locator(selector)
                for index in range(min(locator.count(), 100)):
                    item = locator.nth(index)
                    attrs = self._privacy_attrs(item, frame.url)
                    if "sidebar" in str(attrs.get("outer") or "").lower() or "uploadentrance" in str(attrs.get("outer") or "").lower():
                        continue
                    haystack = " ".join(str(attrs.get(key) or "") for key in ("text", "aria", "value", "data", "outer" )).lower()
                    if any(token in haystack for token in ("privacy", "audience", "watch", "only", "private", "nur", "privat", "sichtbar")):
                        controls.append((item, frame, attrs))
        if not controls:
            raise PublishError("PRIVATE_VISIBILITY_CONTROL_NOT_FOUND")
        control, frame, _ = controls[0]
        menu_options = frame.locator("[data-e2e='video_visibility_container'] [role=option]")
        menu_open = any(menu_options.nth(i).is_visible() for i in range(min(menu_options.count(), 20)))
        if not menu_open:
            control.click()
        self._emit("PRIVACY_MENU_OPEN", control_type="combobox", selector="[data-e2e='video_visibility_container'] [role='combobox']")
        screenshot = Path("state") / "tiktok_debug" / f"publish_{self.publish_job_id}_privacy_open.png"
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot), full_page=True)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            for option_selector in ("[data-e2e='video_visibility_container'] [role=option]", "[role=radio]", "[role=menuitemradio]", "[data-state]", "input[type=radio]"):
                options = frame.locator(option_selector)
                for index in range(min(options.count(), 100)):
                    option = options.nth(index)
                    attrs = self._privacy_attrs(option, frame.url)
                    if self._is_private_option(attrs):
                        self._emit("PRIVACY_OPTION_ONLY_YOU_FOUND", selector=option_selector, text=attrs.get("text"), value=attrs.get("value"), aria_selected=attrs.get("ariaSelected"), data_state=attrs.get("dataState"))
                        option.click()
                        self._emit("PRIVACY_OPTION_CLICKED", selector=option_selector)
                        state_deadline = time.monotonic() + 10
                        while time.monotonic() < state_deadline:
                            if self._privacy_state_is_private(frame):
                                self._emit("PRIVACY_STATE_AFTER", state="ONLY_YOU", evidence="aria-selected=true/data-selected=true")
                                self._emit("PRIVATE_VISIBILITY_CONFIRMED")
                                return
                            page.wait_for_timeout(250)
            dialog = frame.locator("[role='dialog']").last
            if dialog.count() and dialog.is_visible():
                candidate = dialog.get_by_text("Nur du", exact=True).last
                if candidate.count() and candidate.is_visible() and candidate.is_enabled():
                    self._emit("PRIVACY_OPTION_ONLY_YOU_FOUND", selector="[role='dialog'] text=Nur du", text="Nur du")
                    candidate.click()
                    self._emit("PRIVACY_OPTION_CLICKED", selector="[role='dialog'] text=Nur du")
                    state_deadline = time.monotonic() + 10
                    while time.monotonic() < state_deadline:
                        if self._privacy_state_is_private(frame):
                            self._emit("PRIVACY_STATE_AFTER", state="ONLY_YOU", evidence="combobox displayed value")
                            self._emit("PRIVATE_VISIBILITY_CONFIRMED")
                            return
                        page.wait_for_timeout(250)
            page.wait_for_timeout(300)
        raise PublishError("PRIVATE_VISIBILITY_SELECTION_FAILED")

    def _privacy_attrs(self, item, frame_url: str) -> dict[str, Any]:
        return item.evaluate(
            """(el, frameUrl) => ({tag: el.tagName, role: el.getAttribute('role'),
            text: (el.textContent || '').trim().slice(0, 200),
            aria: el.getAttribute('aria-label'), ariaChecked: el.getAttribute('aria-checked'),
            ariaSelected: el.getAttribute('aria-selected'), dataState: el.getAttribute('data-state'),
            dataValue: el.getAttribute('data-value'), value: el.getAttribute('value'), checked: el.checked ?? null,
            disabled: el.disabled ?? false, outer: el.outerHTML.slice(0, 1000), frameUrl})""",
            frame_url,
        )

    def _is_private_option(self, attrs: dict[str, Any]) -> bool:
        if str(attrs.get("dataValue") or "") == "1":
            return True
        semantic = " ".join(str(attrs.get(key) or "") for key in ("aria", "value", "dataValue", "dataState", "text", "outer")).lower()
        return any(token in semantic for token in ("only_you", "only-you", "private", "privat", "nur ich", "nur du", "nur_mir", "sadece"))

    def _privacy_state_is_private(self, frame) -> bool:
        control = frame.locator("[data-e2e='video_visibility_container'] [role=combobox]").first
        if control.count() and control.is_visible():
            displayed = unicodedata.normalize("NFKD", control.inner_text()).encode("ascii", "ignore").decode().lower()
            if any(token in displayed for token in ("nur du", "only you", "private", "privat")):
                return True
        for selector in ("[role=radio]", "[role=option]", "[role=menuitemradio]", "[data-state]", "input[type=radio]", "[role=combobox]"):
            items = frame.locator(selector)
            for index in range(min(items.count(), 100)):
                attrs = self._privacy_attrs(items.nth(index), frame.url)
                selected = attrs.get("ariaChecked") == "true" or attrs.get("ariaSelected") == "true" or attrs.get("dataState") == "checked" or attrs.get("checked") is True
                if selected and self._is_private_option(attrs):
                    return True
        return False

    def _dump_privacy_dom(self, page) -> None:
        selectors = ("[role=radio]", "[role=option]", "[role=combobox]", "[aria-haspopup]", "input[type=radio]", "button")
        for frame in page.frames:
            for selector in selectors:
                items = frame.locator(selector)
                values = [self._privacy_attrs(items.nth(index), frame.url) for index in range(min(items.count(), 120))]
                self._emit("PRIVACY_DOM", selector=selector, frame_url=frame.url, candidates=values)

    def _privacy_screenshot(self, page, phase: str) -> str:
        path = Path("state") / "tiktok_debug" / f"publish_{self.publish_job_id}_privacy_{phase}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path), full_page=True)
        return str(path.resolve())

    def _private_evidence(self, page) -> str:
        for frame in page.frames:
            if self._privacy_state_is_private(frame):
                return "SEMANTIC_PRIVATE_SELECTED"
        raise PublishError("PRIVATE_VISIBILITY_NOT_CONFIRMED")

    def _wait_upload_complete(self, page, profile_id: str, endpoint: str):
        self._emit("UPLOAD_PROGRESS", progress=0)
        try:
            progress = page.locator("text=/Uploading|Đang tải lên/i").first
            progress.wait_for(state="visible", timeout=15_000)
            self._emit("UPLOAD_PROCESSING")
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                percent = self._upload_percent(page)
                if percent is not None:
                    self._emit("UPLOAD_PERCENT", progress=percent)
                    self._emit("TIKTOK_UPLOAD_PERCENT", progress=percent)
                if not progress.is_visible():
                    break
                page.wait_for_timeout(500)
            else:
                raise PublishError("UPLOAD_NOT_COMPLETED", "TikTok upload remained active")
            if progress.is_visible():
                raise PublishError("UPLOAD_NOT_COMPLETED", "TikTok upload indicator is still visible")
            self._emit("WAITING_TIKTOK_UPLOAD", last_event="Waiting for Post button to become enabled")
            page = self._wait_post_button_ready(page, profile_id, endpoint)
            self._emit("UPLOAD_PERCENT", progress=100)
            self._emit("TIKTOK_UPLOAD_PERCENT", progress=100)
            self._emit("UPLOAD_COMPLETE_CONFIRMED", progress=100)
            self._emit("TIKTOK_UPLOAD_COMPLETE", progress=100)
            self._emit("UPLOAD_COMPLETE_REAL", progress=100)
            self._emit("UPLOAD_COMPLETE", progress=100)
            return page
        except Exception as exc:
            if "Connection closed" in str(exc) or "Target page, context or browser has been closed" in str(exc):
                self._emit_cdp_diagnostics(profile_id, endpoint, exc)
                raise PublishError("CDP_DISCONNECTED", str(exc)) from exc
            if isinstance(exc, PublishError):
                raise
            raise PublishError("UPLOAD_NOT_COMPLETED", str(exc)) from exc

    def _upload_percent(self, page) -> float | None:
        for selector in ("[role=progressbar]", "[aria-valuenow]", "[data-e2e*=progress]"):
            items = page.locator(selector)
            for index in range(min(items.count(), 20)):
                item = items.nth(index)
                raw = item.get_attribute("aria-valuenow") or item.get_attribute("data-progress")
                if raw is None:
                    raw = item.inner_text()
                match = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%?", str(raw or ""))
                if match:
                    return max(0, min(100, float(match.group(1))))
        return None

    def _wait_post_button_ready(self, page, profile_id: str, endpoint: str):
        self._emit("WAIT_POST_BUTTON_START", url=page.url, endpoint=endpoint)
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            try:
                for name in self.POST_BUTTON_NAMES:
                    candidate = page.get_by_role("button", name=name, exact=True).first
                    count = candidate.count()
                    visible = bool(count and candidate.is_visible())
                    aria_disabled = candidate.get_attribute("aria-disabled") if visible else None
                    enabled = bool(visible and aria_disabled != "true" and candidate.is_enabled())
                    if count:
                        self._emit("POST_BUTTON_FOUND", name=name, matching_elements=count)
                    if visible and not enabled:
                        self._emit("POST_BUTTON_DISABLED", name=name, aria_disabled=aria_disabled)
                    self._emit("POST_BUTTON_VISIBLE", name=name, matching_elements=count, value=visible)
                    self._emit("POST_BUTTON_ENABLED", name=name, value=enabled, aria_disabled=aria_disabled)
                    if enabled:
                        url_before = page.url
                        page.wait_for_timeout(1000)
                        if page.url == url_before and candidate.is_visible() and candidate.is_enabled():
                            self._emit("READY_TO_POST", url=page.url)
                            return page
                semantic = self._semantic_publish_button(page)
                if semantic is not None:
                    self._log_post_button(semantic, page, semantic=True)
                    self._emit("READY_TO_POST", url=page.url, semantic=True)
                    return page
                page.wait_for_timeout(500)
            except Exception as exc:
                replacement = self._rediscover_upload_page(page)
                if replacement is not None:
                    page = replacement
                    self._emit("PAGE_FOUND", url=page.url, title=page.title(), reason="TARGET_REPLACED")
                    continue
                self._emit_cdp_diagnostics(profile_id, endpoint, exc)
                raise PublishError("CDP_DISCONNECTED", str(exc)) from exc
        raise PublishError("POST_BUTTON_NOT_READY", "TikTok upload finished but Post is not enabled")

    def _rediscover_upload_page(self, page):
        try:
            context = page.context
            for candidate in context.pages:
                if self.UPLOAD_URL.split("?")[0] in candidate.url and not candidate.is_closed():
                    return candidate
        except Exception:
            return None
        return None

    def _emit_cdp_diagnostics(self, profile_id: str, endpoint: str, exc: Exception) -> None:
        try:
            profile_state = self.minha.profile_status(profile_id)
        except Exception as state_error:
            profile_state = {"error": type(state_error).__name__}
        try:
            cdp_open = subprocess.run(
                ["powershell", "-NoProfile", "-Command", f"try {{ Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 '{endpoint}' | Out-Null; 'YES' }} catch {{ 'NO' }}"],
                capture_output=True, text=True, timeout=4,
            ).stdout.strip() or "UNKNOWN"
        except Exception:
            cdp_open = "UNKNOWN"
        self._emit(
            "CDP_DISCONNECTED", endpoint=endpoint, profile_id=profile_id,
            browser_process_alive="UNKNOWN", browser_pid="UNKNOWN",
            browser_pid_after_disconnect="UNKNOWN", cdp_port_open=cdp_open,
            minha_profile_state=profile_state.get("status"),
            disconnect_reason="connection_lost", exception_type=type(exc).__name__,
            exception_message=str(exc),
        )

    def _semantic_publish_button(self, page):
        tokens = ("publish", "post", "submit", "veroff", "publ", "opubl", "发布", "게시", "نشر", "प्रकाश", "yayin", "dang")
        buttons = page.locator("button")
        candidates = []
        for index in range(min(buttons.count(), 200)):
            item = buttons.nth(index)
            if not item.is_visible():
                continue
            attrs = item.evaluate("el => ({text:(el.innerText||'').trim(), aria:el.getAttribute('aria-label'), e2e:el.getAttribute('data-e2e'), testid:el.getAttribute('data-testid'), role:el.getAttribute('role'), disabled:el.getAttribute('disabled'), ariaDisabled:el.getAttribute('aria-disabled')})")
            text_normalized = unicodedata.normalize("NFKD", str(attrs.get("text") or "")).encode("ascii", "ignore").decode().lower()
            haystack = " ".join(str(value or "") for value in attrs.values()).lower()
            if any(word in text_normalized for word in ("verwerf", "discard", "cancel", "huy", "abbrechen")):
                continue
            if not any(token in text_normalized or token in haystack for token in tokens):
                continue
            box = item.bounding_box()
            enabled = item.is_enabled() and attrs.get("ariaDisabled") != "true"
            self._emit(
                "POST_BUTTON_VISIBLE", selector="button", text=attrs.get("text"),
                role=attrs.get("role") or "button", disabled=attrs.get("disabled"),
                aria_disabled=attrs.get("ariaDisabled"), class_name=item.get_attribute("class"),
                bounding_box=box, visible=True, enabled=enabled,
                matching_elements=buttons.count(),
            )
            if enabled and box:
                bottom = box.get("y", 0) + box.get("height", 0) if isinstance(box, dict) else box[1] + box[3]
                candidates.append((bottom, item))
        if candidates:
            return max(candidates, key=lambda pair: pair[0])[1]
        return None

    def _log_post_button(self, button, page, *, semantic: bool = False) -> None:
        box = button.bounding_box()
        self._emit(
            "POST_BUTTON_ENABLED", selector="role=button", text=button.inner_text(),
            role=button.get_attribute("role") or "button",
            disabled=button.get_attribute("disabled"),
            aria_disabled=button.get_attribute("aria-disabled"),
            class_name=button.get_attribute("class"), bounding_box=box,
            visible=button.is_visible(), enabled=button.is_enabled(), semantic=semantic,
        )

    def _post_button(self, page):
        for name in self.POST_BUTTON_NAMES:
            button = page.get_by_role("button", name=name, exact=True).first
            if button.count() and button.is_visible() and button.is_enabled() and button.get_attribute("aria-disabled") != "true":
                return button
        button = self._semantic_publish_button(page)
        if button is not None:
            return button
        raise PublishError("POST_BUTTON_NOT_READY")

    def _verify_success(self, page) -> PostedResult:
        evidence = page.get_by_text(self.SUCCESS_TEXT).first
        try:
            evidence.wait_for(state="visible", timeout=90_000)
        except Exception as exc:
            raise PublishError(
                "POST_SUCCESS_NOT_VERIFIED",
                "No explicit TikTok publication confirmation",
            ) from exc
        text = evidence.inner_text().strip()[:200]
        self._emit("POST_SUCCESS_UI_FOUND", text=text)
        method = f"SUCCESS_TEXT:{text}"
        url = page.url
        match = re.search(r"/video/(\d+)", url)
        self._emit("POST_ID", value=match.group(1) if match else None)
        self._emit("POST_URL", value=url if match else None)
        self._emit("POST_VERIFICATION_METHOD", value=method)
        return PostedResult(
            verification_method=method,
            tiktok_post_id=match.group(1) if match else None,
            tiktok_post_url=url if match else None,
        )


class TikTokPublisher:
    def __init__(
        self, state: StateStore, channels: ChannelStore, minha: MinHaClient,
        automation: TikTokUploadAutomation | None = None, notifier=None,
    ):
        self.state = state
        self.channels = channels
        self.minha = minha
        self.store = PublishStore(state)
        self.automation = automation or TikTokUploadAutomation(minha)
        self.notifier = notifier
        self.debug_upload_only = os.getenv("TIKTOK_DEBUG_UPLOAD_ONLY", "0") == "1"
        self.privacy_debug_only = os.getenv("TIKTOK_DEBUG_PRIVACY_ONLY", "0") == "1"
        self.ready_only = os.getenv("TIKTOK_DEBUG_READY_ONLY", "0") == "1"

    def create(self, processing_job_id: int, channel_id: str, video_path: str) -> sqlite3.Row:
        job = self._validate_provenance(processing_job_id, channel_id, video_path)
        try:
            channel = next(item for item in self.channels.list() if item.channel_id == channel_id)
        except (StopIteration, ChannelStoreError):
            raise PublishError("CHANNEL_NOT_FOUND", status_code=404)
        profile_id = job["minha_profile_id"]
        if not profile_id or channel.minha_profile_id != profile_id:
            raise PublishError("MINHA_PROFILE_MAPPING_MISSING")
        caption = str(job["video_title"] or "").strip()
        if not caption:
            raise PublishError("CAPTION_MISSING")
        return self.store.create(
            processing_job_id, channel_id, profile_id, video_path, caption,
        )

    def clear_completed(self) -> int:
        return self.store.clear_terminal(("DONE", "CANCELLED"))

    def clear_failed(self) -> int:
        return self.store.clear_terminal(("FAILED", "FAILED_PRE_POST", "SESSION_LOST_BEFORE_POST", "BLOCKED", "CANCELLED"))

    def clear_history(self) -> int:
        active = (
            "QUEUED", "VALIDATING", "IDENTITY_CHECK", "OPENING_BROWSER",
            "UPLOADING", "CONFIGURING_POST", "POSTING", "VERIFYING",
            "READY_TO_POST", "READY_FOR_POST", "POST_RESULT_UNCERTAIN",
        )
        with self.store._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            marks = ",".join("?" for _ in active)
            rows = db.execute(
                f"""SELECT p.id FROM publish_jobs p
                    LEFT JOIN publish_receipts r ON r.publish_job_id=p.id
                    WHERE p.status NOT IN ({marks}) AND r.publish_job_id IS NULL""", active
            ).fetchall()
            ids = [row["id"] for row in rows]
            if ids:
                id_marks = ",".join("?" for _ in ids)
                db.execute(f"DELETE FROM publish_receipts WHERE publish_job_id IN ({id_marks})", ids)
                db.execute(f"DELETE FROM publish_jobs WHERE id IN ({id_marks})", ids)
            return len(ids)

    def handle_processing_done(self, processing_job_id: int) -> list[sqlite3.Row]:
        """Create/run ordered publish jobs once for one completed processing job."""
        job = self.state.processing_job(processing_job_id)
        if not job or not job["minha_profile_id"]:
            return []
        try:
            channel = next(
                item for item in self.channels.list()
                if item.channel_id == job["source_channel_id"]
            )
        except (StopIteration, ChannelStoreError):
            return []
        if not channel.minha_profile_id or channel.minha_profile_id != job["minha_profile_id"]:
            return []
        try:
            outputs = json.loads(job["processed_files_json"] or "null")
        except json.JSONDecodeError:
            outputs = None
        if not isinstance(outputs, list):
            outputs = [job["processed_file_path"]] if job["processed_file_path"] else []
        results = []
        for value in outputs:
            video_path = str(value or "")
            if not video_path:
                continue
            key = _idempotency_key(
                processing_job_id, video_path, job["minha_profile_id"],
            )
            existing = self.store.by_idempotency_key(key)
            if existing:
                if existing["status"] == "QUEUED":
                    if int(existing["attempt_count"] or 0) >= 1:
                        logger.info(
                            "RETRY_REASON publish_job_id=%s reason=MAX_ATTEMPTS_1",
                            existing["id"],
                        )
                        continue
                    logger.info(
                        "RETRY_SCHEDULED publish_job_id=%s reason=PROCESSING_DONE_REPLAY",
                        existing["id"],
                    )
                    results.append(self.run(existing["id"], dry_run=False))
                continue
            try:
                publish_job = self.create(
                    processing_job_id, job["source_channel_id"], video_path,
                )
            except PublishError as exc:
                logger.warning(
                    "TIKTOK_AUTO_PUBLISH_CREATE_BLOCKED processing_job_id=%s reason=%s",
                    processing_job_id, exc.code,
                )
                continue
            results.append(self.run(publish_job["id"], dry_run=False))
        return results

    def _validate_provenance(
        self, processing_job_id: int, channel_id: str, video_path: str,
    ) -> sqlite3.Row:
        job = self.state.processing_job(processing_job_id)
        if not job:
            raise PublishError("PROCESSING_JOB_NOT_FOUND", status_code=404)
        if job["source_channel_id"] != channel_id:
            raise PublishError("CHANNEL_MISMATCH")
        if job["status"] not in {"COMPLETED", "DONE"} or job["process_state"] != "DONE":
            raise PublishError("PROCESSING_JOB_NOT_COMPLETED")
        try:
            outputs = json.loads(job["processed_files_json"] or "null")
        except json.JSONDecodeError:
            outputs = None
        if not isinstance(outputs, list):
            outputs = [job["processed_file_path"]] if job["processed_file_path"] else []
        identities = {_path_identity(str(value)) for value in outputs if value}
        if _path_identity(video_path) not in identities:
            raise PublishError("OUTPUT_NOT_OWNED_BY_PROCESSING_JOB")
        path = Path(video_path)
        try:
            if not path.is_file() or path.stat().st_size <= 0:
                raise PublishError("PROCESSED_OUTPUT_MISSING")
        except OSError as exc:
            raise PublishError("PROCESSED_OUTPUT_MISSING") from exc
        return job

    def run(self, job_id: int, *, dry_run: bool = True) -> sqlite3.Row:
        existing = self.store.get(job_id)
        resume_ready = bool(existing and existing["status"] == "READY_TO_POST")
        job = existing if resume_ready else self.store.claim(job_id)
        started_here = False
        upload_active = False
        post_boundary_crossed = False
        try:
            logger.info("JOB_STATE_CHANGE publish_job_id=%s attempt=%s state=CLAIMED", job_id, job["attempt_count"])
            self._validate_provenance(
                job["processing_job_id"], job["channel_id"], job["video_path"],
            )
            self.store.update(job_id, "IDENTITY_CHECK", current_step="IDENTITY_CHECK")
            profile, probe = self._fresh_identity(job)
            expected_uid = str(profile.get("expected_tiktok_uid") or "")
            current_uid = str(probe.get("tiktok_uid") or "")
            username = probe.get("tiktok_username")
            if not isinstance(username, str) or not username.strip():
                raise PublishError("TIKTOK_USERNAME_NOT_DETECTED")
            self.store.update(
                job_id, "OPENING_BROWSER", current_step="OPENING_BROWSER",
                expected_tiktok_uid=expected_uid,
                current_tiktok_uid=current_uid, tiktok_username=username,
            )
            initial_running = self.minha.profile_status(job["minha_profile_id"]).get("status") == "running"
            logger.info("PROFILE_INITIAL_STATE publish_job_id=%s attempt=%s state=%s", job_id, job["attempt_count"], "RUNNING" if initial_running else "STOPPED")
            if not initial_running:
                logger.info("PROFILE_START_REQUEST publish_job_id=%s attempt=%s", job_id, job["attempt_count"])
                self.minha.launch_profile(job["minha_profile_id"])
                started_here = True
                logger.info("PROFILE_STARTED publish_job_id=%s attempt=%s", job_id, job["attempt_count"])
            self.store.update(job_id, "UPLOADING")

            def progress(step, progress, details):
                nonlocal upload_active
                if step in {"UPLOAD_STARTED", "TIKTOK_UPLOAD_STARTED", "UPLOAD_PROGRESS", "TIKTOK_UPLOAD_PERCENT"}:
                    upload_active = True
                if step in {"UPLOAD_COMPLETE_REAL", "TIKTOK_UPLOAD_COMPLETE"}:
                    upload_active = False
                current = self.store.get(job_id)
                if current:
                    values = {
                        "current_step": step,
                        "progress_percent": progress if progress is not None else current["progress_percent"],
                    }
                    if step == "FILE_ATTACHED":
                        values["upload_attach_count"] = int(current["upload_attach_count"] or 0) + 1
                        values["file_size"] = details.get("file_size")
                    self.store.update(job_id, current["status"], **values)

            if isinstance(self.automation, TikTokUploadAutomation):
                self.automation.publish_job_id = job_id
                self.automation.progress_callback = progress
                self.automation.attempt_number = int(job["attempt_count"] or 1)

            if resume_ready:
                def before_post_ready(prepared: PreparedPost) -> None:
                    self.store.update(job_id, "POSTING", current_step="POSTING")
                def after_post_ready() -> None:
                    self.store.update(job_id, "VERIFYING")
                def mark_ready_post_click() -> None:
                    nonlocal post_boundary_crossed
                    post_boundary_crossed = True
                    current = self.store.get(job_id)
                    self.store.update(
                        job_id, "POSTING", current_step="POST_CLICK_START",
                        post_click_count=int(current["post_click_count"] or 0) + 1,
                        post_attempted_at=utc_now(),
                    )
                if isinstance(self.automation, TikTokUploadAutomation):
                    self.automation.post_click_callback = mark_ready_post_click
                posted = self.automation.publish_ready(
                    job["minha_profile_id"], before_post_ready, after_post_ready,
                )
                done = self.store.complete_with_receipt(
                    job_id, verification_method=posted.verification_method,
                    tiktok_post_id=posted.tiktok_post_id,
                    tiktok_post_url=posted.tiktok_post_url,
                )
                try:
                    self._notify_posted(done, profile)
                except Exception as exc:
                    logger.warning("TIKTOK_TELEGRAM_FAILED error_type=%s", type(exc).__name__)
                return done

            if self.debug_upload_only:
                prepared = self.automation.prepare(
                    job["minha_profile_id"], job["video_path"], job["caption"],
                )
                return self.store.update(
                    job_id, "UPLOAD_COMPLETE", current_step="UPLOAD_COMPLETE_REAL",
                    progress_percent=100,
                )

            if self.privacy_debug_only:
                prepared = self.automation.prepare(
                    job["minha_profile_id"], job["video_path"], job["caption"],
                )
                return self.store.update(
                    job_id, "VISIBILITY_ONLY_YOU_CONFIRMED",
                    current_step="VISIBILITY_ONLY_YOU_CONFIRMED", progress_percent=100,
                )

            if self.ready_only:
                prepared = self.automation.prepare(
                    job["minha_profile_id"], job["video_path"], job["caption"],
                )
                return self.store.update(
                    job_id, "READY_TO_POST", current_step="READY_TO_POST",
                    progress_percent=100,
                )

            def build_check(prepared: PreparedPost) -> dict[str, Any]:
                if not prepared.upload_completed:
                    raise PublishError("UPLOAD_NOT_COMPLETED")
                if prepared.visibility != PRIVATE_VISIBILITY:
                    raise PublishError("PRIVATE_VISIBILITY_NOT_CONFIRMED")
                return {
                    "publish_job": job_id,
                    "processing_job": job["processing_job_id"],
                    "channel": job["channel_id"],
                    "minha_profile_id": job["minha_profile_id"],
                    "profile_name": profile.get("name"),
                    "username": username,
                    "expected_uid": expected_uid,
                    "current_uid": current_uid,
                    "identity": "MATCH",
                    "video": job["video_path"],
                    "caption": job["caption"],
                    "visibility": PRIVATE_VISIBILITY,
                    "upload_completed": True,
                    "idempotency_key": job["idempotency_key"],
                    "existing_receipt": bool(self.store.receipt(job_id)),
                    "other_publish_jobs_running": self.store.running_count(job_id),
                    "visibility_evidence": prepared.visibility_evidence,
                    "post_clicked": False,
                }

            if dry_run:
                prepared = self.automation.prepare(
                    job["minha_profile_id"], job["video_path"], job["caption"],
                )
                self.store.update(job_id, "CONFIGURING_POST")
                check = build_check(prepared)
                logger.info("PRE-PUBLISH CHECK %s", json.dumps(check, ensure_ascii=False))
                return self.store.update(
                    job_id, "READY_FOR_POST", current_step="READY_FOR_POST",
                    pre_publish_check_json=json.dumps(check, ensure_ascii=False),
                )

            def before_post(prepared: PreparedPost) -> None:
                self.store.update(job_id, "CONFIGURING_POST")
                check = build_check(prepared)
                logger.info("PRE-PUBLISH CHECK %s", json.dumps(check, ensure_ascii=False))
                logger.info("TIKTOK_POST_CAPTION caption=%s", job["caption"])
                self.store.update(
                    job_id, "POSTING",
                    current_step="POSTING",
                    pre_publish_check_json=json.dumps(check, ensure_ascii=False),
                )
                if not isinstance(self.automation, TikTokUploadAutomation):
                    mark_post_click()

            def after_post() -> None:
                self.store.update(job_id, "VERIFYING")

            def mark_post_click() -> None:
                nonlocal post_boundary_crossed
                post_boundary_crossed = True
                current = self.store.get(job_id)
                self.store.update(
                    job_id, "POSTING", current_step="POST_CLICK_START",
                    post_click_count=int(current["post_click_count"] or 0) + 1,
                    post_attempted_at=utc_now(),
                )

            if isinstance(self.automation, TikTokUploadAutomation):
                self.automation.post_click_callback = mark_post_click
            posted = self.automation.publish(
                job["minha_profile_id"], job["video_path"], job["caption"],
                before_post, after_post,
            )
            done = self.store.complete_with_receipt(
                job_id, verification_method=posted.verification_method,
                tiktok_post_id=posted.tiktok_post_id,
                tiktok_post_url=posted.tiktok_post_url,
            )
            try:
                self._notify_posted(done, profile)
            except Exception as exc:
                logger.warning("TIKTOK_TELEGRAM_FAILED error_type=%s", type(exc).__name__)
            return done
        except PublishError as exc:
            if post_boundary_crossed:
                saved = self.store.update(
                    job_id, "POST_RESULT_UNCERTAIN", current_step="POST_RESULT_UNCERTAIN",
                    failure_reason=_error_text(exc),
                )
                self._notify_uncertain(saved)
                return saved
            status = "BLOCKED" if exc.code in {
                "MINHA_UNAVAILABLE", "MINHA_PROFILE_NOT_FOUND", "UID_UNLOCKED",
                "ACCOUNT_MISMATCH", "NOT_LOGGED_IN", "UID_NOT_DETECTED",
                "PROBE_ERROR", "UNKNOWN_IDENTITY_STATE", "PRIVATE_VISIBILITY_NOT_CONFIRMED",
                "PRIVATE_VISIBILITY_CONTROL_NOT_FOUND", "PRIVATE_VISIBILITY_OPTION_NOT_FOUND",
                "PRIVATE_VISIBILITY_SELECTION_FAILED", "TIKTOK_USERNAME_NOT_DETECTED",
            } else "FAILED"
            if status == "FAILED" and not post_boundary_crossed:
                status = "SESSION_LOST_BEFORE_POST" if exc.code == "CDP_DISCONNECTED" else "FAILED_PRE_POST"
            saved = self.store.update(
                job_id, status,
                current_step=status,
                failure_reason=_error_text(exc),
            )
            if status == "BLOCKED":
                self._notify_blocked(saved)
            return saved
        except MinHaUnavailable:
            saved = self.store.update(
                job_id, "BLOCKED", current_step="BLOCKED", failure_reason="MINHA_UNAVAILABLE",
            )
            self._notify_blocked(saved)
            return saved
        except Exception as exc:
            logger.exception("TIKTOK_PUBLISH_FAILED error_type=%s error=%s", type(exc).__name__, exc)
            if post_boundary_crossed:
                saved = self.store.update(
                    job_id, "POST_RESULT_UNCERTAIN", current_step="POST_RESULT_UNCERTAIN",
                    failure_reason=f"{type(exc).__name__}: {exc}",
                )
                self._notify_uncertain(saved)
                return saved
            return self.store.update(
                job_id, "FAILED", current_step="FAILED",
                failure_reason=f"{type(exc).__name__}: {exc}",
            )
        finally:
            if started_here:
                if upload_active or (self.ready_only and self.store.get(job_id) and self.store.get(job_id)["status"] == "READY_TO_POST"):
                    logger.warning("PROFILE_STOP_DEFERRED publish_job_id=%s reason=UPLOAD_ACTIVE", job_id)
                else:
                    logger.info("PROFILE_STOP_REQUEST publish_job_id=%s attempt=%s", job_id, job["attempt_count"])
                    logger.info("PROFILE_STOP_CALLER publish_job_id=%s caller=TikTokPublisher.run.finally", job_id)
                    try:
                        self.minha.stop_profile(job["minha_profile_id"])
                        logger.info("PROFILE_STOP_REASON publish_job_id=%s reason=TERMINAL_RESULT", job_id)
                    except MinHaUnavailable:
                        logger.warning("MINHA_PROFILE_RESTORE_FAILED profile_id=%s", job["minha_profile_id"])

    def _notify_posted(self, job: sqlite3.Row, profile: dict[str, Any]) -> None:
        receipt = self.store.receipt(job["id"])
        processing = self.state.processing_job(job["processing_job_id"])
        post = receipt["tiktok_post_url"] or receipt["tiktok_post_id"] or "Không có"
        self._send_message(
            "✅ TIKTOK POSTED\n\n"
            f"Channel: {processing['channel_name'] if processing else job['channel_id']}\n"
            f"TikTok: @{job['tiktok_username']}\n"
            f"Profile: {profile.get('name')}\nVideo: {job['caption']}\n"
            f"Visibility: Only you\nTime: {receipt['posted_at']}\nPost: {post}\nStatus: POSTED"
        )

    def _notify_blocked(self, job: sqlite3.Row) -> None:
        processing = self.state.processing_job(job["processing_job_id"])
        self._send_message(
            "⛔ TIKTOK PUBLISH BLOCKED\n"
            f"Channel: {processing['channel_name'] if processing else job['channel_id']}\n"
            f"Profile: {job['minha_profile_id']}\nReason: {job['failure_reason']}"
        )

    def _notify_uncertain(self, job: sqlite3.Row) -> None:
        self._send_message(
            "⚠️ TIKTOK POST RESULT UNCERTAIN\n"
            f"Channel: {job['channel_id']}\nProfile: {job['minha_profile_id']}\n"
            f"Reason: {job['failure_reason']}\nNO AUTO RETRY"
        )

    def _send_message(self, text: str) -> None:
        if not self.notifier:
            return
        try:
            self.notifier.send_message(text)
        except Exception as exc:
            logger.warning("TIKTOK_TELEGRAM_FAILED error_type=%s", type(exc).__name__)

    def _fresh_identity(self, job: sqlite3.Row) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            profile = self.minha.get_profile(job["minha_profile_id"])
            if not profile:
                raise PublishError("MINHA_PROFILE_NOT_FOUND")
            expected = profile.get("expected_tiktok_uid")
            if not isinstance(expected, str) or not expected.isdecimal():
                raise PublishError("UID_UNLOCKED")
            probe = self.minha.probe_tiktok(job["minha_profile_id"])
            refreshed = self.minha.get_profile(job["minha_profile_id"])
            if not refreshed:
                raise PublishError("MINHA_PROFILE_NOT_FOUND")
        except MinHaUnavailable as exc:
            raise PublishError("MINHA_UNAVAILABLE", status_code=503) from exc
        status = probe.get("status")
        if status == "UID_NOT_DETECTED":
            raise PublishError("UID_NOT_DETECTED")
        if status == "ERROR":
            raise PublishError("PROBE_ERROR")
        if status == "NOT_LOGGED_IN" or not probe.get("logged_in"):
            raise PublishError("NOT_LOGGED_IN")
        if status != "DETECTED":
            raise PublishError("UNKNOWN_IDENTITY_STATE")
        current = probe.get("tiktok_uid")
        if not isinstance(current, str) or not current.isdecimal():
            raise PublishError("UID_NOT_DETECTED")
        if current != expected:
            raise PublishError("ACCOUNT_MISMATCH")
        if refreshed.get("tiktok_account_match") != "MATCH":
            raise PublishError("ACCOUNT_MISMATCH")
        return refreshed, probe
