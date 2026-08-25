# ContentOps Client Installer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Đóng gói YT_NOTIFI và YTDOWNLOAD thành một bộ cài/bootstrap Windows có cấu hình riêng, health check, autostart, watchdog và update an toàn.

**Architecture:** Bộ cài giữ source/runtime bất biến trong `app\`, dữ liệu mutable trong `data\`. Một supervisor PowerShell quản lý hai tiến trình độc lập trên `127.0.0.1:8787` và `127.0.0.1:8790`, chỉ restart thành phần lỗi. Bootstrap và update bảo toàn cấu hình/state.

**Tech Stack:** PowerShell, Python venv/uvicorn, Electron/YTDOWNLOAD, Windows Task Scheduler, Pester-free script tests via pytest fixtures.

**Spec:** `C:\Users\nbt04\.codex\attachments\900c18cb-55ae-4523-b62e-923019570835\pasted-text.txt`

## Global Constraints

- Không đóng gói Silence Cutter, Qwen, Manual LAN API hoặc TikTok Publisher.
- Không đổi pipeline production và hợp đồng YT_NOTIFI → YTDOWNLOAD.
- Cổng cố định localhost: YT_NOTIFI `8787`, YTDOWNLOAD `8790`.
- Không đưa secret thật vào Git/package.
- Upgrade không xóa config/state/history.

### Task 1: Runtime manifest and environment checker

**Files:** Create `installer/client-manifest.json`, `installer/scripts/Check-ContentOpsEnvironment.ps1`, `installer/scripts/Repair-ContentOpsEnvironment.ps1`, `tests/test_client_installer.py`.

- [ ] Viết test kiểm tra manifest chỉ có hai component, đúng cổng và các script tồn tại.
- [ ] Chạy test để xác nhận RED.
- [ ] Tạo manifest chứa repo roots, ports, health URLs, runtime commands và config/state paths.
- [ ] Checker kiểm tra Windows/x64, Python/venv, Node/npm/Electron, yt-dlp/FFmpeg/ffprobe, port conflict và writable dirs; trả mã `PORT_CONFLICT`.
- [ ] Repair chỉ tạo thư mục, venv/dependency và config template khi thiếu; không đụng driver/firewall/secret.
- [ ] Chạy test GREEN.

### Task 2: Start/stop/status/watchdog supervisor

**Files:** Create `installer/scripts/Start-ContentOpsClient.ps1`, `Stop-ContentOpsClient.ps1`, `Restart-ContentOpsClient.ps1`, `Status-ContentOpsClient.ps1`, `Watch-ContentOpsClient.ps1`, `tests/test_client_supervisor.py`.

- [ ] Viết test cho PID ownership, duplicate start prevention, per-component restart và backoff.
- [ ] Chạy RED.
- [ ] Implement process records dưới `data\runtime`, health polling, bounded delays `3/10/30/60`, log rotation và không taskkill toàn cục.
- [ ] Implement status output build SHA/PID/uptime.
- [ ] Chạy GREEN.

### Task 3: Bootstrap/update/uninstall and Task Scheduler

**Files:** Create `installer/Setup-ContentOpsClient.ps1`, `Update-ContentOpsClient.ps1`, `Uninstall-ContentOpsClient.ps1`, `installer/README-INSTALL.txt`, `tests/test_client_lifecycle.py`.

- [ ] Viết test idempotent bootstrap, config preservation, uninstall preserving data.
- [ ] Chạy RED.
- [ ] Implement deterministic install root `C:\ProgramData\ContentOps\Client`, copy immutable app trees, create `.env` from template, install two hidden logon tasks and start/health-check.
- [ ] Implement update stop/copy/start/rollback while preserving `data\config`, `data\state`, `data\logs`.
- [ ] Implement uninstall removing only owned files/tasks; full-clean explicit.
- [ ] Chạy GREEN.

### Task 4: Package build and acceptance

**Files:** Modify `installer/build-installer.ps1`; create `installer/build-contentops-client.ps1`, `installer/ContentOpsClient.iss`, `installer/CONTENTOPS_CLIENT_INSTALLER_REPORT.md`.

- [ ] Build package without Silence/Qwen/Manual LAN.
- [ ] Run isolated bootstrap/status acceptance, kill one owned process at a time, verify watchdog restarts only it, rerun bootstrap and config preservation.
- [ ] Run YT_NOTIFI/YTDOWNLOAD regression tests and `git diff --check`.
- [ ] Record source SHAs, live acceptance and pipeline unchanged. Do not push until report review.
