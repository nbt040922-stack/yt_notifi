"""Frozen entry point for the YT_NOTIFI Windows build."""
import os
import sys

from pathlib import Path


def main() -> None:
    base = Path(sys.executable).resolve().parent
    os.environ.setdefault("YT_NOTIFI_PACKAGED", "1")
    os.environ.setdefault("YT_NOTIFI_DATA_DIR", str(Path(os.environ.get("LOCALAPPDATA", base)) / "YT_NOTIFI"))
    bundled_ytdlp = base / "tools" / "yt-dlp.exe"
    if not bundled_ytdlp.is_file():
        bundled_ytdlp = base / "_internal" / "tools" / "yt-dlp.exe"
    if bundled_ytdlp.is_file():
        os.environ.setdefault("YTDLP_PATH", str(bundled_ytdlp))
    os.chdir(base)
    import uvicorn
    uvicorn.run("app.main:app", host=os.getenv("YT_NOTIFI_BIND_HOST", "0.0.0.0"), port=int(os.getenv("YT_NOTIFI_PORT", "8787")), log_level="info")


if __name__ == "__main__":
    main()
