"""Application entry: tray agent or foreground HTTP server."""

from __future__ import annotations

import os
import sys

from scythe_transcribe.http_server import run_foreground

# Module-level file handle kept open for the lifetime of the process so the
# OS lock is held until we exit (even on crash).
_lock_fh: "object | None" = None


def _acquire_single_instance_lock() -> bool:
    """Return True if this process is the sole running instance.

    Uses an exclusive non-blocking flock on macOS/Linux and a CreateFile
    share-denial on Windows.  The lock is held for the process lifetime via
    a module-level file handle so it is released automatically on exit.
    """
    global _lock_fh  # noqa: PLW0603

    from scythe_transcribe.settings_store import _config_dir  # type: ignore[attr-defined]

    lock_path = _config_dir() / "instance.lock"

    if sys.platform == "win32":
        import msvcrt

        try:
            fh = lock_path.open("w")
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            _lock_fh = fh
            return True
        except OSError:
            return False
    else:
        import fcntl

        try:
            fh = lock_path.open("w")
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _lock_fh = fh
            return True
        except OSError:
            return False


def run_app() -> None:
    """Start the system tray / menu bar and the local API server.

    Set ``SCYTHE_SERVER_ONLY`` to ``1``, ``true``, or ``yes`` to run only the
    blocking HTTP server (no tray).

    Set ``SCYTHE_TRAY`` to ``0``, ``false``, or ``no`` to run the server in the
    foreground without the tray (same as :func:`run_server_foreground`).
    """
    if not _acquire_single_instance_lock():
        # Another instance is already running (e.g. launched manually while the
        # LaunchAgent / startup entry also fired).  Exit silently.
        sys.exit(0)

    try:
        import setproctitle
        setproctitle.setproctitle("scythe-transcribe")
    except Exception:
        pass

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
