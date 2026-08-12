from __future__ import annotations

import logging
from datetime import datetime

import httpx

from .models import VideoEvent

logger = logging.getLogger("yt_notifi")


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id

    def send_message(self, text: str) -> bool:
        if not self.token or not self.chat_id:
            logger.error("TELEGRAM_FAILED missing Telegram configuration")
            return False
        try:
            response = httpx.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text, "disable_web_page_preview": False},
                timeout=15,
            )
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                raise RuntimeError(payload.get("description", "Telegram returned ok=false"))
            logger.info("TELEGRAM_SENT")
            return True
        except Exception as exc:
            # HTTP error strings can contain the token-bearing request URL.
            logger.error("TELEGRAM_FAILED error_type=%s", type(exc).__name__)
            return False

    def send_video(self, event: VideoEvent, channel_name: str) -> bool:
        detected = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        published = event.published.replace("T", " ").replace("Z", " UTC")
        return self.send_message(
            "🔴 NEW YOUTUBE VIDEO\n\n"
            f"Channel: {channel_name}\n"
            f"Title: {event.title}\n"
            f"Published: {published}\n"
            f"Detected: {detected}\n\n"
            f"{event.url}"
        )


if __name__ == "__main__":
    from .config import Settings

    settings = Settings.from_env()
    raise SystemExit(0 if TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id).send_message("YT_NOTIFI Telegram test OK") else 1)
