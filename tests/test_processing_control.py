from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.processing_control import ProcessingControl, ProcessingControlStore
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
    requested = datetime.fromisoformat(first.store.read()["off_requested_at"])
    first.tick(requested + timedelta(seconds=3))

    restarted = control(settings)
    assert restarted.snapshot()["silence_engine_enabled"] is False
    assert restarted.snapshot()["qwen_status"] == "OFF"


def test_on_starts_worker_and_requires_fully_ready_health(settings):
    manager = Manager()
    engine = control(settings, manager=manager)
    engine.tick(NOW)
    assert len(manager.started) == 1
    assert engine.is_ready() is False

    manager.health_value = {"status": "WARMING", "model_loaded": True, "warmed_up": False}
    engine.tick(NOW + timedelta(seconds=1))
    assert engine.snapshot()["qwen_status"] == "WARMING"
    assert engine.is_ready() is False

    manager.health_value = {"status": "READY", "model_loaded": True, "warmed_up": True}
    engine.tick(NOW + timedelta(seconds=2))
    assert engine.is_ready() is True


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
    assert len(manager.started) == 1


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
    requested = datetime.fromisoformat(engine.store.read()["off_requested_at"])
    engine.tick(requested + timedelta(seconds=3))
    assert engine.snapshot()["qwen_status"] == "STOPPING"
    assert manager.stopped == []

    state.update_process_job(job["id"], status="COMPLETED", external_id="contentops-process-1")
    engine.tick(requested + timedelta(seconds=4))
    assert engine.snapshot()["qwen_status"] == "OFF"
    assert len(manager.stopped) == 1


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
    assert engine.snapshot()["qwen_status"] == "OFF"
