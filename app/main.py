from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .channel_store import ChannelStore, ChannelStoreError
from .channel_resolver import ChannelResolveError, ResolvedChannel, resolve_channel
from .config import Settings
from .detector import resume_notifications
from .download_worker import DownloadHandoffWorker
from .poller import ChannelPoller
from .process_worker import ProcessHandoffWorker
from .state import StateStore
from .telegram import TelegramNotifier

DASHBOARD = Path(__file__).with_name("dashboard.html")


class ChannelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel_id: str | None = Field(default=None, max_length=500)
    url: str | None = Field(default=None, max_length=500)
    name: str = Field(min_length=1, max_length=200)
    enabled: bool = True


class ChannelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class ChannelResolve(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=500)


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
) -> FastAPI:
    settings = settings or Settings.from_env()
    state = state or StateStore(settings.state_db)
    notifier = notifier or TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    channel_store = channel_store or ChannelStore(settings.channels_file)
    channel_resolver = channel_resolver or resolve_channel
    try:
        channels = channel_store.enabled()
    except ChannelStoreError:
        channels = []
    poller = ChannelPoller(settings, state, notifier, channels, channel_loader=channel_store.enabled)
    download_worker = DownloadHandoffWorker(settings, state)
    process_worker = ProcessHandoffWorker(settings, state)

    async def notification_retry_loop() -> None:
        while True:
            try:
                await asyncio.to_thread(resume_notifications, state, notifier, poller.names)
            except Exception as exc:
                logging.getLogger("yt_notifi").error("TELEGRAM_RETRY error_type=%s", type(exc).__name__)
            await asyncio.sleep(60)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if settings.enable_background_tasks and not poller.executable:
            raise RuntimeError("yt-dlp is required for YT_NOTIFI polling")
        stop = asyncio.Event()
        tasks = (
            [
                asyncio.create_task(notification_retry_loop()),
                asyncio.create_task(poller.run(stop)),
                asyncio.create_task(download_worker.run(stop)),
                asyncio.create_task(process_worker.run(stop)),
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
            "last_poll_at": row["last_poll_at"] if row else None,
            "last_success_at": row["last_success_at"] if row else None,
            "latest_seen_video_id": row["latest_seen_video_id"] if row else None,
            "failures": failures,
            "status": status,
        }

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
        }

    @app.get("/api/channels")
    def api_channels() -> list[dict]:
        return [channel_payload(channel) for channel in channel_store.list()]

    @app.get("/api/jobs")
    def api_jobs() -> list[dict]:
        return [dict(job) for job in state.processing_jobs()]

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
        channel = channel_store.add(values[0], payload.name, payload.enabled)
        if channel.enabled:
            state.reset_poll_baseline(channel.channel_id)
        return channel_payload(channel)

    @app.patch("/api/channels/{channel_id}")
    def update_channel(channel_id: str, payload: ChannelUpdate) -> dict:
        channel, changed_to_enabled = channel_store.update(channel_id, payload.enabled)
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
