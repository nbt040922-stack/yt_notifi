# YT_NOTIFI — Phase 1

A small local service that receives YouTube WebSub upload events, stores each video ID in SQLite, and sends one Telegram notification per video. It uses no YouTube API key and no paid service.

Phase 1 does **not** download videos or run Silence Cutter.

## Requirements

- Windows 11
- Python 3.11+
- A Telegram bot token and destination chat ID
- An HTTPS public callback URL when connecting real YouTube subscriptions (for example, a manually configured Cloudflare Tunnel)

## Setup

Run these commands from the project directory in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and set:

```env
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
PUBLIC_CALLBACK_URL=https://your-public-tunnel-host.example
WEBHOOK_PATH=/youtube/websub
HOST=127.0.0.1
PORT=8787
```

Do not add the webhook path to `PUBLIC_CALLBACK_URL`; the service appends `WEBHOOK_PATH`. `.env` is ignored by Git.

Add channels to `config/channels.json`:

```json
[
  {
    "channel_id": "UC_x5XG1OV2P6uZZ5FSM9Ttw",
    "name": "Example Channel",
    "enabled": true
  }
]
```

Only channel IDs are required. No YouTube Data API key is used.

## Test Telegram

```powershell
.\scripts\test_telegram.ps1
```

The configured chat should receive `YT_NOTIFI Telegram test OK`.

## Run locally

```powershell
.\scripts\run.ps1
```

Check the service at `http://127.0.0.1:8787/health`.

In a second PowerShell window, simulate a YouTube event:

```powershell
.\scripts\simulate_event.ps1
.\scripts\simulate_event.ps1
```

The first request logs `NEW_VIDEO` and sends Telegram. The second logs `DUPLICATE_VIDEO` and does not send again. Delete `state/yt_notifi.db` only when you intentionally want to reset deduplication history.

## Subscribe enabled channels

First expose port 8787 through an HTTPS tunnel and put its public origin in `PUBLIC_CALLBACK_URL`. Cloudflare Tunnel setup is deliberately manual and outside Phase 1. Then run:

```powershell
.\scripts\subscribe.ps1
```

The script sends one subscription request for each enabled channel and displays each success or failure. YouTube verifies the callback with `GET /youtube/websub`; successful verification and lease duration are stored in SQLite for future renewal support.

## Tests

Tests never contact Telegram or YouTube:

```powershell
python -m pytest -q
```

Runtime logs are written to the console and `logs/yt_notifi.log`. Tokens and chat credentials are never logged or returned by status endpoints.

## Important behavior

- Atom XML is treated as untrusted input; DTD/entity declarations, malformed XML, oversized payloads, and invalid IDs are rejected.
- Only configured topics can pass WebSub GET verification.
- SQLite atomically decides whether a video ID is new, so duplicate deliveries and restarts cannot resend a new-video alert.
- Telegram errors are logged and stored as `notification_sent = 0`; they never crash the webhook receiver.
- Phase 1 has no retry scheduler or subscription-renewal scheduler. Re-run the subscription script manually when needed.
