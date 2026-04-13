"""System tray (Windows) / menu bar (macOS) control for the local web app."""

from __future__ import annotations

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


def run_tray() -> None:
    """Run the tray icon until the user chooses Shutdown."""
    server = LocalHttpServer()
    start_hotkey_listener()
    server.start_wake_listener()

    state: dict[str, Any] = {"enabled": True}
    icon_holder: dict[str, pystray.Icon | None] = {"icon": None}

    def rebuild_menu() -> pystray.Menu:
        """Build menu with Enable/Disable visibility."""
        enabled = bool(state["enabled"])

        def open_web(_: pystray.Icon, __: Any) -> None:
            if not enabled:
                return
            server.start()
            webbrowser.open(PUBLIC_BASE_URL)

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
    icon.run()
