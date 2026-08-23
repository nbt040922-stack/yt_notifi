from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from .models import VideoEvent

logger = logging.getLogger("yt_notifi")


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.last_error: str | None = None
        self.last_transient = False

    def send_message(self, text: str) -> bool:
        if not self.token or not self.chat_id:
            self.last_error = "missing configuration"
            self.last_transient = False
            logger.error("TELEGRAM_FAILED missing Telegram configuration")
            return False
        try:
            response = httpx.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text, "disable_web_page_preview": False},
                timeout=15,
            )
            status = response.status_code if isinstance(response.status_code, int) else 200
            if status >= 400:
                self.last_error = f"HTTP {status}"
                self.last_transient = status == 429 or status >= 500
                logger.error("TELEGRAM_FAILED status=%s", status)
                return False
            payload = response.json()
            if not payload.get("ok"):
                code = int(payload.get("error_code", response.status_code))
                self.last_error = f"HTTP {code}"
                self.last_transient = code == 429 or code >= 500
                logger.error("TELEGRAM_FAILED status=%s", code)
                return False
            response.raise_for_status()
            self.last_error = None
            self.last_transient = False
            logger.info("TELEGRAM_SENT")
            return True
        except Exception as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            self.last_error = f"HTTP {status}" if status else type(exc).__name__
            self.last_transient = status == 429 or bool(status and status >= 500) or isinstance(exc, httpx.RequestError)
            logger.error("TELEGRAM_FAILED error_type=%s", type(exc).__name__)
            return False

    def send_video(
        self,
        event: VideoEvent,
        channel_name: str,
        detected_at: str | None = None,
        latency_seconds: float | None = None,
    ) -> bool:
        detected_dt = datetime.fromisoformat(detected_at) if detected_at else datetime.now(timezone.utc)
        detected = detected_dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        published = event.published.replace("T", " ").replace("Z", " UTC")
        latency = f"Latency: {round(latency_seconds)}s\n" if latency_seconds is not None and latency_seconds >= 0 else ""
        published_line = f"Published: {published}\n" if published else ""
        return self.send_message(
            "🔴 NEW YOUTUBE VIDEO\n\n"
            f"Channel: {channel_name}\nTitle: {event.title}\n\n"
            f"Detected via: {event.source.upper()}\n{published_line}Detected: {detected}\n{latency}\n"
            f"{event.url}"
        )

    def send_processing_complete(
        self,
        title: str,
        output_paths: list[str],
        channel_name: str | None = None,
        channel_id: str | None = None,
        source_url: str | None = None,
    ) -> bool:
        locations = "\n".join(f"- {path}" for path in output_paths) or "- Chưa xác định được đường dẫn đầu ra"
        channel_details = f"Kênh: {channel_name or 'Chưa xác định'}\n"
        if channel_id:
            channel_details += f"Channel ID: {channel_id}\n"
        if source_url:
            channel_details += f"Video nguồn: {source_url}\n"
        return self.send_message(
            "✅ EDIT XONG\n\n"
            f"{channel_details}"
            f"Video: {title}\n\n"
            "File đã lưu tại:\n"
            f"{locations}"
        )


if __name__ == "__main__":
    from .config import Settings

    settings = Settings.from_env()
    ok = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id).send_message("YT_NOTIFI Telegram test OK")
    raise SystemExit(0 if ok else 1)
