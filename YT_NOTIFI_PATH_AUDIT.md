# YT_NOTIFI Path Audit

## Authoritative repository

`D:\yt_notifi` is the only authoritative YT_NOTIFI working repository.

## F: duplicate

A temporary clone created during the prior task was found at:

`F:\CA_NHAN\yt_notifi`

Before recovery, it was clean at commit `611b75a`, while the authoritative D:
repository was at `2638a46`. The unique commit was:

`611b75a feat(routing): map YouTube channels to MinHa profiles`

Its exact changed files were:

- `.env.example`
- `CHANNEL_MINHA_MAPPING_REPORT.md`
- `README.md`
- `app/channel_store.py`
- `app/config.py`
- `app/dashboard.html`
- `app/detector.py`
- `app/jobs.py`
- `app/main.py`
- `app/minha.py`
- `app/poller.py`
- `app/state.py`
- `tests/test_minha_mapping.py`

The F: clone also contained only ignored task-generated artifacts:
`.pytest_cache/`, Python `__pycache__/` folders, `logs/yt_notifi.log`, and
`state/yt_notifi.db`. The temporary database contained zero videos, jobs, poll
states, and notify channels. None was copied into D:.

## Recovery and comparison

`D:\yt_notifi` was fast-forwarded from its configured user-owned `origin/main`
to `611b75a`. The pre-existing local modification to `config/channels.json` and
untracked `state/processing-control.json` were preserved. After recovery, the D:
and F: repository HEADs and tracked trees were identical.

No NAS folders, `F:\ContentOpsFallback`, video output, or production runtime data
was read from or modified as part of recovery.

After recovery and equality verification, the redundant task-created clone was
moved to the Windows Recycle Bin. It is no longer present at
`F:\CA_NHAN\yt_notifi` and remains recoverable from the Recycle Bin if needed.

## Final D: Git state

- Branch: `main`
- HEAD: `611b75a feat(routing): map YouTube channels to MinHa profiles`
- Remote: `https://github.com/nbt040922-stack/yt_notifi.git`
- Sync: `main` matches `origin/main`
- Preserved pre-existing local state: modified `config/channels.json` and
  untracked `state/processing-control.json`
- Audit artifact: untracked `YT_NOTIFI_PATH_AUDIT.md`

All future YT_NOTIFI work must run only from `D:\yt_notifi`.
