"""System tray (Windows) / menu bar (macOS) control for the local web app."""

from __future__ import annotations

import logging
import sys
import threading
import time
import webbrowser
from typing import Any

import pystray
from PIL import Image

from scythe_transcribe.config import PUBLIC_BASE_URL
from scythe_transcribe.hotkey_service import start_hotkey_listener
from scythe_transcribe.http_server import LocalHttpServer
from scythe_transcribe.runtime_icon import attach_tray_icon, resolve_icon_path


def _load_icon_image(name: str) -> Image.Image:
    """Load a tray icon from the project-root assets."""
    with Image.open(resolve_icon_path(name)) as image:
        return image.convert("RGBA")


def _open_default_browser(url: str) -> None:
    """Open ``url`` in the user's default browser.

    On Windows, :mod:`webbrowser` is unreliable for frozen apps. We use ``ShellExecuteW``
    with correct 64-bit return typing, then ``explorer.exe``, ``os.startfile``, and
    ``cmd /c start`` (including ``shell=True`` for windowed / PyInstaller exes).

    Args:
        url: HTTP(S) URL to open.
    """
    if sys.platform == "win32":
        import ctypes
        import os
        import subprocess
        from ctypes import wintypes
        from pathlib import Path

        # PyInstaller's bootloader calls SetDllDirectoryW to the bundle dir so our DLLs
        # load; that inheritance breaks ShellExecute/subprocess when starting the default
        # browser or cmd. Reset for this launch, then restore (see PyInstaller Windows notes).
        _bundle_dir = getattr(sys, "_MEIPASS", None)
        _fix_dll_search = bool(getattr(sys, "frozen", False) or _bundle_dir)
        if _fix_dll_search:
            ctypes.windll.kernel32.SetDllDirectoryW(None)

        def _shell_execute_open() -> bool:
            """Return True if ShellExecute reports success (see MSDN ShellExecuteW)."""
            shell32 = ctypes.windll.shell32
            shell32.ShellExecuteW.argtypes = [
                wintypes.HWND,
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                wintypes.LPCWSTR,
                ctypes.c_int,
            ]
            shell32.ShellExecuteW.restype = ctypes.c_void_p
            rc = shell32.ShellExecuteW(None, "open", url, None, None, 1)
            if not rc:
                return False
            val = ctypes.c_void_p(rc).value
            if val is None:
                return False
            # Error codes are 0..32; a successful HINSTANCE is always greater.
            return int(val) > 32

        try:
            if _shell_execute_open():
                return

            windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows"
            explorer = Path(windir) / "explorer.exe"
            if explorer.is_file():
                try:
                    subprocess.Popen([str(explorer), url])
                    return
                except OSError:
                    pass

            try:
                os.startfile(url)
            except OSError:
                pass
            else:
                return

            try:
                subprocess.run(f'cmd /c start "" "{url}"', shell=True, check=False)
            except OSError:
                subprocess.run(["cmd", "/c", "start", "", url], check=False)
        finally:
            if _fix_dll_search and _bundle_dir:
                ctypes.windll.kernel32.SetDllDirectoryW(_bundle_dir)

        return

    webbrowser.open(url)


def run_tray() -> None:
    """Run the tray icon until the user chooses Shutdown."""
    log = logging.getLogger(__name__)
    log.info("run_tray: initializing tray and wake listener")
    server = LocalHttpServer()
    start_hotkey_listener()
    server.start_wake_listener()

    state: dict[str, Any] = {"enabled": True}
    icon_holder: dict[str, pystray.Icon | None] = {"icon": None}

    def rebuild_menu() -> pystray.Menu:
        """Build menu with Enable/Disable visibility."""

        def open_web(_: pystray.Icon, __: Any) -> None:
            log = logging.getLogger(__name__)
            if not state["enabled"]:
                return
            try:
                server.start()
            except Exception:
                log.exception("open_web: server.start failed")
                raise
            if not server.is_running:
                log.error("open_web: uvicorn thread not running after server.start()")
            bundled = getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS")
            try:
                if sys.platform == "win32":
                    if bundled:
                        # Windowed PyInstaller builds: opening from a daemon worker thread often
                        # does nothing (no visible error). Run on the tray message-loop thread.
                        time.sleep(0.05)
                        _open_default_browser(PUBLIC_BASE_URL)
                    else:

                        def _open_after_menu() -> None:
                            # pystray invokes the handler during TrackPopupMenuEx teardown; defer
                            # slightly so ShellExecute works reliably in dev runs.
                            time.sleep(0.1)
                            _open_default_browser(PUBLIC_BASE_URL)

                        threading.Thread(
                            target=_open_after_menu,
                            daemon=True,
                            name="scythe-open-settings",
                        ).start()
                else:
                    _open_default_browser(PUBLIC_BASE_URL)
            except Exception:
                log.exception("open_web: browser launch failed")
                raise

        def toggle_server(_: pystray.Icon, __: Any) -> None:
            if state["enabled"]:
                server.disable()
                state["enabled"] = False
            else:
                server.enable_sleeping()
                state["enabled"] = True
            ic = icon_holder["icon"]
            if ic is not None:
                ic.menu = rebuild_menu()
                ic.update_menu()

        def shutdown(_: pystray.Icon, __: Any) -> None:
            server.disable()
            ic = icon_holder["icon"]
            if ic is not None:
                ic.stop()

        return pystray.Menu(
            pystray.MenuItem(
                "Open settings",
                open_web,
                enabled=lambda _: bool(state["enabled"]),
                default=True,
            ),
            pystray.MenuItem(
                "Disable server",
                toggle_server,
                visible=lambda _: bool(state["enabled"]),
            ),
            pystray.MenuItem(
                "Enable server",
                toggle_server,
                visible=lambda _: not bool(state["enabled"]),
            ),
            pystray.MenuItem("Shutdown", shutdown),
        )

    image = _load_icon_image("icon-blue.webp")
    icon = pystray.Icon(
        "scythe-transcribe",
        image,
        "Scythe-Transcribe",
        rebuild_menu(),
    )
    icon_holder["icon"] = icon
    attach_tray_icon(icon)
    try:
        icon.run()
    except Exception:
        log.exception("run_tray: icon.run exited with an error")
        raise
