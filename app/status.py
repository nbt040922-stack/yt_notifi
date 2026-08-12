from __future__ import annotations

import argparse

import httpx

from .config import Settings, enabled_channels, find_ytdlp, public_callback
from .state import StateStore


def health_ok(url: str) -> bool:
    try:
        response = httpx.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get("status") == "ok" and data.get("service") == "YT_NOTIFI"
    except Exception:
        return False


def status_snapshot(settings: Settings, state: StateStore, local_ok: bool, public_ok: bool) -> dict:
    ytdlp = find_ytdlp(settings)
    return {
        "service": "YT_NOTIFI",
        "local_service": local_ok,
        "public_callback": public_ok,
        "enabled_channels": len(enabled_channels(settings.channels_file)),
        "subscriptions": [
            {"channel_id": row["channel_id"], "status": row["status"], "expires_at": row["expires_at"]}
            for row in state.subscriptions()
        ],
        "telegram_configured": bool(settings.telegram_bot_token and settings.telegram_chat_id),
        "polling": "RUNNING" if local_ok and ytdlp else "UNAVAILABLE" if not ytdlp else "STOPPED",
        "poll_interval_seconds": settings.poll_interval_seconds,
        "ytdlp_available": bool(ytdlp),
        "poll_channels": [dict(row) for row in state.poll_states()],
        **state.latest_activity(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--health-only", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env()
    local = health_ok(f"http://127.0.0.1:{settings.port}/health")
    try:
        public = health_ok(public_callback(settings).removesuffix(settings.webhook_path) + "/health")
    except ValueError:
        public = False
    print(f"LOCAL HEALTH    {'PASS' if local else 'FAIL'}")
    print(f"PUBLIC HEALTH   {'PASS' if public else 'FAIL'}")
    if args.health_only:
        return 0 if local and public else 1
    snapshot = status_snapshot(settings, StateStore(settings.state_db), local, public)
    print(f"Enabled channels:     {snapshot['enabled_channels']}")
    print(f"Polling:              {snapshot['polling']}")
    print(f"Poll interval:        {snapshot['poll_interval_seconds']}s")
    print(f"yt-dlp:               {'AVAILABLE' if snapshot['ytdlp_available'] else 'MISSING'}")
    print("\nSubscriptions:")
    for item in snapshot["subscriptions"]:
        print(f"{item['channel_id']}    {item['status']}    expires {item['expires_at'] or '-'}")
    print(f"\nTelegram:             {'configured' if snapshot['telegram_configured'] else 'not configured'}")
    print(f"Last webhook:          {snapshot['last_webhook'] or '-'}")
    print(f"Last new video:        {snapshot['last_new_video'] or '-'}")
    print("\nPolling channels:")
    for item in snapshot["poll_channels"]:
        print(f"{item['channel_id']}")
        print(f"  last poll:           {item['last_poll_at'] or '-'}")
        print(f"  last success:        {item['last_success_at'] or '-'}")
        print(f"  latest seen video:   {item['latest_seen_video_id'] or '-'}")
        print(f"  failures:            {item['consecutive_failures']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
