from __future__ import annotations

import logging
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from .config import Settings, enabled_channels
from .state import StateStore
from .telegram import TelegramNotifier
from .webhook import parse_atom, process_event


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
    topics = {f"https://www.youtube.com/feeds/videos.xml?channel_id={channel.channel_id}" for channel in channels}
    app = FastAPI(title="YT_NOTIFI", docs_url=None, redoc_url=None)

    @app.get("/")
    def root() -> dict[str, str]:
        return {"status": "ok", "service": "YT_NOTIFI"}

    @app.get("/health")
    def health() -> dict[str, str | int]:
        return {"status": "ok", "service": "YT_NOTIFI", "enabled_channels": len(channels)}

    @app.get(settings.webhook_path, response_class=PlainTextResponse)
    def verify(
        hub_mode: str = Query(alias="hub.mode"),
        hub_topic: str = Query(alias="hub.topic"),
        hub_challenge: str = Query(alias="hub.challenge"),
        hub_lease_seconds: int | None = Query(default=None, alias="hub.lease_seconds"),
    ) -> str:
        if hub_mode not in {"subscribe", "unsubscribe"} or hub_topic not in topics or not hub_challenge:
            raise HTTPException(400, "Invalid WebSub verification")
        state.record_subscription(hub_topic, hub_mode, hub_lease_seconds)
        logging.getLogger("yt_notifi").info("WEBSUB_VERIFY mode=%s topic=%s", hub_mode, hub_topic)
        return hub_challenge

    @app.post(settings.webhook_path, status_code=202)
    async def receive(request: Request, background_tasks: BackgroundTasks) -> dict[str, str]:
        payload = await request.body()
        if len(payload) > 1_000_000:
            raise HTTPException(413, "Payload too large")
        try:
            events = parse_atom(payload)
        except ValueError as exc:
            logging.getLogger("yt_notifi").warning("WEBSUB_EVENT rejected: %s", exc)
            raise HTTPException(400, str(exc)) from exc
        for event in events:
            background_tasks.add_task(process_event, event, state, notifier, names)
        return {"status": "accepted"}

    return app


configure_logging()
app = create_app()
logging.getLogger("yt_notifi").info("SERVICE_STARTED")
