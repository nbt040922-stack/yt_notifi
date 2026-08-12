# YT_NOTIFI Phase 2 Report

## Phase 1 preservation

All Phase 1 behavior remains covered: FastAPI endpoints, safe Atom parsing, 1 MB payload cap, enabled-channel allowlist, unknown/disabled channel suppression, SQLite video dedupe, Telegram secrecy, GET topic verification, and health reporting. Existing tests remain green.

## Cloudflare approach and scripts

Phase 2 supports the zero-cost Quick Tunnel command through `scripts/start_tunnel.ps1`. The script requires local health first and discovers `cloudflared.exe` from `CLOUDFLARED_PATH`, `tools/cloudflared.exe`, or `PATH`; it never downloads a binary. Cloudflared displays the generated public HTTPS hostname for explicit copying into `.env`.

Optional Named Tunnel commands are documented in `README.md` for users with a Cloudflare-managed domain. A domain or paid plan is not required for Quick Tunnel testing.

`scripts/test_public_callback.ps1` tests local and public health. `scripts/status.ps1` displays health, enabled channel count, subscription states/expirations, Telegram configuration state, and last activity without secrets.

## Public callback validation

Subscription requires a valid HTTPS origin with no path, query, fragment, credentials, trailing slash, localhost, or loopback address. The webhook path is appended exactly once. Both manual and automatic subscription paths require the public `/health` endpoint to identify `YT_NOTIFI` before contacting the hub.

## Subscription state machine and lease storage

SQLite now separates `REQUESTED` hub submissions from `ACTIVE` callback verification. Subscription records contain channel ID, topic, callback URL, mode, status, requested/verified timestamps, lease, UTC expiry, last renewal attempt, last error, retry count, and next retry time.

The GET challenge changes an enabled topic to `ACTIVE` and calculates `expires_at` in UTC. Hub HTTP acceptance alone leaves the subscription `REQUESTED`.

## Automatic renewal and callback changes

A FastAPI lifespan task checks subscriptions every 60 seconds and stops cleanly on shutdown. Active subscriptions renew only within the final 25% of their lease. Missing, expired, failed, timed-out, or callback-changed subscriptions request again. Healthy active subscriptions survive restart without unnecessary requests.

Renewal failure preserves a still-valid active lease. Retry backoff is 1, 5, 15, then capped at 30 minutes. A changed Quick Tunnel callback invalidates the old callback state and requests a fresh subscription.

## Telegram retry and latency

Videos have one persistent notification lifecycle. Transient network, HTTP 429, and HTTP 5xx failures retry up to three total attempts at 0, 5, and 20 seconds. Missing configuration and HTTP 401/403 stop without looping. Successful notifications never resend; partially attempted notifications can resume after a crash without exceeding three attempts.

SQLite stores notification attempts, last sanitized error, detected UTC time, and valid nonnegative detection latency. Telegram includes latency only when meaningful.

## Automated validation

- Command: `python -m pytest -q`
- Result: **41 passed**
- Network access: fully mocked
- Covered: Phase 1 regression, REQUESTED/ACTIVE distinction, GET activation, lease expiry, renewal threshold, unnecessary-renewal suppression, expiry/callback resubscription, active-state preservation, capped backoff, transient/permanent Telegram behavior, successful dedupe, crash-persistent notification state, Phase 1 schema migration, public URL validation, secret-safe status, disabled channels, unknown POST channels, and duplicates.
- Non-failing warning: Starlette reports its current `TestClient`/`httpx` compatibility deprecation.

## Manual validation actually performed

- Local uvicorn start on `127.0.0.1:8787`: **PASS**
- Local `/health`: **PASS** (`status=ok`, `service=YT_NOTIFI`)
- `scripts/status.ps1` local reporting: **PASS**
- Public health: **MANUAL ACTION REQUIRED**
- Quick Tunnel: **MANUAL ACTION REQUIRED** (`cloudflared.exe` unavailable)
- Telegram live test: **MANUAL ACTION REQUIRED** (`.env` and credentials unavailable)
- Real channel subscription/callback verification: **MANUAL ACTION REQUIRED** (no configured channels/public callback)
- Real YouTube upload POST: **MANUAL ACTION REQUIRED**; not faked

## Known limitations

- Quick Tunnel hostnames are temporary and must be copied into `.env` explicitly.
- Named Tunnel setup requires the user's Cloudflare account/domain and remains optional.
- Renewal runs inside the watcher process; it does not run while the watcher is stopped.
- Telegram retry is intentionally limited to three total attempts per video.
- A real upload event depends on a monitored channel publishing after activation.

## Exact next manual commands

```powershell
Copy-Item .env.example .env
# Fill TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, and config/channels.json
.\scripts\run.ps1
```

In a second PowerShell window after installing `cloudflared`:

```powershell
.\scripts\start_tunnel.ps1
# Copy its https://*.trycloudflare.com origin into PUBLIC_CALLBACK_URL in .env
# Restart scripts/run.ps1 so the watcher loads the changed .env
.\scripts\test_public_callback.ps1
.\scripts\test_telegram.ps1
.\scripts\subscribe.ps1
.\scripts\status.ps1
```

PHASE 2 IMPLEMENTATION COMPLETE — LIVE VALIDATION REQUIRED
