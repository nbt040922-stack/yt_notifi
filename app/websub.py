from __future__ import annotations

import logging

import httpx

from .config import Channel, Settings, enabled_channels

HUB_URL = "https://pubsubhubbub.appspot.com/subscribe"
logger = logging.getLogger("yt_notifi")


def topic_for(channel_id: str) -> str:
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def subscription_data(channel: Channel, callback: str) -> dict[str, str]:
    return {"hub.mode": "subscribe", "hub.topic": topic_for(channel.channel_id), "hub.callback": callback}


def subscribe_all(settings: Settings) -> bool:
    if not settings.public_callback_url:
        print("PUBLIC_CALLBACK_URL is required")
        return False
    callback = settings.public_callback_url + settings.webhook_path
    success = True
    with httpx.Client(timeout=20) as client:
        for channel in enabled_channels(settings.channels_file):
            logger.info("SUBSCRIBE_REQUEST channel_id=%s", channel.channel_id)
            try:
                response = client.post(HUB_URL, data=subscription_data(channel, callback))
                response.raise_for_status()
                print(f"[SUBSCRIBE] {channel.name} ... OK ({response.status_code})")
            except Exception as exc:
                success = False
                logger.error("SUBSCRIBE_FAILED channel_id=%s error=%s", channel.channel_id, exc)
                print(f"[SUBSCRIBE] {channel.name} ... FAILED: {exc}")
    return success


if __name__ == "__main__":
    raise SystemExit(0 if subscribe_all(Settings.from_env()) else 1)
