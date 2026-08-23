from dataclasses import replace

from app.process_worker import ProcessHandoffWorker
from app.download_worker import DownloadHandoffWorker
from app.state import StateStore
from tests.test_process_worker import Bridge, Response, downloaded_job, payload


class DownloadBridge:
    def __init__(self, response):
        self.response = response
        self.posts = []

    def post(self, url, json):
        self.posts.append((url, json))
        return self.response

    def get(self, url):
        return self.response


def test_auto_processing_ignores_manual_lan_endpoint(settings, tmp_path):
    settings = replace(
        settings,
        silence_cutter_lan_url="http://192.168.1.50:8780",
        silence_cutter_lan_token="manual-only-token",
    )
    state, source = downloaded_job(settings, tmp_path)
    bridge = Bridge([Response(payload())])

    ProcessHandoffWorker(settings, state, bridge).tick()

    assert len(bridge.posts) == 1
    assert bridge.posts[0][0] == "http://127.0.0.1:8791/api/process-jobs"
    assert bridge.posts[0][1]["source_file"] == str(source)
    assert bridge.posts[0][1]["origin"] == "AUTO_YT_NOTIFI"


def test_auto_processing_keeps_same_local_handoff_when_bridge_waits(settings, tmp_path):
    state, source = downloaded_job(settings, tmp_path)
    bridge = Bridge([Response(payload()), Response(payload("PROCESSING", progress_percent=20))])
    worker = ProcessHandoffWorker(settings, state, bridge)

    worker.tick()
    worker.tick()

    assert len(bridge.posts) == 1
    assert len(bridge.gets) == 1
    assert bridge.posts[0][1]["source_file"] == str(source)


def test_auto_download_ignores_manual_mode_and_requires_ytdownload(settings, tmp_path):
    settings = replace(settings, processing_work_root=tmp_path / "work")
    state = StateStore(settings.state_db)
    from app.models import VideoEvent
    event = VideoEvent("abcdefghijk", "UC_x5XG1OV2P6uZZ5FSM9Ttw", "Video", "", "", "https://youtu.be/abcdefghijk")
    assert state.create_processing_job(event, "Channel", str(tmp_path / "out"), "QUEUED", None)
    bridge = DownloadBridge(Response({"external_id": "download-1", "state": "QUEUED", "progress_percent": 0}))

    DownloadHandoffWorker(settings, state, bridge, remote_processing=True).tick()

    assert bridge.posts[0][0] == "http://127.0.0.1:8790/api/download-jobs"
