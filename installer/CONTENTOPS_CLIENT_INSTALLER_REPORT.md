# CONTENTOPS CLIENT INSTALLER REPORT

## Source checkpoints

- YT_NOTIFI: `9e1b15ddc3a717b95dbde647f10b94ee02743b15`
- YTDOWNLOAD: `c43ba3645987c3e8125037414e7e59be9f7eb8f7`

## Package

- Format: `ContentOpsClient.zip` bootstrap package
- Install root: `C:\ProgramData\ContentOps\Client`
- Components: YT_NOTIFI `127.0.0.1:8787`, YTDOWNLOAD `127.0.0.1:8790`
- Excluded services: Silence Cutter, Qwen, Manual LAN API, TikTok Publisher runtime
- Mutable data: `config`, `state`, `logs`

## Deployment controls

- Environment checker and safe repair scripts included.
- Start/stop/restart/status/watchdog scripts included.
- Watchdog uses bounded backoff and PID files; it does not taskkill global Python/Node processes.
- Setup/update preserve config and state.
- Uninstall preserves config/state/logs unless `-FullClean` is explicitly supplied.
- Build identity records both source SHAs without credentials.

## Verification

- Installer tests: `3 passed`.
- PowerShell parser: all installer scripts valid.
- Package build: `D:\ContentOpsClientBuild.zip` created successfully.
- Package contents: 2,335 files; no Silence Cutter/Qwen/Manual LAN service binaries.
- Production pipeline code changed: NO.

## Live acceptance

The development machine already owns ports 8787/8790, so isolated live crash-recovery testing was not performed to avoid touching production jobs. The package build and static lifecycle checks passed; run `Setup-ContentOpsClient.ps1` on an isolated coworker/test machine for the final two-port acceptance.

FINAL: READY_FOR_ISOLATED_INSTALL_ACCEPTANCE
