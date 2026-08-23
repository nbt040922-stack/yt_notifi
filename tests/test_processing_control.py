from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.processing_control import ProcessingControl, ProcessingControlStore, QwenProcessManager
from app.process_worker import ProcessHandoffWorker
from app.state import StateStore
from tests.test_process_worker import Bridge, Response, downloaded_job, payload


NOW = datetime(2026, 8, 14, tzinfo=timezone.utc)


class Manager:
    def __init__(self):
        self.health_value = None
        self.started = []
        self.stopped = []
        self.owned = True
        self.adopted = None

    def health(self):
        return self.health_value

    def start(self):
        self.started.append(True)
        return 4321, NOW.isoformat()

    def is_owned(self, pid, started_at):
        return self.owned and pid == 4321

    def stop(self, pid, started_at):
        self.stopped.append((pid, started_at))
        return True

    def adopt_runtime(self):
        return self.adopted


def control(settings, state=None, manager=None):
    return ProcessingControl(settings, state or StateStore(settings.state_db), manager or Manager())


def test_missing_control_defaults_enabled_and_off_survives_restart(settings):
    store = ProcessingControlStore(settings.processing_control_file)
    assert store.read()["silence_engine_enabled"] is True

    first = control(settings)
    first.request(False)

    restarted = control(settings)
    assert restarted.snapshot()["silence_engine_enabled"] is True
    assert restarted.snapshot()["qwen_status"] == "ERROR"


def test_on_starts_worker_and_requires_fully_ready_health(settings):
    manager = Manager()
    engine = control(settings, manager=manager)
    engine.tick(NOW)
    assert engine.is_ready() is False

    manager.health_value = {"status": "WARMING", "model_loaded": True, "warmed_up": False}
    engine.tick(NOW + timedelta(seconds=1))
    assert engine.snapshot()["qwen_status"] == "WARMING"
    assert engine.is_ready() is False

    manager.health_value = {"status": "READY", "model_loaded": True, "warmed_up": True}
    engine.tick(NOW + timedelta(seconds=2))
    assert engine.is_ready() is True


def test_processing_control_probes_scheduler_not_qwen(settings):
    class Client:
        def __init__(self): self.urls = []
        def get(self, url):
            self.urls.append(url)
            return type("Response", (), {"json": lambda _self: {"status": "READY", "qwen_status": "READY", "enhanced_ready": True}})()
    client = Client()
    manager = QwenProcessManager(settings, client=client)
    assert manager.health()["qwen_status"] == "READY"
    assert client.urls == ["http://127.0.0.1:8791/health"]


def test_repeated_on_keeps_owned_pid_mapping(settings):
    manager = Manager()
    engine = control(settings, manager=manager)
    engine.tick(NOW)
    before = engine.store.read()
    engine.request(True)
    after = engine.store.read()
    assert (after["qwen_pid"], after["qwen_started_at"]) == (
        before["qwen_pid"], before["qwen_started_at"]
    )
    assert len(manager.started) == 0


def test_off_drains_existing_bridge_job_before_stopping(settings, tmp_path):
    state, _ = downloaded_job(settings, tmp_path)
    job = state.processing_jobs()[0]
    state.update_process_job(
        job["id"], status="PROCESSING", external_id="contentops-process-1",
        process_state="PROCESSING",
    )
    manager = Manager()
    engine = control(settings, state, manager)
    engine.store.write({
        **engine.store.default(), "qwen_pid": 4321, "qwen_started_at": NOW.isoformat(),
        "qwen_status": "READY",
    })

    engine.request(False)
    assert engine.snapshot()["qwen_status"] == "ERROR"
    assert manager.stopped == []


def test_off_holds_downloaded_job_then_on_resumes_same_handoff(settings, tmp_path):
    state, _ = downloaded_job(settings, tmp_path)
    manager = Manager()
    engine = control(settings, state, manager)
    engine.store.write({**engine.store.default(), "silence_engine_enabled": False, "qwen_status": "OFF"})
    bridge = Bridge([Response(payload("PROCESSING"))])
    worker = ProcessHandoffWorker(settings, state, bridge, engine)

    worker.tick(NOW)
    waiting = state.processing_jobs()[0]
    assert waiting["status"] == "PROCESS_PENDING"
    assert waiting["process_external_id"] is None
    assert waiting["process_attempts"] == 0
    assert bridge.posts == []

    engine.store.write({
        **engine.store.default(), "qwen_status": "READY", "qwen_pid": 4321,
        "qwen_started_at": NOW.isoformat(),
    })
    worker.tick(NOW + timedelta(seconds=1))
    assert len(bridge.posts) == 1
    assert bridge.posts[0][1]["handoff_id"] == str(waiting["id"])
    assert state.processing_jobs()[0]["process_external_id"] == "contentops-process-1"


def test_off_never_stops_unowned_process(settings):
    manager = Manager()
    manager.owned = False
    engine = control(settings, manager=manager)
    engine.store.write({
        **engine.store.default(), "silence_engine_enabled": False, "qwen_status": "STOPPING",
        "qwen_pid": 4321, "qwen_started_at": NOW.isoformat(),
        "off_requested_at": (NOW - timedelta(seconds=3)).isoformat(),
    })
    engine.tick(NOW)
    assert manager.stopped == []
    assert engine.snapshot()["qwen_status"] == "ERROR"


def test_launcher_off_state_remains_stably_off(settings):
    manager = Manager()
    engine = control(settings, manager=manager)
    engine.store.write({
        **engine.store.default(), "silence_engine_enabled": False, "qwen_status": "OFF",
        "off_requested_at": None,
    })

    engine.tick(NOW)
    assert engine.snapshot()["qwen_status"] == "ERROR"
    assert manager.stopped == []


@pytest.mark.parametrize("qwen_status", ["READY", "STARTING", "ERROR"])
def test_every_enabled_state_requests_off(settings, qwen_status):
    engine = control(settings)
    engine.store.write({
        **engine.store.default(), "silence_engine_enabled": True,
        "qwen_status": qwen_status,
    })

    requested = engine.request(False)
    assert requested["silence_engine_enabled"] is True
    assert requested["qwen_status"] == "ERROR"


def test_on_error_turns_off_without_stopping_external_worker(settings):
    manager = Manager()
    engine = control(settings, manager=manager)
    engine.store.write({
        **engine.store.default(), "silence_engine_enabled": True, "qwen_status": "ERROR",
        "qwen_pid": 4321, "qwen_started_at": NOW.isoformat(), "error": "QWEN_START_TIMEOUT",
    })

    requested = engine.request(False)
    assert requested["silence_engine_enabled"] is True
    assert requested["qwen_status"] == "ERROR"
    assert len(manager.stopped) == 0


def test_off_error_can_start_clean_retry(settings):
    manager = Manager()
    engine = control(settings, manager=manager)
    engine.store.write({
        **engine.store.default(), "silence_engine_enabled": False, "qwen_status": "ERROR",
        "error": "QWEN_PORT_STILL_OPEN",
    })
    started = engine.request(True)
    assert started["silence_engine_enabled"] is True
    assert started["qwen_status"] == "ERROR"
    assert started["error"] == "QWEN_NOT_REACHABLE" and len(manager.started) == 0


def test_off_ignores_external_qwen_health_error(settings):
    manager = Manager()
    manager.health_value = {"status": "ERROR"}
    engine = control(settings, manager=manager)
    engine.store.write({
        **engine.store.default(), "silence_engine_enabled": False, "qwen_status": "STOPPING",
        "qwen_pid": 4321, "qwen_started_at": NOW.isoformat(),
        "off_requested_at": (NOW - timedelta(seconds=3)).isoformat(),
    })
    engine.tick(NOW)
    snapshot = engine.snapshot()
    assert snapshot["silence_engine_enabled"] is True
    assert snapshot["qwen_status"] == "ERROR"
    assert snapshot["error"] == "QWEN_NOT_REACHABLE"


def test_dynamic_qwen_start_supplies_existing_default_model(settings, monkeypatch):
    root = settings.silence_cutter_root
    python = root / ".venv_asr_test" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    model = root / "local_models" / "Qwen2.5-VL-7B-Instruct-AWQ"
    model.mkdir(parents=True)
    captured = {}

    class Process:
        pid = 4321

    def popen(command, **kwargs):
        captured.update(command=command, **kwargs)
        return Process()

    monkeypatch.delenv("SEMANTIC_QWEN_MODEL", raising=False)
    monkeypatch.setattr("app.processing_control.subprocess.Popen", popen)
    pid, _ = QwenProcessManager(settings).start()
    assert pid == 4321
    assert captured["env"]["SEMANTIC_QWEN_MODEL"] == str(model)
