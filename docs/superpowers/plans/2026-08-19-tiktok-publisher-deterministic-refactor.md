# TikTok Publisher Deterministic Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the TikTok publisher deterministic from valid processed output through verified post, with safe restart/idempotency and real DONE auto-trigger.

**Architecture:** Keep the existing `PublishStore`, `TikTokUploadAutomation`, and `TikTokPublisher` boundaries, but make persisted state transitions explicit and fail-closed. Browser ownership remains inside one publisher attempt; target acquisition reuses a matching TikTok Studio page and never calls `page.goto()` on a reused CDP target. Auto-trigger remains at the processing DONE boundary and uses the existing unique idempotency key.

**Tech Stack:** Python, FastAPI, SQLite, Playwright CDP, MinHa HTTP API, pytest.

**Spec:** User attachment `db83988b-25fd-4b8b-8f57-a176d85920d9/pasted-text.txt`.

## Global Constraints

- Do not modify YTDOWNLOAD, Silence Cutter, MinHa architecture, polling, NAS routing, or unrelated dashboard behavior.
- Never weaken UID MATCH, provenance, idempotency, or receipt requirements.
- Never retry a Post click or automatically retry `POST_RESULT_UNCERTAIN`.
- No commit or push in this task.
- A real acceptance PASS requires strong evidence of the actual TikTok post; otherwise report BLOCKED.

### Task 1: State and persistence hardening

**Files:** `app/tiktok_publisher.py`, `tests/test_tiktok_publisher.py`

- Add persisted diagnostics for attach count, post click count, attempted time, verification method, post id/url, and receipt id with additive SQLite migration.
- Define explicit pre-click and post-click terminal semantics; restart converts only post-boundary states to `POST_RESULT_UNCERTAIN` and pre-click active states to `FAILED_PRE_POST`.
- Add tests for zero-click failures, one-click uncertainty, and restart reconciliation.

### Task 2: Deterministic target/upload/privacy lifecycle

**Files:** `app/tiktok_publisher.py`, `tests/test_tiktok_publisher.py`

- Keep target acquisition centralized, emit `TARGET_REUSED`/`NO_GOTO_USED`, and hard-block any live-flow `page.goto()`.
- Normalize clean upload pages immediately; discard stale pages only when stale evidence exists; attach through CDP for remote large files exactly once.
- Wait for the real bottom Post button to become visible and enabled, then set and independently verify ONLY_YOU before READY_TO_POST.
- Preserve browser/profile/CDP at READY_TO_POST and clean up only on terminal states.

### Task 3: Real post verification and receipts

**Files:** `app/tiktok_publisher.py`, `tests/test_tiktok_publisher.py`

- Persist `POSTING` before the single click and `VERIFYING_POST` after it.
- Require strong success evidence (success response, returned post id/url, or definitive success state) before writing receipt/DONE.
- Map post-click ambiguity to `POST_RESULT_UNCERTAIN`; keep receipt and Telegram notification strictly after DONE.

### Task 4: DONE auto-post, cleanup, dashboard

**Files:** `app/process_worker.py`, `app/tiktok_publisher.py`, `app/dashboard.html`, `tests/test_process_worker.py`, `tests/test_dashboard.py`

- Ensure DONE processing calls idempotent per-output publish creation in PART order and skips unmapped/invalid outputs.
- Protect active, READY_TO_POST, POSTING, VERIFYING, and uncertain records from cleanup while preserving receipts/idempotency.
- Expose current step, progress, diagnostics, failure reason, profile/output, and receipt details through existing APIs/UI.

### Task 5: Verification

- Run focused publisher/process/dashboard tests, then the full relevant pytest suite and `git diff --check`.
- Attempt exactly one designated real acceptance only if a legitimate DONE job, mapped profile, logged-in UID MATCH, and valid output are available; otherwise report BLOCKED without enabling unattended auto-post.
