# MinHa Mapping Persistence Fix

## Root cause

The mapping was not written and then lost. Production reproduction showed that
`PATCH /api/channels/UCNiurMpWExWgio2lqldycbA/minha-profile` returned
`409 UID_UNLOCKED` before `channel_store.set_minha_profile()` ran.

The running MinHa API exposes TN001UK using its older profile response schema.
The profile exists, but the response omits `expected_tiktok_uid` and the other
TikTok identity fields. YT_NOTIFI used `profile.get("expected_tiktok_uid")`, which
incorrectly treated an omitted field as an explicitly persisted null/unlocked UID.
The dashboard then handled the failed PATCH by refreshing from the unchanged
channel configuration, so the selector correctly returned to UNASSIGNED and
appeared to have forgotten a successful selection.

Observed before the fix:

- PATCH: `409 UID_UNLOCKED`
- `config/channels.json`: unchanged; `minha_profile_id` absent
- `GET /api/channels`: `minha_profile_id=null`
- dashboard refresh: selected UNASSIGNED

## Fix

The assignment guard now rejects UID_UNLOCKED only when the MinHa API explicitly
provides `expected_tiktok_uid` with a null/empty value. A legacy response that
omits the field can be mapped by stable profile ID.

This does not weaken publish safety. The live resolver still reports
`PROBE_ERROR` when MinHa does not provide a verified account state, and no
publisher was added or invoked.

## Data-flow audit

- Dashboard sends the selected stable profile ID to the dedicated PATCH route.
- The PATCH route validates that the MinHa profile exists.
- `ChannelStore.set_minha_profile()` reconstructs the channel with the profile ID.
- `_save_unlocked()` writes `minha_profile_id` atomically to `channels.json`.
- `load_channels()` reloads the persisted field.
- `channel_payload()` includes `minha_profile_id` in every `/api/channels` row.
- `refresh()` assigns the persisted value back to the selector.
- Poller/config reload reads the same persisted channel record and does not write
  channel configuration.

All Channel reconstruction paths were checked. Rename, enabled toggle, cut toggle,
and owner updates preserve `channel.minha_profile_id`; new/bulk/migrated channels
start unassigned; only the dedicated unassign PATCH clears it.

## UI behavior

No dashboard redesign or optional lock UX was added. After a successful assignment,
refresh continues to select the persisted profile ID. After a failed PATCH, the
existing error notice is shown and refresh restores the previous persisted value.

Production currently displays `TN001UK · UNKNOWN` because the running MinHa API
does not expose TikTok identity fields. This is intentionally not fabricated as
MATCH. The mapping itself remains selected and persisted.

## Regression verification

- legacy MinHa profile response without identity fields can be mapped
- explicit unlocked UID remains rejected
- assignment persists in storage and app restart
- `/api/channels` returns `minha_profile_id`
- dashboard binds the selector to the persisted profile ID
- rename preserves the mapping
- cut OFF/ON preserves the mapping
- enabled OFF/ON preserves the mapping
- failed PATCH preserves the previous mapping
- explicit unassign clears the mapping
- job snapshot keeps only the stable MinHa profile ID
- full suite: `226 passed, 1 skipped`

## Real acceptance

Controlled production acceptance used:

- YouTube channel: `TN003UK - Nhật` (`UCNiurMpWExWgio2lqldycbA`)
- MinHa profile: `TN001UK` (`0923d692-e561-4468-b508-e73fa5e93659`)

Verified:

1. PATCH returned 200 with the selected profile ID.
2. The actual production `config/channels.json` record persisted the same ID.
3. `/api/channels` returned the same ID after refresh cycles.
4. The dashboard selected TN001UK after initial load and manual page refresh.
5. Cut was toggled ON → OFF → ON; the mapping remained unchanged and the original
   Cut state was restored.
6. The production stack was restarted; file, API, and manually refreshed dashboard
   still selected TN001UK.
7. No browser profile, TikTok probe, job, upload, post, or publisher was triggered.
