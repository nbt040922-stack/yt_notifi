# Channel → MinHa Mapping Report

## Result

Implemented in YT_NOTIFI. MinHa remains the source of truth for profile name,
current/expected TikTok UID, and `tiktok_account_match`. YT_NOTIFI persists only
nullable `minha_profile_id` on each YouTube channel and snapshots that ID into new
processing jobs.

## Data and API

- Configuration: `MINHA_BASE_URL` defaults to `http://127.0.0.1:8080`; optional
  `MINHA_AUTH_TOKEN` is sent only as a bearer header.
- Live profile list: `GET /api/minha/profiles`.
- Assignment: `PATCH /api/channels/{channel_id}/minha-profile`, including `null`
  to unassign.
- Live guard resolution: `GET /api/channels/{channel_id}/publish-target`.
- Resolver states: `OK`, `CHANNEL_NOT_FOUND`, `MINHA_PROFILE_UNASSIGNED`,
  `MINHA_UNAVAILABLE`, `MINHA_PROFILE_NOT_FOUND`, `UID_UNLOCKED`,
  `ACCOUNT_MISMATCH`, `NOT_LOGGED_IN`, `UID_NOT_DETECTED`, and `PROBE_ERROR`.
- One MinHa profile cannot be assigned to more than one YouTube channel.
- Failed MinHa reads preserve the stored mapping. No UID is duplicated into
  channel configuration or processing jobs.

## Dashboard

Each channel card has a compact TikTok Profile selector populated from live MinHa
data. Options show profile name, username, and account state while storing the
stable profile ID. If MinHa is offline, the dashboard continues loading, keeps the
existing mapping visible, and disables changes until MinHa returns.

## Verification

- Final full suite: `224 passed, 1 skipped`; the skip is the opt-in real acceptance
  case, which was also run separately and passed.
- Syntax compilation and `git diff --check`: passed.
- Controlled real acceptance: passed against the current MinHa source on an
  isolated temporary local port and production data read through its API.
- Existing YouTube channel: `UCNiurMpWExWgio2lqldycbA`.
- Existing MinHa profile: `TN001UK` (`0923d692-e561-4468-b508-e73fa5e93659`).
- MinHa returned equal current/expected TikTok UID and `tiktok_account_match=MATCH`.
- Assignment survived a YT_NOTIFI app restart; resolver returned `OK` before and
  after restart; processing job count remained zero.
- The acceptance MinHa process was stopped immediately afterward.

No production channel mapping, browser profile, TikTok identity, browser process,
publisher, caption, schedule, upload, or post was changed or triggered.
