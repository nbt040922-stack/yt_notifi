from __future__ import annotations

import asyncio
import hmac
import ipaddress
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from .callback import ActiveCallback
from .config import Settings, enabled_channels
from .poller import ChannelPoller
from .state import StateStore
from .telegram import TelegramNotifier
from .webhook import parse_atom, process_event, resume_notifications
from .websub import ensure_subscriptions, maintain_subscriptions


class RuntimeCallbackUpdate(BaseModel):
    public_origin: str


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
    topic_channels = {f"https://www.youtube.com/feeds/videos.xml?channel_id={channel.channel_id}": channel.channel_id for channel in channels}
    topics = set(topic_channels)
    poller = ChannelPoller(settings, state, notifier, channels)
    active_callback = ActiveCallback(settings)

    def refresh_subscriptions(callback: str) -> list[tuple]:
        import httpx

        with httpx.Client(timeout=20) as client:
            return ensure_subscriptions(settings, state, channels, client, callback=callback)

    async def maintenance_loop() -> None:
        while True:
            try:
                await asyncio.to_thread(resume_notifications, state, notifier, names)
                try:
                    callback = active_callback.callback_url
                except ValueError:
                    callback = None
                if callback:
                    await asyncio.to_thread(maintain_subscriptions, settings, state, channels, callback)
            except Exception as exc:
                logging.getLogger("yt_notifi").error("SUBSCRIPTION_RENEWAL error_type=%s", type(exc).__name__)
            await asyncio.sleep(60)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        stop = asyncio.Event()
        tasks = (
            [asyncio.create_task(maintenance_loop()), asyncio.create_task(poller.run(stop))]
            if settings.enable_background_tasks else []
        )
        yield
        stop.set()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="YT_NOTIFI", docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.get("/")
    def root() -> dict[str, str]:
        return {"status": "ok", "service": "YT_NOTIFI"}

    @app.get("/health")
    def health() -> dict[str, str | int]:
        return {"status": "ok", "service": "YT_NOTIFI", "enabled_channels": len(channels)}

    @app.post("/internal/runtime-callback")
    async def update_runtime_callback(payload: RuntimeCallbackUpdate, request: Request) -> dict[str, str | int | bool]:
        try:
            caller = ipaddress.ip_address(request.client.host if request.client else "")
        except ValueError:
            caller = None
        token = request.headers.get("x-yt-notifi-runtime-token", "")
        if not caller or not caller.is_loopback or not settings.launcher_runtime_token or not hmac.compare_digest(
            token, settings.launcher_runtime_token
        ):
            raise HTTPException(403, "Forbidden")
        try:
            changed = active_callback.set_runtime(payload.public_origin)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not changed:
            return {"status": "unchanged", "changed": False, "requested": 0}
        results = await asyncio.to_thread(refresh_subscriptions, active_callback.callback_url)
        logging.getLogger("yt_notifi").info("RUNTIME_CALLBACK_UPDATED subscription_requests=%s", len(results))
        return {"status": "updated", "changed": True, "requested": len(results)}

    @app.get(settings.webhook_path, response_class=PlainTextResponse)
    def verify(
        hub_mode: str = Query(alias="hub.mode"),
        hub_topic: str = Query(alias="hub.topic"),
        hub_challenge: str = Query(alias="hub.challenge"),
        hub_lease_seconds: int | None = Query(default=None, alias="hub.lease_seconds"),
    ) -> str:
        if hub_mode not in {"subscribe", "unsubscribe"} or hub_topic not in topics or not hub_challenge:
            raise HTTPException(400, "Invalid WebSub verification")
        callback = active_callback.callback_url
        if not state.get_subscription(hub_topic):
            state.mark_subscription_requested(topic_channels[hub_topic], hub_topic, callback)
        state.activate_subscription(hub_topic, hub_mode, hub_lease_seconds, callback)
        logging.getLogger("yt_notifi").info("WEBSUB_VERIFY mode=%s topic=%s", hub_mode, hub_topic)
        logging.getLogger("yt_notifi").info("SUBSCRIPTION_ACTIVE topic=%s", hub_topic)
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
            if event.channel_id not in names:
                logging.getLogger("yt_notifi").warning("WEBSUB_EVENT_REJECTED_UNKNOWN_CHANNEL")
                continue
            background_tasks.add_task(process_event, event, state, notifier, names)
        return {"status": "accepted"}

    return app


configure_logging()
app = create_app()
logging.getLogger("yt_notifi").info("SERVICE_STARTED")
