"""Shared runtime icon state for the tray and hotkey worker."""

from __future__ import annotations

import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from PIL import Image

IconState = Literal["idle", "recording", "processing"]

_ROOT = Path(__file__).resolve().parents[2]
_ICON_PATHS: dict[IconState, Path] = {
    "idle": _ROOT / "icon-blue.webp",
    "recording": _ROOT / "icon-red.webp",
    "processing": _ROOT / "icon-yellow.webp",
}

_LOCK = threading.RLock()
_STATE: IconState = "idle"
_TRAY_ICON: Any | None = None


def resolve_icon_path(name: str) -> Path:
    """Resolve an icon path in source checkout or bundled app data."""
    bundled = Path(__file__).resolve().parent / name
    if bundled.is_file():
        return bundled
    return _ROOT / name


@lru_cache(maxsize=3)
def _load_image(state: IconState) -> Image.Image:
    """Load and cache the tray image for a runtime state."""
    path = resolve_icon_path(_ICON_PATHS[state].name)
    with Image.open(path) as image:
        return image.convert("RGBA")


def get_icon_state() -> IconState:
    """Return the current shared icon state."""
    with _LOCK:
        return _STATE


def attach_tray_icon(icon: Any) -> None:
    """Bind the live tray icon so future state changes update its image."""
    global _TRAY_ICON

    with _LOCK:
        _TRAY_ICON = icon
        state = _STATE
    icon.icon = _load_image(state)


def set_icon_state(state: IconState) -> None:
    """Update the shared state and, if possible, swap the tray image."""
    global _STATE

    with _LOCK:
        if state == _STATE:
            icon = _TRAY_ICON
            if icon is None:
                return
        else:
            _STATE = state
            icon = _TRAY_ICON
    if icon is not None:
        icon.icon = _load_image(state)
