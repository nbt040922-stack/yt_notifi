from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI

from .config import Settings, enabled_channels
from .detector import resume_notifications
from .poller import ChannelPoller
from .state import StateStore
from .telegram import TelegramNotifier


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


def create_app(settings: Settings | None = None, state: StateStore | None = None, notifier: TelegramNotifier | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    state = state or StateStore(settings.state_db)
    notifier = notifier or TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    channels = enabled_channels(settings.channels_file)
    names = {channel.channel_id: channel.name for channel in channels}
    poller = ChannelPoller(settings, state, notifier, channels)

    async def notification_retry_loop() -> None:
        while True:
            try:
                await asyncio.to_thread(resume_notifications, state, notifier, names)
            except Exception as exc:
                logging.getLogger("yt_notifi").error("TELEGRAM_RETRY error_type=%s", type(exc).__name__)
            await asyncio.sleep(60)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if settings.enable_background_tasks and not poller.executable:
            raise RuntimeError("yt-dlp is required for YT_NOTIFI polling")
        stop = asyncio.Event()
        tasks = (
            [asyncio.create_task(notification_retry_loop()), asyncio.create_task(poller.run(stop))]
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

    @app.get("/health")
    def health() -> dict[str, str | int]:
        return {"status": "ok", "service": "YT_NOTIFI", "enabled_channels": len(channels)}

    return app


configure_logging()
app = create_app()
logging.getLogger("yt_notifi").info("SERVICE_STARTED")
