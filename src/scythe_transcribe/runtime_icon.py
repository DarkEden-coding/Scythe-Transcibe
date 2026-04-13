"""Shared runtime icon state for the tray and hotkey worker."""

from __future__ import annotations

import sys
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from PIL import Image

IconState = Literal["idle", "recording", "processing"]
IconOverride = IconState | None

_PACKAGE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _PACKAGE_DIR.parents[1]
_ICON_FILENAMES: dict[IconState, str] = {
    "idle": "icon-blue.webp",
    "recording": "icon-red.webp",
    "processing": "icon-yellow.webp",
}

_LOCK = threading.RLock()
_BASE_STATE: IconState = "idle"
_OVERRIDE_STATE: IconOverride = None
_TRAY_ICON: Any | None = None


def resolve_icon_path(name: str) -> Path:
    """Resolve an icon path in source checkout or bundled app data."""
    bundled = _PACKAGE_DIR / name
    if bundled.is_file():
        return bundled
    fallback = _REPO_ROOT / name
    if fallback.is_file():
        return fallback
    return bundled


@lru_cache(maxsize=3)
def _load_image(state: IconState) -> Image.Image:
    """Load and cache the tray image for a runtime state."""
    path = resolve_icon_path(_ICON_FILENAMES[state])
    with Image.open(path) as image:
        return image.convert("RGBA")


def get_icon_state() -> IconState:
    """Return the current displayed icon state."""
    with _LOCK:
        return _effective_state_locked()


def get_icon_status() -> dict[str, IconState | IconOverride]:
    """Return base, manual override, and displayed OS icon state."""
    with _LOCK:
        return {
            "base_state": _BASE_STATE,
            "override_state": _OVERRIDE_STATE,
            "display_state": _effective_state_locked(),
        }


def attach_tray_icon(icon: Any) -> None:
    """Bind the live tray icon so future state changes update its image."""
    global _TRAY_ICON

    with _LOCK:
        _TRAY_ICON = icon
        state = _effective_state_locked()
    _apply_icon_image(icon, state)


def _set_icon_image(icon: Any, state: IconState) -> None:
    icon.icon = _load_image(state)


def _apply_icon_image(icon: Any, state: IconState) -> None:
    """Swap tray/menu-bar image, using the AppKit run loop on macOS."""
    if sys.platform == "darwin":
        try:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(_set_icon_image, icon, state)
            return
        except Exception:
            _set_icon_image(icon, state)
            return
    _set_icon_image(icon, state)


def _effective_state_locked() -> IconState:
    return _OVERRIDE_STATE or _BASE_STATE


def _next_icon_state(state: IconState) -> IconState:
    if state == "idle":
        return "recording"
    if state == "recording":
        return "processing"
    return "idle"


def set_icon_state(state: IconState) -> None:
    """Update the backend-requested icon state and, unless overridden, the OS icon."""
    global _BASE_STATE
    with _LOCK:
        old_effective = _effective_state_locked()
        if state == _BASE_STATE:
            return
        _BASE_STATE = state
        icon = _TRAY_ICON
        new_effective = _effective_state_locked()
        if icon is None or new_effective == old_effective:
            return
    _apply_icon_image(icon, new_effective)


def set_icon_override(state: IconOverride) -> dict[str, IconState | IconOverride]:
    """Set or clear a manual OS icon override and return current icon status."""
    global _OVERRIDE_STATE
    with _LOCK:
        old_effective = _effective_state_locked()
        _OVERRIDE_STATE = state
        new_effective = _effective_state_locked()
        icon = _TRAY_ICON
    if icon is not None and new_effective != old_effective:
        _apply_icon_image(icon, new_effective)
    return get_icon_status()


def cycle_icon_override() -> dict[str, IconState | IconOverride]:
    """Cycle manual OS icon override, returning to follow-backend when it matches base."""
    global _OVERRIDE_STATE
    with _LOCK:
        current = _OVERRIDE_STATE or _BASE_STATE
        next_state = _next_icon_state(current)
        _OVERRIDE_STATE = None if next_state == _BASE_STATE else next_state
        new_effective = _effective_state_locked()
        icon = _TRAY_ICON
    if icon is not None:
        _apply_icon_image(icon, new_effective)
    return get_icon_status()
