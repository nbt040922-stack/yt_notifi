"""Frozen entry point for the YT_NOTIFI Windows build."""
import os
import subprocess
import sys
import time
import urllib.request

from pathlib import Path


def _health_ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def _start_local_ytdownload(base: Path) -> subprocess.Popen | None:
    """Start the sibling Electron bridge when this is the packaged client."""
    electron = base.parent / "ytdownload" / "YTDOWNLOAD.exe"
    if not electron.is_file() or _health_ready(8790):
        return None
    env = os.environ.copy()
    env.update({"CONTENTOPS_HEADLESS": "1", "CONTENTOPS_BRIDGE_PORT": "8790"})
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    child = subprocess.Popen(
        [str(electron)],
        cwd=str(electron.parent),
        env=env,
        creationflags=flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if _health_ready(8790):
            return child
        if child.poll() is not None:
            raise RuntimeError("YTDOWNLOAD_EXITED_BEFORE_HEALTH")
        time.sleep(0.5)
    child.terminate()
    raise RuntimeError("YTDOWNLOAD_HEALTH_TIMEOUT")


def _worker_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--worker"]
    return [sys.executable, __file__, "--worker"]


def _run_worker() -> None:
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.getenv("YT_NOTIFI_BIND_HOST", "0.0.0.0"),
        port=int(os.getenv("YT_NOTIFI_PORT", "8787")),
        log_level="info",
    )


def _run_supervisor(base: Path) -> None:
    """Keep both packaged services alive without a PowerShell runtime."""
    electron = base.parent / "ytdownload" / "YTDOWNLOAD.exe"
    ytdownload = None
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        while True:
            if electron.is_file() and not _health_ready(8790):
                if ytdownload is None or ytdownload.poll() is not None:
                    ytdownload = _start_local_ytdownload(base)
            worker = subprocess.Popen(
                _worker_command(),
                cwd=str(base),
                env=os.environ.copy(),
                creationflags=flags,
            )
            while worker.poll() is None:
                if electron.is_file() and not _health_ready(8790):
                    if ytdownload is None or ytdownload.poll() is not None:
                        ytdownload = _start_local_ytdownload(base)
                time.sleep(2)
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    finally:
        if ytdownload is not None and ytdownload.poll() is None:
            ytdownload.terminate()


def main() -> None:
    base = Path(sys.executable).resolve().parent
    os.environ.setdefault("YT_NOTIFI_PACKAGED", "1")
    # The proven legacy launcher injects YT_NOTIFI_DATA_DIR. Keep the direct
    # fallback compatible with the old per-user deployment as well.
    data_parent = os.environ.get("LOCALAPPDATA") or str(base)
    os.environ.setdefault("YT_NOTIFI_DATA_DIR", str(Path(data_parent) / "YT_NOTIFI"))
    bundled_ytdlp = base / "tools" / "yt-dlp.exe"
    if not bundled_ytdlp.is_file():
        bundled_ytdlp = base / "_internal" / "tools" / "yt-dlp.exe"
    if bundled_ytdlp.is_file():
        os.environ.setdefault("YTDLP_PATH", str(bundled_ytdlp))
    os.chdir(base)
    if "--worker" in sys.argv:
        _run_worker()
    else:
        _run_supervisor(base)


if __name__ == "__main__":
    main()
