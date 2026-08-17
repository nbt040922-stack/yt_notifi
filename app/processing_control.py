from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .config import Settings
from .state import StateStore


logger = logging.getLogger("yt_notifi")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


class ProcessingControlStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    @staticmethod
    def default() -> dict[str, Any]:
        return {
            "silence_engine_enabled": True, "qwen_status": "STARTING",
            "qwen_pid": None, "qwen_started_at": None, "error": None,
            "off_requested_at": None,
        }

    def read(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return self.default()
            try:
                value = json.loads(self.path.read_text(encoding="utf-8-sig"))
                if not isinstance(value, dict) or not isinstance(value.get("silence_engine_enabled"), bool):
                    raise ValueError("invalid processing control")
                return value
            except (OSError, ValueError, TypeError):
                return {
                    "silence_engine_enabled": False, "qwen_status": "ERROR",
                    "qwen_pid": None, "qwen_started_at": None,
                    "error": "PROCESSING_CONTROL_INVALID", "off_requested_at": None,
                }

    def write(self, value: dict[str, Any]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + ".tmp")
            try:
                with temporary.open("w", encoding="utf-8", newline="\n") as file:
                    json.dump(value, file, ensure_ascii=False, indent=2)
                    file.write("\n")
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)


class QwenProcessManager:
    def __init__(self, settings: Settings, client=None):
        self.settings = settings
        self.client = client or httpx.Client(timeout=2)
        self.library = Path(__file__).resolve().parent.parent / "scripts" / "launcher_lib.ps1"

    def health(self) -> dict[str, Any] | None:
        try:
            response = self.client.get("http://127.0.0.1:8792/health")
            return response.json() if response.status_code == 200 else None
        except Exception:
            return None

    def start(self) -> tuple[int, str]:
        root = self.settings.silence_cutter_root
        python = root / ".venv_asr_test" / "Scripts" / "python.exe"
        if not python.is_file():
            raise RuntimeError("QWEN_RUNTIME_MISSING")
        logs = Path(__file__).resolve().parent.parent / "logs"
        logs.mkdir(exist_ok=True)
        stdout = (logs / "qwen-worker.stdout.log").open("ab")
        stderr = (logs / "qwen-worker.stderr.log").open("ab")
        environment = os.environ.copy()
        default_model = root / "local_models" / "Qwen2.5-VL-7B-Instruct-AWQ"
        if "SEMANTIC_QWEN_MODEL" not in environment and default_model.is_dir():
            environment["SEMANTIC_QWEN_MODEL"] = str(default_model)
        try:
            process = subprocess.Popen(
                [str(python), "-m", "qwen_worker.supervisor"], cwd=root,
                stdout=stdout, stderr=stderr,
                env=environment,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        finally:
            stdout.close()
            stderr.close()
        return process.pid, datetime.now(timezone.utc).isoformat()

    def _powershell(self, expression: str) -> bool:
        library = str(self.library).replace("'", "''")
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
             f". '{library}'; {expression}"],
            capture_output=True, text=True, timeout=15,
        )
        return result.returncode == 0 and result.stdout.strip().lower() == "true"

    def is_owned(self, pid: int | None, started_at: str | None) -> bool:
        if not pid or not started_at:
            return False
        safe_started = started_at.replace("'", "''")
        return self._powershell(
            f"Test-RuntimeProcess {int(pid)} '{safe_started}' 'qwen_worker.supervisor'"
        )

    def stop(self, pid: int | None, started_at: str | None) -> bool:
        if not pid or not started_at:
            return False
        safe_started = started_at.replace("'", "''")
        return self._powershell(
            f"Stop-OwnedProcessTree {int(pid)} '{safe_started}' 'qwen_worker.supervisor'"
        )

    def adopt_runtime(self) -> tuple[int, str] | None:
        try:
            runtime = json.loads(self.settings.production_runtime_file.read_text(encoding="utf-8-sig"))
            pid, started = runtime.get("qwen_worker_pid"), runtime.get("qwen_worker_started_at")
            if self.is_owned(pid, started):
                return int(pid), str(started)
        except (OSError, ValueError, TypeError):
            pass
        return None


class ProcessingControl:
    def __init__(
        self, settings: Settings, state: StateStore, manager: QwenProcessManager | None = None,
    ):
        self.settings = settings
        self.state = state
        self.store = ProcessingControlStore(settings.processing_control_file)
        self.manager = manager or QwenProcessManager(settings)
        self._lock = threading.RLock()

    def snapshot(self) -> dict[str, Any]:
        value = self.store.read()
        return {
            "silence_engine_enabled": value["silence_engine_enabled"],
            "qwen_status": value.get("qwen_status") or "ERROR",
            "error": value.get("error"),
            "waiting_jobs": self.state.waiting_process_job_count(),
        }

    def is_ready(self) -> bool:
        value = self.store.read()
        return bool(value["silence_engine_enabled"] and value.get("qwen_status") == "READY")

    def pause_reason(self) -> str:
        return "QWEN_NOT_READY" if self.store.read()["silence_engine_enabled"] else "SILENCE_ENGINE_DISABLED"

    def request(self, enabled: bool) -> dict[str, Any]:
        with self._lock:
            value = self.store.read()
            if enabled:
                if value["silence_engine_enabled"] and value.get("qwen_status") != "ERROR":
                    self.tick()
                    return self.snapshot()
                if value.get("qwen_status") == "ERROR" and value.get("qwen_pid"):
                    self.manager.stop(value.get("qwen_pid"), value.get("qwen_started_at"))
                value.update(
                    silence_engine_enabled=True, qwen_status="STARTING", qwen_pid=None,
                    qwen_started_at=None, error=None, off_requested_at=None,
                )
            else:
                value.update(
                    silence_engine_enabled=False, qwen_status="STOPPING",
                    off_requested_at=utc_now(), error=None,
                )
            self.store.write(value)
            self.tick()
            return self.snapshot()

    def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        with self._lock:
            value = self.store.read()
            if value["silence_engine_enabled"]:
                self._tick_enabled(value, now)
            else:
                self._tick_disabled(value, now)

    def _tick_enabled(self, value: dict[str, Any], now: datetime) -> None:
        health = self.manager.health()
        if health and health.get("status") == "READY" and health.get("model_loaded") and health.get("warmed_up"):
            if not value.get("qwen_pid"):
                adopted = self.manager.adopt_runtime()
                if adopted:
                    value.update(qwen_pid=adopted[0], qwen_started_at=adopted[1])
                else:
                    value.update(qwen_status="ERROR", error="UNOWNED_QWEN_PROCESS")
                    self.store.write(value)
                    return
            value.update(qwen_status="READY", error=None)
            self.store.write(value)
            return
        if health and health.get("status") == "ERROR":
            value.update(qwen_status="ERROR", error=str(health.get("error") or "QWEN_START_FAILED")[:500])
            self.store.write(value)
            return
        if value.get("qwen_pid") and not health and not self.manager.is_owned(
            value.get("qwen_pid"), value.get("qwen_started_at")
        ):
            value.update(qwen_pid=None, qwen_started_at=None)
        if not value.get("qwen_pid"):
            adopted = self.manager.adopt_runtime()
            if adopted:
                value.update(qwen_pid=adopted[0], qwen_started_at=adopted[1])
            elif health:
                value.update(qwen_status="ERROR", error="UNOWNED_QWEN_PROCESS")
                self.store.write(value)
                return
            else:
                try:
                    pid, started = self.manager.start()
                    value.update(qwen_pid=pid, qwen_started_at=started, qwen_status="STARTING", error=None)
                except Exception as exc:
                    value.update(qwen_status="ERROR", error=str(exc)[:500])
                self.store.write(value)
                return
        status = str((health or {}).get("status") or "STARTING")
        started = parse_time(value.get("qwen_started_at"))
        if started and (now - started).total_seconds() > self.settings.qwen_ready_timeout_seconds:
            value.update(qwen_status="ERROR", error="QWEN_START_TIMEOUT")
        else:
            value.update(qwen_status=status, error=None)
        self.store.write(value)

    def _tick_disabled(self, value: dict[str, Any], now: datetime) -> None:
        requested = parse_time(value.get("off_requested_at"))
        if requested is None and value.get("qwen_status") == "OFF":
            if self.manager.health():
                value.update(qwen_status="ERROR", error="QWEN_PORT_STILL_OPEN")
            else:
                value.update(qwen_pid=None, qwen_started_at=None, error=None)
            self.store.write(value)
            return
        if requested is None:
            requested = now
            value["off_requested_at"] = now.isoformat()
        if self.state.active_process_job_count() or (now - requested).total_seconds() < 2:
            value.update(qwen_status="STOPPING")
            self.store.write(value)
            return
        pid, started = value.get("qwen_pid"), value.get("qwen_started_at")
        if pid and self.manager.is_owned(pid, started):
            self.manager.stop(pid, started)
        health = self.manager.health()
        if health:
            value.update(qwen_status="ERROR", error="QWEN_PORT_STILL_OPEN")
        else:
            value.update(qwen_status="OFF", qwen_pid=None, qwen_started_at=None, error=None)
        self.store.write(value)

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await asyncio.to_thread(self.tick)
            try:
                await asyncio.wait_for(stop.wait(), timeout=1)
            except TimeoutError:
                pass
