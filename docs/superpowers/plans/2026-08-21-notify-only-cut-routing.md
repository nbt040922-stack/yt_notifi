# Notify-only Cut Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove member-based channel routing and defer Telegram notifications only for channels with `cut_enabled=true`.

**Architecture:** Keep channel polling and SQLite dedupe. A cut-off channel notifies immediately; a cut-on channel records the event as notification-pending, runs the existing download/processing pipeline, then releases the notification after processing succeeds. Member columns remain nullable for old database compatibility but are no longer used for validation or routing.

**Tech Stack:** Python, FastAPI, SQLite, pytest, existing YT_NOTIFI workers.

**Spec:** Approved user design in chat on 2026-08-21.

## Global Constraints

- Do not change YouTube polling, Telegram message format, SQLite dedupe, or bridge contracts.
- Silence Cutter remains optional and is used only for channels with `cut_enabled=true`.
- Existing old channel configuration is cleared; the new seed configuration is an empty list.

### Task 1: Notification readiness state

**Files:** `app/state.py`, `app/detector.py`, `tests/test_notify_routing.py`

- Add a nullable-safe `notification_ready` column defaulting to `1`.
- Record cut-on events with readiness `0`; filter retries to readiness `1`.
- Add `release_notification(video_id)` to make a completed cut job eligible.
- Test immediate and deferred behavior.

### Task 2: Remove member routing from workers and store

**Files:** `app/config.py`, `app/channel_store.py`, `app/poller.py`, `app/jobs.py`, `app/process_worker.py`, `app/main.py`

- Stop loading/validating team members for runtime channel management.
- Keep old owner columns only as compatibility fields.
- Build processing output under the configured NAS root and sanitized channel name.
- On successful processing, release and deliver the deferred notification.

### Task 3: Dashboard and API cleanup

**Files:** `app/dashboard.html`, `app/main.py`, `tests/test_notify_routing.py`

- Remove member tabs, owner selectors, and member bulk payloads.
- Keep one channel list and the existing cut toggle.
- Add channel creation without member selection.

### Task 4: Clear old channel data and verify

**Files:** `config/channels.json`, user LocalAppData channel file if present.

- Replace repository seed with `[]`.
- Clear the installed user channel list so the user can add channels again.
- Run focused tests, `git diff --check`, and smoke-test the API.
