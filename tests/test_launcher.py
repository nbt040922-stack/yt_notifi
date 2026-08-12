from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "scripts" / "launcher_lib.ps1"


def ps(code: str, *, check: bool = True) -> subprocess.CompletedProcess:
    command = f". '{LIB}'; {code}"
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=check,
    )


def quoted(path: Path) -> str:
    return str(path).replace("'", "''")


def test_launcher_project_root_resolution():
    result = ps(f"Get-ProjectRoot '{quoted(ROOT / 'scripts')}'")
    assert result.stdout.strip() == str(ROOT)


def test_launcher_venv_missing_is_explicit(tmp_path):
    result = ps(
        f"try {{ Get-VenvPython '{quoted(tmp_path)}' }} catch {{ $_.Exception.Message }}"
    )
    assert "Python virtual environment missing" in result.stdout
    assert "Run setup first" in result.stdout


def test_ytdlp_missing_is_degraded_not_exception(tmp_path):
    result = ps(
        f"$found=Find-LauncherExecutable '' '{quoted(tmp_path / 'yt-dlp.exe')}' 'missing-yt-dlp-test'; "
        "if ($null -eq $found) { 'MISSING' }"
    )
    assert result.stdout.strip() == "MISSING"


def test_cloudflared_missing_is_fatal(tmp_path):
    result = ps(
        f"$env:CLOUDFLARED_PATH=''; try {{ Assert-Cloudflared '{quoted(tmp_path)}' }} "
        "catch { $_.Exception.Message }"
    )
    assert "cloudflared.exe missing" in result.stdout


def test_duplicate_launcher_mutex():
    name = f"Local\\YT_NOTIFI_TEST_{uuid.uuid4().hex}"
    holder_code = (
        f". '{LIB}'; $m=Enter-LauncherMutex '{name}'; 'READY'; [Console]::Out.Flush(); "
        "Start-Sleep -Seconds 10"
    )
    holder = subprocess.Popen(
        ["powershell.exe", "-NoProfile", "-Command", holder_code],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "READY"
        result = ps(f"$m=Enter-LauncherMutex '{name}'; if ($null -eq $m) {{ 'ALREADY' }}")
        assert result.stdout.strip() == "ALREADY"
    finally:
        holder.terminate()
        holder.wait(timeout=5)


def test_stale_runtime_pid_recovery(tmp_path):
    runtime = tmp_path / "runtime.json"
    result = ps(
        f"Write-RuntimeState '{quoted(runtime)}' ([pscustomobject]@{{launcher_pid=999999;"
        "launcher_started_at='2000-01-01T00:00:00Z'}); "
        f"(Get-RuntimeStatus '{quoted(runtime)}').Status"
    )
    assert result.stdout.strip() == "STALE"


def test_watcher_health_success():
    result = ps("Wait-LocalHealth 1 1 { $true } { $true }")
    assert result.stdout.strip() == "OK"


def test_watcher_health_timeout():
    result = ps("Wait-LocalHealth 1 1 { $true } { $false }")
    assert result.stdout.strip() == "TIMEOUT"


def test_watcher_exit_before_health():
    result = ps("Wait-LocalHealth 1 1 { $false } { $true }")
    assert result.stdout.strip() == "EXITED"


def test_tunnel_url_extraction():
    result = ps("Get-TunnelUrl 'INF ready https://quick-test-123.trycloudflare.com connected'")
    assert result.stdout.strip() == "https://quick-test-123.trycloudflare.com"


def test_runtime_json_written_and_read(tmp_path):
    runtime = tmp_path / "runtime.json"
    result = ps(
        f"Write-RuntimeState '{quoted(runtime)}' ([pscustomobject]@{{launcher_pid=1;tunnel_url='https://x.trycloudflare.com'}}); "
        f"(Read-RuntimeState '{quoted(runtime)}').tunnel_url"
    )
    assert result.stdout.strip() == "https://x.trycloudflare.com"
    assert runtime.exists()


def test_stop_only_accepts_matching_owned_process():
    result = ps(
        "$p=Get-Process -Id $PID; $started=Get-ProcessStartUtc $p; "
        "$marker=('marker-' + 'that-is-not-present'); Stop-OwnedProcess $PID $started $marker -WhatIf"
    )
    assert result.stdout.strip() == "False"


def test_clean_shutdown_removes_runtime(tmp_path):
    runtime = tmp_path / "runtime.json"
    result = ps(
        f"Write-RuntimeState '{quoted(runtime)}' ([pscustomobject]@{{launcher_pid=1}}); "
        f"Remove-RuntimeState '{quoted(runtime)}'; Test-Path '{quoted(runtime)}'"
    )
    assert result.stdout.strip() == "False"


def test_launcher_log_redacts_secrets(tmp_path):
    log = tmp_path / "launcher.log"
    result = ps(
        "$env:TELEGRAM_BOT_TOKEN='top-secret-token'; $env:TELEGRAM_CHAT_ID='private-chat'; "
        f"Write-LauncherLog '{quoted(log)}' 'token=top-secret-token chat=private-chat'; "
        f"Get-Content -Raw '{quoted(log)}'"
    )
    assert "top-secret-token" not in result.stdout
    assert "private-chat" not in result.stdout
    assert result.stdout.count("[REDACTED]") == 2
