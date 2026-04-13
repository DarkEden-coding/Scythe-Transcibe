"""Start, stop, and wake the local settings server from the tray."""

from __future__ import annotations

import contextlib
import http.server
import socketserver
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from scythe_transcribe.config import API_HOST, API_PORT
from scythe_transcribe.frontend_activity import FrontendActivity

if TYPE_CHECKING:
    import uvicorn

FRONTEND_IDLE_UNLOAD_SECONDS = 5 * 60


class _ReusableThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Small wake server that can rebind the app port quickly."""

    allow_reuse_address = True
    daemon_threads = True


def _make_wake_handler(wake: Callable[[], None]) -> type[http.server.BaseHTTPRequestHandler]:
    """Build a request handler that wakes the full backend and asks the browser to retry."""

    class WakeHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self._wake_and_retry()

        def do_HEAD(self) -> None:  # noqa: N802
            self._wake_and_retry(head_only=True)

        def do_POST(self) -> None:  # noqa: N802
            self._wake_and_retry()

        def do_PUT(self) -> None:  # noqa: N802
            self._wake_and_retry()

        def do_DELETE(self) -> None:  # noqa: N802
            self._wake_and_retry()

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _wake_and_retry(self, *, head_only: bool = False) -> None:
            wake()
            body = (
                b"<!doctype html><title>Starting Scythe-Transcribe</title>"
                b"<meta http-equiv='refresh' content='1'>"
                b"<p>Starting Scythe-Transcribe...</p>"
            )
            self.send_response(503)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Retry-After", "1")
            self.end_headers()
            if not head_only:
                with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                    self.wfile.write(body)

    return WakeHandler


class LocalHttpServer:
    """Runs Uvicorn when awake and a tiny HTTP wake listener when asleep."""

    def __init__(self) -> None:
        """Initialize with no running server."""
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._wake_server: _ReusableThreadingHTTPServer | None = None
        self._wake_thread: threading.Thread | None = None
        self._monitor_thread: threading.Thread | None = None
        self._enabled = True
        self._lock = threading.RLock()
        self._waking = False
        self._activity = FrontendActivity()

    @property
    def is_running(self) -> bool:
        """Whether the Uvicorn server thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def is_wake_listening(self) -> bool:
        """Whether the lightweight wake listener owns the local port."""
        return self._wake_thread is not None and self._wake_thread.is_alive()

    def start(self) -> None:
        """Start Uvicorn if not already running."""
        with self._lock:
            if not self._enabled or self.is_running:
                return
            self._stop_wake_listener_locked()

            import uvicorn

            from scythe_transcribe.frontend_build import ensure_frontend_built
            from scythe_transcribe.web_app import create_app

            ensure_frontend_built()
            self._activity.reset_idle_clock()
            app = create_app(frontend_activity=self._activity)
            config = uvicorn.Config(
                app,
                host=API_HOST,
                port=API_PORT,
                log_level="warning",
            )
            self._server = uvicorn.Server(config)
            self._thread = threading.Thread(
                target=self._server.run,
                daemon=True,
                name="scythe-uvicorn",
            )
            self._thread.start()
            self._wait_until_started_locked()
            self._ensure_monitor_locked()

    def start_wake_listener(self) -> None:
        """Listen with a lightweight server that wakes Uvicorn on the next request."""
        with self._lock:
            if not self._enabled or self.is_running or self.is_wake_listening:
                return
            handler = _make_wake_handler(self.wake)
            self._wake_server = _ReusableThreadingHTTPServer((API_HOST, API_PORT), handler)
            self._wake_thread = threading.Thread(
                target=self._wake_server.serve_forever,
                daemon=True,
                name="scythe-http-wake",
            )
            self._wake_thread.start()

    def wake(self) -> None:
        """Wake the full backend from a separate thread."""
        with self._lock:
            if self._waking or self.is_running or not self._enabled:
                return
            self._waking = True
        threading.Thread(target=self._wake_worker, daemon=True, name="scythe-wake-worker").start()

    def disable(self) -> None:
        """Stop both real and wake servers until explicitly enabled."""
        with self._lock:
            self._enabled = False
            self._stop_locked(start_wake=False)
            self._stop_wake_listener_locked()

    def enable_sleeping(self) -> None:
        """Enable the local port in sleeping mode."""
        with self._lock:
            self._enabled = True
        self.start_wake_listener()

    def stop(self, *, start_wake: bool = True) -> None:
        """Signal Uvicorn shutdown and optionally replace it with the wake listener."""
        with self._lock:
            self._stop_locked(start_wake=start_wake)

    def _wake_worker(self) -> None:
        try:
            self.start()
        finally:
            with self._lock:
                self._waking = False

    def _stop_locked(self, *, start_wake: bool) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=15.0)
        self._server = None
        self._thread = None
        if start_wake and self._enabled:
            self.start_wake_listener()

    def _stop_wake_listener_locked(self) -> None:
        wake_server = self._wake_server
        wake_thread = self._wake_thread
        self._wake_server = None
        self._wake_thread = None
        if wake_server is not None:
            wake_server.shutdown()
            wake_server.server_close()
        if wake_thread is not None and wake_thread is not threading.current_thread():
            wake_thread.join(timeout=2.0)

    def _wait_until_started_locked(self) -> None:
        deadline = time.monotonic() + 3.0
        while self._server is not None and not self._server.started:
            if not self._thread or not self._thread.is_alive() or time.monotonic() >= deadline:
                return
            time.sleep(0.025)

    def _ensure_monitor_locked(self) -> None:
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            return
        self._monitor_thread = threading.Thread(
            target=self._monitor_idle,
            daemon=True,
            name="scythe-idle-unload",
        )
        self._monitor_thread.start()

    def _monitor_idle(self) -> None:
        while True:
            time.sleep(10.0)
            with self._lock:
                if not self._enabled or not self.is_running:
                    return
                idle_for = self._activity.seconds_since_last_seen()
                if self._activity.has_active_sessions() or idle_for < FRONTEND_IDLE_UNLOAD_SECONDS:
                    continue
                self._stop_locked(start_wake=True)
                return


def run_foreground() -> None:
    """Block the current thread with Uvicorn (development / SCYTHE_SERVER_ONLY)."""
    import uvicorn

    from scythe_transcribe.frontend_build import ensure_frontend_built
    from scythe_transcribe.hotkey_service import start_hotkey_listener
    from scythe_transcribe.web_app import create_app

    ensure_frontend_built()
    start_hotkey_listener()
    app = create_app()
    uvicorn.run(app, host=API_HOST, port=API_PORT, log_level="info")
