"""System tray (Windows) / menu bar (macOS) control for the local web app."""

from __future__ import annotations

import webbrowser
from typing import Any

import pystray
from PIL import Image, ImageDraw

from scythe_transcribe.config import PUBLIC_BASE_URL
from scythe_transcribe.hotkey_service import start_hotkey_listener
from scythe_transcribe.http_server import LocalHttpServer


def _make_tray_image(size: int = 64) -> Image.Image:
    """Build a simple circular icon (Material-ish indigo)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = 4
    draw.ellipse(
        (margin, margin, size - margin, size - margin),
        fill=(92, 107, 192, 255),
        outline=(180, 190, 240, 255),
        width=2,
    )
    return img


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

    image = _make_tray_image()
    icon = pystray.Icon(
        "scythe-transcribe",
        image,
        "Scythe-Transcribe",
        rebuild_menu(),
    )
    icon_holder["icon"] = icon
    icon.run()
