from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from .config import Channel, Settings, enabled_channels, public_callback
from .state import StateStore, parse_utc

HUB_URL = "https://pubsubhubbub.appspot.com/subscribe"
logger = logging.getLogger("yt_notifi")


def topic_for(channel_id: str) -> str:
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def subscription_data(channel: Channel, callback: str) -> dict[str, str]:
    return {"hub.mode": "subscribe", "hub.topic": topic_for(channel.channel_id), "hub.callback": callback}


def renewal_due(subscription, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    expires = parse_utc(subscription["expires_at"])
    lease = subscription["lease_seconds"]
    return bool(expires and lease and expires - now <= timedelta(seconds=lease * 0.25))


def request_reason(subscription, callback: str, now: datetime | None = None) -> str | None:
    now = now or datetime.now(timezone.utc)
    if not subscription:
        return "missing"
    if subscription["callback_url"] != callback:
        return "callback_changed"
    next_retry = parse_utc(subscription["next_retry_at"])
    if next_retry and next_retry > now:
        return None
    if subscription["status"] == "REQUESTED":
        requested = parse_utc(subscription["requested_at"])
        return "verification_timeout" if not requested or now - requested >= timedelta(minutes=5) else None
    expires = parse_utc(subscription["expires_at"])
    if expires and expires <= now:
        return "expired"
    if subscription["status"] != "ACTIVE":
        return "retry"
    return "renewal" if renewal_due(subscription, now) else None


def request_subscription(channel: Channel, callback: str, state: StateStore, client, reason: str) -> bool:
    topic = topic_for(channel.channel_id)
    current = state.get_subscription(topic)
    preserve_active = reason == "renewal" and current is not None and current["status"] == "ACTIVE"
    state.mark_subscription_requested(channel.channel_id, topic, callback, preserve_active)
    logger.info("SUBSCRIBE_REQUEST channel_id=%s reason=%s", channel.channel_id, reason)
    if reason == "renewal":
        logger.info("SUBSCRIPTION_RENEWAL channel_id=%s", channel.channel_id)
    try:
        response = client.post(HUB_URL, data=subscription_data(channel, callback))
        response.raise_for_status()
        logger.info("SUBSCRIBE_HUB_ACCEPTED channel_id=%s", channel.channel_id)
        return True
    except Exception as exc:
        state.record_subscription_failure(topic, type(exc).__name__)
        logger.error("SUBSCRIBE_FAILED channel_id=%s error_type=%s", channel.channel_id, type(exc).__name__)
        return False


def ensure_subscriptions(
    settings: Settings,
    state: StateStore,
    channels: list[Channel],
    client,
    *,
    force: bool = False,
    now: datetime | None = None,
) -> list[tuple[Channel, bool, str]]:
    callback = public_callback(settings)
    results = []
    for channel in channels:
        current = state.get_subscription(topic_for(channel.channel_id))
        reason = "manual" if force else request_reason(current, callback, now)
        if reason:
            results.append((channel, request_subscription(channel, callback, state, client, reason), reason))
    return results


def maintain_subscriptions(settings: Settings, state: StateStore, channels: list[Channel]) -> None:
    with httpx.Client(timeout=20) as client:
        if public_health_ok(settings, client):
            ensure_subscriptions(settings, state, channels, client)
        else:
            logger.error("SUBSCRIPTION_RENEWAL public_health=FAIL")


def public_health_ok(settings: Settings, client) -> bool:
    try:
        callback = public_callback(settings)
        origin = callback.removesuffix(settings.webhook_path)
        response = client.get(origin + "/health")
        response.raise_for_status()
        payload = response.json()
        return payload.get("status") == "ok" and payload.get("service") == "YT_NOTIFI"
    except Exception:
        return False


def subscribe_all(settings: Settings) -> bool:
    try:
        callback = public_callback(settings)
    except ValueError as exc:
        print(f"PUBLIC_CALLBACK_URL invalid: {exc}")
        return False
    state = StateStore(settings.state_db)
    channels = enabled_channels(settings.channels_file)
    with httpx.Client(timeout=20) as client:
        if not public_health_ok(settings, client):
            print("PUBLIC HEALTH   FAIL — subscription cancelled")
            return False
        results = ensure_subscriptions(settings, state, channels, client, force=True)
    success = True
    for channel, accepted, _ in results:
        print(f"[SUBSCRIBE] {channel.name}")
        print(f"Hub request:      {'ACCEPTED' if accepted else 'FAILED'}")
        print("Verification:     waiting for callback" if accepted else "Verification:     not requested")
        success &= accepted
    if not channels:
        print("No enabled channels")
    logger.info("TUNNEL_PUBLIC_URL host=%s", callback.split("/youtube/websub")[0])
    return success


if __name__ == "__main__":
    raise SystemExit(0 if subscribe_all(Settings.from_env()) else 1)
