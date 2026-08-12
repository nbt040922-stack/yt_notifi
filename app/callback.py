from __future__ import annotations

from threading import Lock

from .config import Settings, validate_public_origin


class ActiveCallback:
    def __init__(self, settings: Settings):
        self.webhook_path = settings.webhook_path
        self._static = validate_public_origin(settings.public_callback_url) if settings.public_callback_url else None
        self._runtime: str | None = None
        self._lock = Lock()

    def set_runtime(self, origin: str) -> bool:
        validated = validate_public_origin(origin)
        with self._lock:
            if validated == self._runtime:
                return False
            self._runtime = validated
            return True

    @property
    def origin(self) -> str:
        with self._lock:
            value = self._runtime or self._static
        if not value:
            raise ValueError("No active public callback origin")
        return value

    @property
    def callback_url(self) -> str:
        return self.origin + self.webhook_path

    @property
    def source(self) -> str:
        with self._lock:
            return "runtime" if self._runtime else "static" if self._static else "unconfigured"
