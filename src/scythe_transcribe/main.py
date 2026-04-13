"""Application entry: tray agent or foreground HTTP server."""

from __future__ import annotations

import os

from scythe_transcribe.http_server import run_foreground


def run_app() -> None:
    """Start the system tray / menu bar and the local API server.

    Set ``SCYTHE_SERVER_ONLY`` to ``1``, ``true``, or ``yes`` to run only the
    blocking HTTP server (no tray).

    Set ``SCYTHE_TRAY`` to ``0``, ``false``, or ``no`` to run the server in the
    foreground without the tray (same as :func:`run_server_foreground`).
    """
    if os.environ.get("SCYTHE_SERVER_ONLY", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        run_server_foreground()
        return

    use_tray = os.environ.get("SCYTHE_TRAY", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    if use_tray:
        from scythe_transcribe.tray_app import run_tray

        run_tray()
    else:
        run_server_foreground()


def run_server_foreground() -> None:
    """Run Uvicorn in the current thread (development / CI)."""
    run_foreground()
