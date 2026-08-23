# ContentOps Local Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate automatic and manual entry points while making `:8791` the only durable Silence/Qwen scheduler.

**Architecture:** YT_NOTIFI always downloads through `:8790` and submits finalized local paths to `:8791`; the LAN API keeps `:8780` for manual URLs and follows the same `:8790` → `:8791` path. The scheduler persists one FIFO queue and enforces one active job.

**Tech Stack:** Python, FastAPI, SQLite/state stores, httpx, existing Node YTDOWNLOAD bridge, existing Silence/Qwen bridge.

**Spec:** `docs/superpowers/specs/2026-08-23-contentops-local-architecture-design.md`

## Global Constraints

- Ports remain exactly `8787`, `8780`, `8790`, `8791`, and `8792`.
- Automatic YT_NOTIFI code must not call `:8780`.
- LAN API must not call `:8792`.
- YT_NOTIFI must not call `:8792`.
- Processing concurrency is exactly 1 and queue order is FIFO.
- Use finalized local files only; no LAN upload, base64, or temporary share.
- Preserve authentication on the manual LAN API.
- No unrelated refactor, commit, or push.

### Task 1: Lock automatic local routing

**Files:**
- Modify: `D:/yt_notifi/app/process_worker.py`
- Modify: `D:/yt_notifi/app/download_worker.py`
- Modify: `D:/yt_notifi/app/main.py`
- Test: `D:/yt_notifi/tests/test_local_contentops_routing.py`

- [ ] Write tests proving an automatic job posts only to `http://127.0.0.1:8790` and `http://127.0.0.1:8791`, never a configured `:8780`, and sends a finalized local path.
- [ ] Run the focused tests and observe the expected failure from the existing LAN branch.
- [ ] Remove the automatic `remote_processing` branch and retain the local bridge health/retry behavior.
- [ ] Run the focused tests and verify all pass.

### Task 2: Add manual origin and shared-scheduler handoff

**Files:**
- Modify: `D:/Silence_cutter/lan_job_api.py`
- Modify: `D:/Silence_cutter/contentops_process_bridge.py`
- Test: `D:/Silence_cutter/tests/test_lan_scheduler_handoff.py`

- [ ] Write tests proving a manual URL is tagged `MANUAL_LAN`, uses YTDOWNLOAD `:8790`, waits for a finalized path, and submits to `:8791` without any `:8792` request.
- [ ] Run the tests red.
- [ ] Implement the manual adapter as a thin producer of the shared scheduler job, preserving authentication and polling responses.
- [ ] Run the tests green.

### Task 3: Enforce durable FIFO and one processing slot

**Files:**
- Modify: `D:/Silence_cutter/contentops_process_bridge.py`
- Modify: `D:/Silence_cutter/backend` persistence module selected by existing bridge imports
- Test: `D:/Silence_cutter/tests/test_shared_scheduler_queue.py`

- [ ] Add failing tests for AUTO/MANUAL FIFO ordering, one active job, restart recovery, stable idempotency, terminal-job protection, and queue position.
- [ ] Run tests red.
- [ ] Implement the smallest queue persistence/locking change using existing storage; expose origin, active job, waiting jobs, and Qwen readiness in existing status/health responses.
- [ ] Run tests green and verify no second Qwen submission can occur while one job is active.

### Task 4: Remove stale production assumptions and document startup

**Files:**
- Modify: `D:/yt_notifi/app/config.py`
- Modify: `D:/yt_notifi/installer/packaged_launcher.ps1`
- Modify: `D:/yt_notifi/README.md`
- Modify: `D:/Silence_cutter/README.md`
- Test: `D:/yt_notifi/tests/test_launcher.py`, `D:/Silence_cutter/tests/test_port_contract.py`

- [ ] Write failing contract tests for the fixed ports, startup ordering, local-only 8791/8792, and LAN-only 8780.
- [ ] Run tests red.
- [ ] Remove obsolete automatic LAN configuration from production startup while retaining manual LAN configuration and token validation.
- [ ] Run the contract tests green.

### Task 5: Full verification and controlled acceptance

**Files:**
- Test: `D:/yt_notifi/tests`, `D:/Silence_cutter/tests`
- Create: `D:/yt_notifi/CONTENTOPS_FINAL_LOCAL_ARCHITECTURE_REPORT.md`

- [ ] Run focused routing, scheduler, launcher, and bridge tests.
- [ ] Run full Python and Node suites where available.
- [ ] Run `git diff --check` in both repositories and inspect `git status`.
- [ ] Perform one controlled AUTO and one MANUAL contention test, then reverse order if the live services are available.
- [ ] Record exact acceptance results, queue order, max concurrent Qwen jobs, and any blocked live checks in the report.
