"""API key file storage, optional .env, and JSON preferences."""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from platformdirs import user_config_dir

from scythe_transcribe.models import AppPreferences

_APP_NAME = "Scythe-Transcribe"

_MAX_TRANSCRIPTION_HISTORY = 5000

_history_lock = threading.Lock()
_dotenv_lock = threading.Lock()
_dotenv_loaded = False
_diagnostics_logging_configured = False


def _ensure_dotenv_loaded() -> None:
    """Load optional .env values only if an environment fallback is needed."""
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    with _dotenv_lock:
        if _dotenv_loaded:
            return
        from dotenv import load_dotenv

        load_dotenv()
        _dotenv_loaded = True


def _config_dir() -> Path:
    """Return user config directory, created if needed."""
    base = Path(user_config_dir(_APP_NAME, appauthor=False))
    base.mkdir(parents=True, exist_ok=True)
    return base


def diagnostics_log_path() -> Path:
    """Return the path to the diagnostics log file."""
    return _config_dir() / "scythe-transcribe.log"


def configure_diagnostics_file_logging() -> Path:
    """Attach a file handler so windowed (no-console) builds can be diagnosed.

    Idempotent: safe to call multiple times.

    Returns:
        Absolute path to the log file.
    """
    global _diagnostics_logging_configured
    path = diagnostics_log_path()
    if _diagnostics_logging_configured:
        return path
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"),
    )
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    _diagnostics_logging_configured = True
    return path


def _prefs_path() -> Path:
    """Return path to persisted preferences JSON."""
    return _config_dir() / "preferences.json"


def _transcription_history_path() -> Path:
    """Return path to persisted transcription history JSON."""
    return _config_dir() / "transcription_history.json"


def _api_keys_path() -> Path:
    """Return path to persisted API keys JSON."""
    return _config_dir() / "api_keys.json"


def load_preferences() -> AppPreferences:
    """Load preferences from disk, or defaults if missing or invalid."""
    path = _prefs_path()
    if not path.is_file():
        return AppPreferences()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return AppPreferences()
        return AppPreferences.from_json({k: v for k, v in raw.items() if isinstance(k, str)})
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return AppPreferences()


def save_preferences(prefs: AppPreferences) -> None:
    """Persist preferences to disk."""
    path = _prefs_path()
    path.write_text(json.dumps(prefs.to_json(), indent=2), encoding="utf-8")


def _load_api_keys_file() -> dict[str, str]:
    """Load raw key strings from disk."""
    path = _api_keys_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        out: dict[str, str] = {}
        for k, v in raw.items():
            if isinstance(k, str) and isinstance(v, str):
                out[k] = v
        return out
    except (OSError, json.JSONDecodeError):
        return {}


def _save_api_keys_file(keys: dict[str, str]) -> None:
    """Write API keys JSON."""
    path = _api_keys_path()
    path.write_text(json.dumps(keys, indent=2), encoding="utf-8")


def get_groq_api_key() -> str:
    """Return Groq API key from file, environment, or empty string."""
    data = _load_api_keys_file()
    key = (data.get("groq") or "").strip()
    if key:
        return key
    _ensure_dotenv_loaded()
    return os.environ.get("GROQ_API_KEY", "").strip()


def get_openrouter_api_key() -> str:
    """Return OpenRouter API key from file, environment, or empty string."""
    data = _load_api_keys_file()
    key = (data.get("openrouter") or "").strip()
    if key:
        return key
    _ensure_dotenv_loaded()
    return os.environ.get("OPENROUTER_API_KEY", "").strip()


def set_groq_api_key(key: str) -> None:
    """Persist Groq API key to the keys file."""
    data = _load_api_keys_file()
    data["groq"] = key
    _save_api_keys_file(data)


def set_openrouter_api_key(key: str) -> None:
    """Persist OpenRouter API key to the keys file."""
    data = _load_api_keys_file()
    data["openrouter"] = key
    _save_api_keys_file(data)


def openrouter_models_cache_path() -> Path:
    """Path for cached OpenRouter model list JSON."""
    return _config_dir() / "openrouter_models_cache.json"


def load_json_cache(path: Path) -> Any | None:
    """Load JSON from path or return None on failure."""
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return None


def save_json_cache(path: Path, data: Any) -> None:
    """Write JSON to path."""
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _load_transcription_history_raw(path: Path) -> list[dict[str, Any]]:
    """Read history list from disk without locking (internal)."""
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            out.append(item)
    return out


def load_transcription_history() -> list[dict[str, Any]]:
    """Return all persisted transcription entries, newest first."""
    path = _transcription_history_path()
    with _history_lock:
        return list(_load_transcription_history_raw(path))


def append_transcription_history(entry: dict[str, Any]) -> None:
    """Prepend one entry and trim to the configured maximum."""
    path = _transcription_history_path()
    with _history_lock:
        entries = _load_transcription_history_raw(path)
        entries.insert(0, dict(entry))
        if len(entries) > _MAX_TRANSCRIPTION_HISTORY:
            entries = entries[:_MAX_TRANSCRIPTION_HISTORY]
        path.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def patch_transcription_history_entry(entry_id: str, patch: dict[str, Any]) -> bool:
    """Merge ``patch`` into the newest matching entry by ``id`` (e.g. hotkey timings).

    Args:
        entry_id: ``TranscribeResponse.id`` for the row to update.
        patch: Keys to merge into that entry.

    Returns:
        True if an entry was updated.
    """
    if not entry_id or not patch:
        return False
    path = _transcription_history_path()
    with _history_lock:
        entries = _load_transcription_history_raw(path)
        for i, row in enumerate(entries):
            if isinstance(row, dict) and str(row.get("id", "")) == entry_id:
                merged = dict(row)
                merged.update(patch)
                entries[i] = merged
                path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
                return True
    return False
