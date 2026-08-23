# Windows Installer Implementation Plan

> **For agentic workers:** Use the existing launcher and test suite; keep the YouTube polling and notification pipeline unchanged.

**Goal:** Build a Windows installer and first-run experience that runs YT_NOTIFI without requiring Python or command-line knowledge.

**Architecture:** Keep the FastAPI application and polling workers intact. Add a packaging layer that stages the application, bundled Python runtime/dependencies, launcher scripts, and an Inno Setup installer. Store user configuration/state under `%LOCALAPPDATA%\YT_NOTIFI` and keep Silence Cutter external and optional.

**Tech Stack:** PowerShell, PyInstaller, Inno Setup, existing FastAPI/uvicorn application, Windows Task Scheduler.

**Spec:** User-provided Windows Packaging & Installer task.

## Global Constraints

- Do not refactor YouTube polling, yt-dlp, Telegram delivery, database schema, or processing pipeline.
- Do not package Silence Cutter inside YT_NOTIFI.
- Never commit `.env` or credentials.
- Preserve user data on update and normal uninstall.
- Silence Cutter health failure must not prevent YT_NOTIFI startup.

### Task 1: Packaging assets

- Create `installer/build-installer.ps1`, `installer/installer.iss`, and `installer/README.md`.
- Stage the current application with PyInstaller and copy configuration templates, scripts, and static assets.
- Fail clearly when PyInstaller or Inno Setup is unavailable.
- Ensure staged output excludes `.env`, `state`, `logs`, tests, and developer artifacts.

### Task 2: Runtime/user-data adapter

- Add a small config-path helper that prefers `%LOCALAPPDATA%\YT_NOTIFI` when packaged and preserves repository `.env` during development.
- Keep Telegram values in local `.env`; never log them.
- Add tests proving user data survives application replacement.

### Task 3: Setup and optional bridge status

- Add setup API/UI for Telegram token and chat ID validation plus a real test message.
- Add a “Kết nối Silence Cutter” check using the existing bridge `/health`; failure is informational only.
- Add focused API/UI tests and secret-redaction tests.

### Task 4: Windows background launcher

- Add a packaged launcher that starts YT_NOTIFI hidden, waits for `/health`, writes logs under `%LOCALAPPDATA%\YT_NOTIFI\logs`, and restarts with bounded backoff after crashes.
- Keep Task Scheduler registration/update/uninstall scripts idempotent.
- Add PowerShell/static tests for startup, recovery, and data preservation.

### Task 5: Build and verify

- Run targeted tests and `git diff --check`.
- Build `installer/output/YT_NOTIFI_Setup.exe` when Inno Setup is available.
- Verify installer metadata, staging exclusions, and README instructions.
