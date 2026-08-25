from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

from app.config import Settings

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


def test_production_launcher_does_not_take_yt_notifi_down_when_ytdownload_exits():
    script = (ROOT / "scripts" / "start_production.ps1").read_text(encoding="utf-8")
    assert 'throw "Owned service exited pid=$($service.Id)"' not in script
    assert "YTDOWNLOAD" in script and "YT_NOTIFI" in script


def test_three_independent_supervisors_have_one_health_endpoint_each():
    root = ROOT / "scripts"
    for name, port in (("supervise_yt_notifi.ps1", "8787"), ("supervise_ytdownload.ps1", "8790"), ("supervise_silence.ps1", "8791")):
        text = (root / name).read_text(encoding="utf-8")
        assert port in text
        assert "Watch-Service" in text
    watcher = (root / "Watch-Service.ps1").read_text(encoding="utf-8")
    assert "Invoke-RestMethod" in watcher
    assert "Restart" in watcher
    assert "Start-ThreeSupervisors.ps1" in (root / "start_production_hidden.vbs").read_text(encoding="utf-8")


def test_launcher_venv_missing_is_explicit(tmp_path):
    result = ps(
        f"try {{ Get-VenvPython '{quoted(tmp_path)}' }} catch {{ $_.Exception.Message }}"
    )
    assert "Python virtual environment missing" in result.stdout
    assert "Run setup first" in result.stdout


def test_ytdlp_missing_is_fatal(tmp_path):
    result = ps(
        "$env:YTDLP_PATH=''; "
        f"try {{ Assert-YtDlp '{quoted(tmp_path)}' }} catch {{ $_.Exception.Message }}"
    )
    assert "yt-dlp is required for YT_NOTIFI polling" in result.stdout


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


def test_runtime_json_written_and_read(tmp_path):
    runtime = tmp_path / "runtime.json"
    result = ps(
        f"Write-RuntimeState '{quoted(runtime)}' ([pscustomobject]@{{launcher_pid=1;watcher_pid=2;started_at='now'}}); "
        f"$r=Read-RuntimeState '{quoted(runtime)}'; @($r.psobject.Properties.Name) -join ','"
    )
    assert set(result.stdout.strip().split(",")) == {"launcher_pid", "watcher_pid", "started_at"}
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


def test_launcher_starts_no_cloudflare_or_callback_process():
    script = (ROOT / "scripts" / "start_all.ps1").read_text(encoding="utf-8").lower()
    assert "cloudflared" not in script
    assert "tunnel" not in script
    assert "callback" not in script


def test_runtime_state_is_polling_only():
    script = (ROOT / "scripts" / "start_all.ps1").read_text(encoding="utf-8").lower()
    assert "launcher_pid" in script
    assert "watcher_pid" in script
    for removed in ("cloudflared_pid", "tunnel_url", "callback_url", "callback_generation"):
        assert removed not in script


def test_stop_script_only_targets_launcher_and_watcher():
    script = (ROOT / "scripts" / "stop_all.ps1").read_text(encoding="utf-8").lower()
    assert "uvicorn app.main:app" in script
    assert "start_all.ps1" in script
    assert "cloudflared" not in script


def test_production_health_timeout():
    result = ps("Wait-ServiceHealth 1 'http://127.0.0.1:9999/health' 1 { $true } { $false }")
    assert result.stdout.strip() == "TIMEOUT"


def test_qwen_health_accepts_only_loaded_and_warm_ready():
    result = ps(
        "$script:i=0; $states=@("
        "[pscustomobject]@{status='STARTING';model_loaded=$false;warmed_up=$false},"
        "[pscustomobject]@{status='LOADING_MODEL';model_loaded=$false;warmed_up=$false},"
        "[pscustomobject]@{status='WARMING_UP';model_loaded=$true;warmed_up=$false},"
        "[pscustomobject]@{status='READY';model_loaded=$true;warmed_up=$true});"
        "$global:seen=@(); $result=Wait-QwenReady 1 5 {$true} {$value=$states[$script:i];"
        "$script:i=[Math]::Min($script:i+1,$states.Count-1);$value} {$global:seen += $args[0]};"
        "Write-Output $result; Write-Output ($global:seen -join ',')"
    )
    assert result.stdout.strip().splitlines() == [
        "READY", "STARTING,LOADING_MODEL,WARMING_UP,READY"
    ]


def test_qwen_error_fails_startup_wait():
    result = ps(
        "Wait-QwenReady 1 2 {$true} "
        "{[pscustomobject]@{status='ERROR';model_loaded=$false;warmed_up=$false}}"
    )
    assert result.stdout.strip() == "ERROR"


def test_configurable_dashboard_bind(monkeypatch):
    monkeypatch.setenv("YT_NOTIFI_BIND_HOST", "0.0.0.0")
    monkeypatch.setenv("YT_NOTIFI_PORT", "9876")
    configured = Settings.from_env()
    assert (configured.host, configured.port) == ("0.0.0.0", 9876)


def test_lan_address_prefers_default_route_and_ignores_virtual():
    result = ps(
        "$items=@("
        "[pscustomobject]@{Address='192.168.1.31';Interface='Ethernet';HasDefaultRoute=$true;Metric=25;IsUp=$true},"
        "[pscustomobject]@{Address='10.0.0.9';Interface='vEthernet WSL';HasDefaultRoute=$true;Metric=1;IsUp=$true},"
        "[pscustomobject]@{Address='172.20.0.4';Interface='Wi-Fi';HasDefaultRoute=$false;Metric=5;IsUp=$true});"
        "Get-PrivateLanIPv4 $items"
    )
    assert result.stdout.strip() == "192.168.1.31"


def test_production_startup_order_and_local_health_urls():
    script = (ROOT / "scripts" / "start_production.ps1").read_text(encoding="utf-8")
    assert script.index("8790/health") < script.index("8791/health") < script.index(":$port/health")
    assert "http://127.0.0.1:8790/health" in script
    assert "http://127.0.0.1:8791/health" in script
    assert "http://127.0.0.1:$port/health" in script
    assert 'Start-Process -FilePath $silencePython -ArgumentList @("-m", "qwen_worker.supervisor")' in script
    assert '-WorkingDirectory $silenceRoot -WindowStyle Hidden' in script
    assert 'local_models\\Qwen2.5-VL-7B-Instruct-AWQ' in script
    assert "$env:SEMANTIC_QWEN_MODEL" in script
    assert "Wait-QwenReady $qwen_worker.Id $QwenTimeoutSeconds" in script
    assert "Qwen Worker port 8792 is already in use" in script
    assert script.index("qwen_worker.supervisor") < script.index("Assert-Healthy $ytdownload")
    assert '"--host", $bindHost' in script
    assert script.index("Test-LocalPortListening 8790") < script.index("Start-Process -FilePath $electron")
    assert script.index("Test-LocalPortListening 8791") < script.index("Start-Process -FilePath $silencePython")
    assert script.index("Test-LocalPortListening $port") < script.index("Start-Process -FilePath $watcherPython")

    legacy = (ROOT / "scripts" / "start_all.ps1").read_text(encoding="utf-8")
    assert "$env:YT_NOTIFI_BIND_HOST" in legacy and "$env:YT_NOTIFI_PORT" in legacy
    assert "http://127.0.0.1:$port/health" in legacy


def test_production_duplicate_guard_and_failure_cleanup():
    script = (ROOT / "scripts" / "start_production.ps1").read_text(encoding="utf-8")
    assert "Local\\CONTENTOPS_PRODUCTION_LAUNCHER" in script
    assert "Content Ops production is already running." in script
    final = script.index("} finally {")
    assert script.index("Stop-ProductionChildren", final) > final


def test_production_launcher_honors_persisted_engine_off():
    script = (ROOT / "scripts" / "start_production.ps1").read_text(encoding="utf-8")
    assert 'state\\processing-control.json' in script
    assert '$engineEnabled = $(if ($null -eq $control) { $true }' in script
    assert 'if ($engineEnabled) {' in script
    monitor = script[script.index("while ($true)"):]
    assert '@($ytdownload, $silence, $watcher)' in monitor
    assert '@($ytdownload, $qwen_worker, $silence, $watcher)' not in monitor


def test_stop_order_and_owned_process_validation():
    script = (ROOT / "scripts" / "stop_production.ps1").read_text(encoding="utf-8")
    assert script.index('"watcher"') < script.index('"silence"') < script.index('"ytdownload"')
    assert '"qwen_worker", "qwen_worker.supervisor"' in script
    assert "Stop-OwnedProcessTree" in script and "production-runtime.json" in script
    assert 'PSObject.Properties[$pidName]' in script
    assert 'processing-control.json' in script and '"qwen_pid"' in script


def test_qwen_runtime_status_and_firewall_boundary():
    start = (ROOT / "scripts" / "start_production.ps1").read_text(encoding="utf-8")
    status = (ROOT / "scripts" / "production_status.ps1").read_text(encoding="utf-8")
    firewall = (ROOT / "scripts" / "setup_lan_access.ps1").read_text(encoding="utf-8")
    assert '"qwen_worker"' in start and 'qwen_worker_port"] = 8792' in start
    assert "http://127.0.0.1:8792/health" in status
    assert "Model Loaded" in status and "Warm" in status
    assert "8792" not in firewall


def test_startup_task_install_is_idempotent_and_user_scoped():
    script = (ROOT / "scripts" / "install_windows_startup.ps1").read_text(encoding="utf-8")
    assert '"ContentOps Production"' in script
    assert "-AtLogOn -User $user" in script
    assert "-LogonType Interactive" in script
    assert '"wscript.exe"' in script
    assert "start_production_hidden.vbs" in script
    assert "StartupDelaySeconds" not in script
    assert "-Force" in script


def test_hidden_wrapper_owns_the_only_startup_delay():
    wrapper = (ROOT / "scripts" / "start_production_hidden.vbs").read_text(encoding="utf-8")
    assert "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden" in wrapper
    assert "start_production.ps1" in wrapper
    assert "-StartupDelaySeconds 20" in wrapper
    assert "shell.Run command, 0, False" in wrapper
    assert "TELEGRAM" not in wrapper and "TOKEN" not in wrapper


def test_production_scripts_parse():
    names = (
        "start_production.ps1", "stop_production.ps1", "production_status.ps1",
        "setup_lan_access.ps1", "install_windows_startup.ps1", "uninstall_windows_startup.ps1",
    )
    for name in names:
        path = quoted(ROOT / "scripts" / name)
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             f"$e=$null; [Management.Automation.Language.Parser]::ParseFile('{path}',[ref]$null,[ref]$e)|Out-Null; $e.Count"],
            capture_output=True, text=True, check=True,
        )
        assert result.stdout.strip() == "0", name
