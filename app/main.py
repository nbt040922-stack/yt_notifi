from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .channel_store import ChannelStore, ChannelStoreError
from .channel_resolver import ChannelResolveError, ResolvedChannel, resolve_channel
from .cleanup_worker import CleanupWorker
from .config import Channel, Settings, load_team_members
from .detector import resume_notifications
from .download_worker import DownloadHandoffWorker
from .nas_sync_worker import NasSyncWorker
from .poller import ChannelPoller
from .process_worker import ProcessHandoffWorker
from .processing_control import ProcessingControl
from .state import StateStore
from .telegram import TelegramNotifier

DASHBOARD = Path(__file__).with_name("dashboard.html")


class ChannelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel_id: str | None = Field(default=None, max_length=500)
    url: str | None = Field(default=None, max_length=500)
    name: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    owner_id: str | None = None


class ChannelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    cut_enabled: StrictBool | None = None
    name: str | None = None


class ChannelResolve(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=500)


class ChannelBulkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channels: list[str] = Field(min_length=1, max_length=500)
    owner_id: str = Field(min_length=1, max_length=50)


class NotifyChannelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool | None = None
    cut_enabled: StrictBool | None = None
    owner_id: str | None = Field(default=None, max_length=50)


class ProcessingControlUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    silence_engine_enabled: StrictBool


def configure_logging() -> None:
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    logger = logging.getLogger("yt_notifi")
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    for handler in (logging.StreamHandler(), logging.FileHandler(log_dir / "yt_notifi.log", encoding="utf-8")):
        handler.setFormatter(formatter)
        logger.addHandler(handler)


def create_app(
    settings: Settings | None = None,
    state: StateStore | None = None,
    notifier: TelegramNotifier | None = None,
    channel_store: ChannelStore | None = None,
    channel_resolver=None,
    processing_control=None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    team_members = load_team_members(settings.team_members_file)
    state = state or StateStore(settings.state_db)
    notifier = notifier or TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    channel_store = channel_store or ChannelStore(settings.channels_file, team_members)
    channel_resolver = channel_resolver or resolve_channel
    def processing_channel_ids():
        try:
            return {channel.channel_id for channel in channel_store.enabled() if channel.cut_enabled}
        except ChannelStoreError:
            return set()

    migration = {"imported": 0, "conflicts": 0, "unresolved": 0}
    try:
        channels = channel_store.enabled()
    except ChannelStoreError:
        channels = []
    poller = ChannelPoller(
        settings, state, notifier, channels, channel_loader=channel_store.enabled,
        processing_channel_loader=processing_channel_ids, team_members=team_members,
    )
    download_worker = DownloadHandoffWorker(settings, state)
    processing_control = processing_control or ProcessingControl(settings, state)
    process_worker = ProcessHandoffWorker(settings, state, control=processing_control)
    nas_sync_worker = NasSyncWorker(settings, state)
    cleanup_worker = CleanupWorker(settings, state)

    async def notification_retry_loop() -> None:
        while True:
            try:
                await asyncio.to_thread(resume_notifications, state, notifier, poller.names)
            except Exception as exc:
                logging.getLogger("yt_notifi").error("TELEGRAM_RETRY error_type=%s", type(exc).__name__)
            await asyncio.sleep(60)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        migration.update(channel_store.migrate_notify_channels(state.notify_channels()))
        poller.refresh_channels()
        if migration["unresolved"] or migration["conflicts"]:
            logging.getLogger("yt_notifi").warning(
                "LEGACY_NOTIFY_MIGRATION imported=%s conflicts=%s unresolved=%s",
                migration["imported"], migration["conflicts"], migration["unresolved"],
            )
        if settings.enable_background_tasks and not poller.executable:
            raise RuntimeError("yt-dlp is required for YT_NOTIFI polling")
        stop = asyncio.Event()
        tasks = (
            [
                asyncio.create_task(notification_retry_loop()),
                asyncio.create_task(poller.run(stop)),
                asyncio.create_task(download_worker.run(stop)),
                asyncio.create_task(processing_control.run(stop)),
                asyncio.create_task(process_worker.run(stop)),
                asyncio.create_task(nas_sync_worker.run(stop)),
                asyncio.create_task(cleanup_worker.run(stop)),
            ]
            if settings.enable_background_tasks else []
        )
        yield
        stop.set()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="YT_NOTIFI", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.exception_handler(ChannelStoreError)
    async def channel_error(_request: Request, exc: ChannelStoreError) -> JSONResponse:
        return JSONResponse({"error": exc.code, "message": exc.message}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _exc: RequestValidationError) -> JSONResponse:
        return JSONResponse({"error": "INVALID_REQUEST", "message": "Yêu cầu không hợp lệ."}, status_code=400)

    def channel_payload(channel) -> dict:
        row = state.get_poll_state(channel.channel_id)
        failures = row["consecutive_failures"] if row else 0
        status = (
            "Disabled"
            if not channel.enabled
            else "Waiting for first poll"
            if not row or not row["initialized"]
            else "Retrying"
            if failures
            else "Healthy"
        )
        return {
            "channel_id": channel.channel_id,
            "name": channel.name,
            "enabled": channel.enabled,
            "owner_id": channel.owner_id,
            "cut_enabled": channel.cut_enabled,
            "last_poll_at": row["last_poll_at"] if row else None,
            "last_success_at": row["last_success_at"] if row else None,
            "latest_seen_video_id": row["latest_seen_video_id"] if row else None,
            "failures": failures,
            "status": status,
        }

    def notify_channel_payload(row) -> dict:
        payload = channel_payload(Channel(
            row["channel_id"], row["name"], bool(row["enabled"]), row["owner_id"] or ""
        ))
        payload.update({
            "id": row["id"], "source_url": row["source_url"], "created_at": row["created_at"],
            "cut_enabled": bool(row["cut_enabled"]), "owner_id": row["owner_id"],
        })
        return payload

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        return DASHBOARD.read_text(encoding="utf-8")

    @app.get("/health")
    def health() -> dict[str, str | int]:
        try:
            enabled_count = len(channel_store.enabled())
        except ChannelStoreError:
            enabled_count = len(poller.channels)
        return {"status": "ok", "service": "YT_NOTIFI", "enabled_channels": enabled_count}

    @app.get("/api/status")
    def api_status() -> dict:
        try:
            enabled_count = len(channel_store.enabled())
            config_error = None
        except ChannelStoreError as exc:
            enabled_count = 0
            config_error = exc.message
        return {
            "watcher": "RUNNING",
            "poll_interval_seconds": settings.poll_interval_seconds,
            "ytdlp": "READY" if poller.executable else "MISSING",
            "telegram": "CONFIGURED" if settings.telegram_bot_token and settings.telegram_chat_id else "NOT CONFIGURED",
            "enabled_channels": enabled_count,
            "last_new_video": state.latest_activity()["last_new_video"],
            "config_error": config_error,
            "legacy_notify_migration": migration,
        }

    @app.get("/api/channels")
    def api_channels() -> list[dict]:
        return [channel_payload(channel) for channel in channel_store.list()]

    @app.get("/api/team-members")
    def api_team_members() -> list[dict]:
        return [member.__dict__ for member in team_members]

    @app.get("/api/jobs")
    def api_jobs() -> list[dict]:
        return [dict(job) for job in state.processing_jobs()]

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: int) -> dict:
        result = state.cancel_processing_job(job_id)
        if result == "JOB_NOT_FOUND":
            return JSONResponse(
                {"error": result, "message": "Không tìm thấy job."}, status_code=404,
            )
        if result == "JOB_NOT_CANCELLABLE":
            return JSONResponse(
                {"error": result, "message": "Job này không thể hủy."}, status_code=409,
            )
        logging.getLogger("yt_notifi").info("JOB_MANUAL_CANCEL job_id=%s", job_id)
        return dict(state.processing_job(job_id))

    @app.post("/api/jobs/{job_id}/retry")
    def retry_job(job_id: int) -> dict:
        job = state.processing_job(job_id)
        source = Path(job["downloaded_file_path"]) if job and job["downloaded_file_path"] else None
        try:
            source_exists = bool(
                job and job["download_state"] == "DONE" and source
                and source.is_file() and source.stat().st_size > 0
            )
        except OSError:
            source_exists = False
        result = state.retry_processing_job(job_id, source_exists=source_exists)
        errors = {
            "JOB_NOT_FOUND": (404, "Không tìm thấy job."),
            "JOB_NOT_RETRYABLE": (409, "Job này không thể thử lại."),
            "JOB_ALREADY_RUNNING": (409, "Job đang chạy hoặc đã được xếp lại."),
        }
        if result in errors:
            status_code, message = errors[result]
            return JSONResponse({"error": result, "message": message}, status_code=status_code)
        logging.getLogger("yt_notifi").info(
            "JOB_MANUAL_RETRY job_id=%s resume=%s", job_id, result,
        )
        return dict(state.processing_job(job_id))

    @app.get("/api/processing-control")
    def get_processing_control() -> dict:
        return processing_control.snapshot()

    @app.patch("/api/processing-control")
    def update_processing_control(payload: ProcessingControlUpdate) -> dict:
        return processing_control.request(payload.silence_engine_enabled)

    @app.post("/api/jobs/{job_id}/retry-nas-sync")
    def retry_nas_sync(job_id: int) -> dict[str, str]:
        job = state.processing_job(job_id)
        if not job:
            return JSONResponse({"error": "JOB_NOT_FOUND", "message": "Không tìm thấy job."}, status_code=404)
        if not state.retry_nas_sync(job_id):
            return JSONResponse({"error": "NAS_SYNC_NOT_RETRYABLE", "message": "Job chưa cần đồng bộ lại."}, status_code=409)
        return {"status": "scheduled"}

    @app.get("/api/notify-channels")
    def api_notify_channels() -> list[dict]:
        return [notify_channel_payload(row) for row in state.notify_channels()]

    @app.post("/api/channels/bulk")
    async def add_channels_bulk(payload: ChannelBulkCreate) -> dict:
        if payload.owner_id not in {member.id for member in team_members}:
            raise ChannelStoreError("INVALID_OWNER_ID", "Thành viên không hợp lệ.")
        semaphore = asyncio.Semaphore(settings.notify_resolve_concurrency)
        unique_inputs = list(dict.fromkeys(
            value.strip() for value in payload.channels if value.strip() and len(value.strip()) <= 500
        ))

        async def resolve(value: str):
            async with semaphore:
                try:
                    if channel_resolver is resolve_channel:
                        return await asyncio.to_thread(channel_resolver, settings, value, resolve_title=True), None
                    return await asyncio.to_thread(channel_resolver, settings, value), None
                except ChannelResolveError as exc:
                    logging.getLogger("yt_notifi").info(
                        "CHANNEL_BULK_RESOLVE_FAILED error_type=%s", type(exc).__name__
                    )
                    return None, str(exc)
                except Exception as exc:
                    logging.getLogger("yt_notifi").info(
                        "CHANNEL_BULK_RESOLVE_FAILED error_type=%s", type(exc).__name__
                    )
                    return None, "Could not resolve YouTube channel ID."

        resolved = dict(zip(unique_inputs, await asyncio.gather(*(resolve(value) for value in unique_inputs))))
        results, seen_inputs, seen_channels = [], set(), set()
        for original in payload.channels:
            value = original.strip()
            if not value:
                results.append({
                    "input": original, "status": "FAILED", "channel_id": None,
                    "name": None, "error": "Empty channel reference.",
                })
                continue
            if len(value) > 500:
                results.append({
                    "input": original, "status": "FAILED", "channel_id": None,
                    "name": None, "error": "Channel reference is too long.",
                })
                continue
            item, error = resolved[value]
            if error:
                results.append({
                    "input": original, "status": "FAILED", "channel_id": None,
                    "name": None, "error": error,
                })
                continue
            name = item.title or item.channel_id
            duplicate = value in seen_inputs or item.channel_id in seen_channels
            try:
                channel = channel_store.add(
                    item.channel_id, name, owner_id=payload.owner_id, cut_enabled=False,
                )
                state.reset_poll_baseline(item.channel_id)
                status, item_error = "ADDED", None
            except ChannelStoreError as exc:
                channel = next(
                    (row for row in channel_store.list() if row.channel_id == item.channel_id), None
                )
                owner = next((member for member in team_members if channel and member.id == channel.owner_id), None)
                status = "ALREADY_EXISTS" if exc.code == "CHANNEL_ALREADY_EXISTS" else "FAILED"
                item_error = f"Kênh này đang thuộc {owner.display_name}." if owner else exc.message
            if duplicate:
                status = "ALREADY_EXISTS"
            results.append({
                "input": original, "status": status, "channel_id": item.channel_id,
                "name": channel.name if channel else name, "error": item_error,
            })
            seen_inputs.add(value)
            seen_channels.add(item.channel_id)
        return {
            "total": len(results),
            "added": sum(item["status"] == "ADDED" for item in results),
            "existing": sum(item["status"] == "ALREADY_EXISTS" for item in results),
            "failed": sum(item["status"] == "FAILED" for item in results),
            "results": results,
        }

    @app.patch("/api/notify-channels/{channel_id}")
    def update_notify_channel(channel_id: str, payload: NotifyChannelUpdate) -> dict:
        before = next((row for row in state.notify_channels() if row["channel_id"] == channel_id), None)
        if not before:
            return JSONResponse({"error": "CHANNEL_NOT_FOUND", "message": "Channel not found."}, status_code=404)
        owner_ids = {member.id for member in team_members}
        if payload.owner_id is not None and payload.owner_id not in owner_ids:
            return JSONResponse({"error": "INVALID_OWNER", "message": "Owner không hợp lệ."}, status_code=400)
        cut_enabled = payload.cut_enabled if payload.cut_enabled is not None else bool(before["cut_enabled"])
        owner_id = payload.owner_id or before["owner_id"]
        if cut_enabled and not owner_id:
            return JSONResponse({"error": "OWNER_REQUIRED", "message": "Phải chọn người nhận output."}, status_code=400)
        row = state.update_notify_channel(
            channel_id, payload.enabled, payload.cut_enabled, payload.owner_id
        )
        silence_ids = processing_channel_ids()
        if payload.enabled and before and not before["enabled"] and channel_id not in silence_ids:
            state.reset_poll_baseline(channel_id)
        return notify_channel_payload(row)

    @app.delete("/api/notify-channels/{channel_id}")
    def delete_notify_channel(channel_id: str) -> dict[str, str]:
        if not state.delete_notify_channel(channel_id):
            return JSONResponse({"error": "CHANNEL_NOT_FOUND", "message": "Channel not found."}, status_code=404)
        return {"status": "removed"}

    @app.post("/api/channels/resolve")
    async def resolve_channel_url(payload: ChannelResolve) -> dict:
        try:
            resolved: ResolvedChannel = await asyncio.to_thread(channel_resolver, settings, payload.url)
        except ChannelResolveError as exc:
            logging.getLogger("yt_notifi").info("CHANNEL_RESOLVE_FAILED error_type=%s", type(exc).__name__)
            return JSONResponse(
                {"ok": False, "error": "CHANNEL_RESOLVE_FAILED", "message": str(exc)},
                status_code=400,
            )
        if any(channel.channel_id == resolved.channel_id for channel in channel_store.list()):
            return JSONResponse(
                {"ok": False, "error": "CHANNEL_ALREADY_EXISTS", "message": "This YouTube channel is already being monitored."},
                status_code=409,
            )
        return {
            "ok": True,
            "channel_id": resolved.channel_id,
            "canonical_url": resolved.canonical_url,
            "title": resolved.title,
        }

    @app.post("/api/channels", status_code=201)
    def add_channel(payload: ChannelCreate) -> dict:
        values = [value for value in (payload.channel_id, payload.url) if value]
        if len(values) != 1:
            raise ChannelStoreError("INVALID_REQUEST", "Hãy nhập một Channel ID hoặc URL.")
        channel = channel_store.add(values[0], payload.name, payload.enabled, payload.owner_id)
        if channel.enabled:
            state.reset_poll_baseline(channel.channel_id)
        return channel_payload(channel)

    @app.patch("/api/channels/{channel_id}")
    def update_channel(channel_id: str, payload: ChannelUpdate) -> dict:
        if payload.enabled is None and payload.cut_enabled is None and payload.name is None:
            raise ChannelStoreError("INVALID_REQUEST", "Không có thay đổi.")
        channel, changed_to_enabled = channel_store.update(
            channel_id, payload.enabled, cut_enabled=payload.cut_enabled, name=payload.name,
        )
        if changed_to_enabled:
            state.reset_poll_baseline(channel.channel_id)
        return channel_payload(channel)

    @app.delete("/api/channels/{channel_id}")
    def delete_channel(channel_id: str) -> dict[str, str]:
        channel_store.remove(channel_id)
        return {"status": "removed"}

    return app


configure_logging()
app = create_app()
logging.getLogger("yt_notifi").info("SERVICE_STARTED")
