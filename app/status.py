from __future__ import annotations

import argparse

import httpx

from .config import Settings, enabled_channels, find_ytdlp
from .state import StateStore


def health_ok(url: str) -> bool:
    try:
        response = httpx.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get("status") == "ok" and data.get("service") == "YT_NOTIFI"
    except Exception:
        return False


def status_snapshot(settings: Settings, state: StateStore, local_ok: bool) -> dict:
    ytdlp = find_ytdlp(settings)
    return {
        "service": "YT_NOTIFI",
        "local_service": local_ok,
        "enabled_channels": len(enabled_channels(settings.channels_file)),
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
    print(f"LOCAL HEALTH          {'PASS' if local else 'FAIL'}")
    if args.health_only:
        return 0 if local else 1
    snapshot = status_snapshot(settings, StateStore(settings.state_db), local)
    print(f"Enabled channels      {snapshot['enabled_channels']}")
    print(f"Polling               {snapshot['polling']}")
    print(f"Poll interval         {snapshot['poll_interval_seconds']}s")
    print(f"yt-dlp                 {'AVAILABLE' if snapshot['ytdlp_available'] else 'MISSING'}")
    print(f"Telegram              {'configured' if snapshot['telegram_configured'] else 'not configured'}")
    print("\nPolling channels:")
    for item in snapshot["poll_channels"]:
        print(f"\n{item['channel_id']}")
        print(f"  last poll:          {item['last_poll_at'] or '-'}")
        print(f"  last success:       {item['last_success_at'] or '-'}")
        print(f"  latest seen video:  {item['latest_seen_video_id'] or '-'}")
        print(f"  failures:           {item['consecutive_failures']}")
    print(f"\nLast new video:       {snapshot['last_new_video'] or '-'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
