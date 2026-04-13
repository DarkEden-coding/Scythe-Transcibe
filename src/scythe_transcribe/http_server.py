"""Start and stop the local Uvicorn server from the tray or tests."""

from __future__ import annotations

import threading

import uvicorn

from scythe_transcribe.config import API_HOST, API_PORT
from scythe_transcribe.frontend_build import ensure_frontend_built
from scythe_transcribe.hotkey_service import start_hotkey_listener
from scythe_transcribe.web_app import create_app


class LocalHttpServer:
    """Runs :func:`uvicorn.Server` on a background thread."""

    def __init__(self) -> None:
        """Initialize with no running server."""
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        """Whether the server thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Start Uvicorn if not already running."""
        if self.is_running:
            return
        ensure_frontend_built()
        app = create_app()
        config = uvicorn.Config(
            app,
            host=API_HOST,
            port=API_PORT,
            log_level="info",
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True, name="scythe-uvicorn")
        self._thread.start()
        start_hotkey_listener()

    def stop(self) -> None:
        """Signal shutdown and wait for the thread to finish."""
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=15.0)
        self._server = None
        self._thread = None


def run_foreground() -> None:
    """Block the current thread with Uvicorn (development / SCYTHE_SERVER_ONLY)."""
    ensure_frontend_built()
    start_hotkey_listener()
    app = create_app()
    uvicorn.run(app, host=API_HOST, port=API_PORT, log_level="info")
