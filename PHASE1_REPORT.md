# YT_NOTIFI Phase 1 Report

## Architecture implemented

```text
YouTube WebSub → FastAPI webhook → safe Atom parser → SQLite dedupe
                                                    → Telegram Bot API
```

The POST webhook parses and validates the request, returns HTTP 202, and runs state/Telegram work as a FastAPI background task. There is no polling, downloader, Silence Cutter, frontend, scheduler, or paid dependency.

## Files created

- `app/`: configuration, app factory/endpoints, Atom parsing, SQLite state, Telegram client, and WebSub subscription helper
- `config/channels.json`: channel allowlist and enable flags
- `scripts/`: run, subscribe, Telegram test, and local event simulation commands
- `tests/fixtures/youtube_event.xml`: realistic local Atom event
- `tests/`: isolated endpoint, parser, state, Telegram, configuration, and subscription tests
- `.env.example`, `.gitignore`, `requirements.txt`, and `README.md`
- `state/` and `logs/`: runtime data directories

## State storage

SQLite was selected for atomic first-insert deduplication and persistence across restarts. The `videos` table stores video/channel/title/published timestamps, first and last seen timestamps, and notification status. The `subscriptions` table stores verified topic, mode, verification time, and lease seconds.

## WebSub verification

`GET /youtube/websub` requires a nonempty challenge, `subscribe` or `unsubscribe` mode, and a topic belonging to an enabled configured channel. It returns the challenge as raw text and records verification/lease information. Invalid verification requests return HTTP 400.

## Atom parsing

The standard-library XML parser handles Atom and YouTube namespaces. The input is capped at 1 MB; DTD/entity declarations are rejected; malformed XML and invalid video/channel IDs return HTTP 400 without crashing the service. Event URLs are restricted to YouTube forms, with a canonical watch URL fallback.

## Telegram behavior

The client makes a direct HTTPS `sendMessage` request. New-video messages include channel, title, publication time, local detection time, and watch URL. Missing configuration, HTTP failures, and Telegram API failures are logged as `TELEGRAM_FAILED`, saved with `notification_sent = 0`, and never propagate into the webhook response.

## Deduplication behavior

SQLite `INSERT OR IGNORE` makes the first unseen video ID the only `NEW_VIDEO`. Repeated metadata notifications update `last_seen_at`, log `DUPLICATE_VIDEO`, and do not call Telegram. Reopening the database or restarting the service preserves this behavior.

## Validation results

- Automated tests: **20 passed**
- Command: `python -m pytest -q`
- Covered: health and secret safety, WebSub challenge validation, valid/unsafe/malformed Atom input, first event, duplicates, persistence, mocked Telegram success/failure, channel loading/filtering, topic generation, and subscription request construction
- Manual local smoke test: **passed**
  - Service started on `127.0.0.1:8787`
  - `/health` returned `ok`
  - Fixture POST twice returned `accepted`
  - Service restarted with the same temporary SQLite database
  - Fixture POST after restart returned `accepted`
  - Database still contained exactly one video record
  - Real Telegram delivery was not attempted because no user credentials were available; automated mocked success and failure paths passed

## Known limitations

- Subscription renewal is prepared through persisted lease data but is manual in Phase 1.
- Failed Telegram notifications are recorded but not automatically retried.
- Cloudflare Tunnel must be installed and configured manually.
- YouTube WebSub authenticity is based on topic verification and strict payload validation; signed-content verification is not provided by the YouTube hub flow used here.

PHASE 1 COMPLETE
