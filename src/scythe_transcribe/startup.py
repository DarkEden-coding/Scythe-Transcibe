from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

_PLIST_LABEL = "com.scythe-transcribe.app"
_PLIST_NAME = f"{_PLIST_LABEL}.plist"


def _launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _plist_path() -> Path:
    return _launch_agents_dir() / _PLIST_NAME


def _executable() -> str:
    """Return the path to the running Python executable (or frozen binary)."""
    return sys.executable


def _script_args() -> list[str]:
    """Arguments to pass after the executable when relaunching."""
    if getattr(sys, "frozen", False):
        # PyInstaller / cx_Freeze bundle – the executable IS the app.
        return []
    # Running as a module via `uv run` / plain Python.
    return ["-m", "scythe_transcribe"]


def _macos_app_bundle_path(executable: str | None = None) -> Path | None:
    """Return the containing .app bundle when running from one."""
    current = Path(executable or sys.executable).resolve()
    for path in (current, *current.parents):
        if path.suffix == ".app" and (path / "Contents" / "Info.plist").is_file():
            return path
    return None


def _macos_program_arguments() -> list[str]:
    """Return LaunchAgent ProgramArguments for the current macOS app."""
    if getattr(sys, "frozen", False):
        app_bundle = _macos_app_bundle_path()
        if app_bundle is not None:
            # Launch the bundle, not Contents/MacOS/scythe-transcribe directly.
            # Accessibility/TCC grants are attached to the app identity users see
            # in System Settings.
            return ["/usr/bin/open", "-g", str(app_bundle)]
    return [_executable(), *_script_args()]


def _plist_xml(argv: list[str]) -> str:
    payload = {
        "Label": _PLIST_LABEL,
        "ProgramArguments": argv,
        "RunAtLoad": True,
        "KeepAlive": False,
        "StandardOutPath": str(Path.home() / "Library" / "Logs" / "scythe-transcribe.log"),
        "StandardErrorPath": str(
            Path.home() / "Library" / "Logs" / "scythe-transcribe-error.log"
        ),
    }
    return plistlib.dumps(payload).decode("utf-8")


# ---------------------------------------------------------------------------
# macOS
# ---------------------------------------------------------------------------


def _macos_is_enabled() -> bool:
    return _plist_path().is_file()


def _macos_enable() -> None:
    agents_dir = _launch_agents_dir()
    agents_dir.mkdir(parents=True, exist_ok=True)
    _plist_path().write_text(_plist_xml(_macos_program_arguments()), encoding="utf-8")
    try:
        subprocess.run(
            ["launchctl", "load", str(_plist_path())],
            check=False,
            capture_output=True,
        )
    except FileNotFoundError:
        pass  # launchctl not found in test/CI environments


def _macos_disable() -> None:
    plist = _plist_path()
    if plist.is_file():
        try:
            subprocess.run(
                ["launchctl", "unload", str(plist)],
                check=False,
                capture_output=True,
            )
        except FileNotFoundError:
            pass
        plist.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------


def _windows_reg_key() -> str:
    return "Scythe-Transcribe"


def _windows_is_enabled() -> bool:
    try:
        import winreg  # type: ignore[import]

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_READ,
        )
        try:
            winreg.QueryValueEx(key, _windows_reg_key())
            return True
        except FileNotFoundError:
            return False
        finally:
            winreg.CloseKey(key)
    except Exception:
        return False


def _windows_enable() -> None:
    try:
        import winreg  # type: ignore[import]

        exe = _executable()
        args = _script_args()
        cmd = " ".join([f'"{exe}"'] + [f'"{a}"' if " " in a else a for a in args])
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        )
        try:
            winreg.SetValueEx(key, _windows_reg_key(), 0, winreg.REG_SZ, cmd)
        finally:
            winreg.CloseKey(key)
    except Exception:
        pass


def _windows_disable() -> None:
    try:
        import winreg  # type: ignore[import]

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        )
        try:
            winreg.DeleteValue(key, _windows_reg_key())
        except FileNotFoundError:
            pass
        finally:
            winreg.CloseKey(key)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_startup_enabled() -> bool:
    """Return True if the app is registered to run at login."""
    if sys.platform == "darwin":
        return _macos_is_enabled()
    if sys.platform == "win32":
        return _windows_is_enabled()
    return False


def set_startup_enabled(enabled: bool) -> None:
    """Register or unregister the app to run at login."""
    if sys.platform == "darwin":
        if enabled:
            _macos_enable()
        else:
            _macos_disable()
    elif sys.platform == "win32":
        if enabled:
            _windows_enable()
        else:
            _windows_disable()
