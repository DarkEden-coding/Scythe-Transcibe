"""Global hold-to-dictate hotkey: record, transcribe, optional post-process, paste."""

from __future__ import annotations

import io
import os
import sys
import threading
import time
import wave
from collections import defaultdict
from pathlib import Path
from typing import Any

from scythe_transcribe.runtime_icon import set_icon_state
from scythe_transcribe.settings_store import load_preferences, patch_transcription_history_entry

_started = threading.Lock()
_listener_manager_started = False
_keyboard_init_lock = threading.Lock()
_keyboard_api: dict[str, Any] = {}
_status_lock = threading.Lock()
_listener_status: dict[str, Any] = {
    "state": "stopped",
    "error": None,
}

_HOTKEY_RETRY_SECONDS = 2.0

_MODIFIER_TOKENS = frozenset({"ctrl", "alt", "shift", "meta"})

_KEY_TO_TOKEN: dict[object, str] = {}

_PART_ALIASES = {
    "control": "ctrl",
    "option": "alt",
    "cmd": "meta",
    "command": "meta",
    "win": "meta",
    "super": "meta",
    "os": "meta",
}

_SPACE_VKS = {32}

# VK code to f-key token, built from the platform Key enum at import time.
# On macOS, pynput may return a KeyCode (with only a vk, no char) for function
# keys instead of the Key.fN enum member when the OS intercepts the key for a
# media/special action.  Without this table the KeyCode branch returns None and
# the hotkey is silently ignored.
_VK_TO_FKEY_TOKEN: dict[int, str] = {}


def current_app_identity() -> dict[str, Any]:
    """Return paths useful for matching this process to macOS privacy settings."""
    executable = Path(sys.executable).resolve()
    app_bundle = None
    for path in (executable, *executable.parents):
        if path.suffix == ".app" and (path / "Contents" / "Info.plist").is_file():
            app_bundle = str(path)
            break
    return {
        "pid": os.getpid(),
        "executable": str(executable),
        "app_bundle": app_bundle,
    }


def is_accessibility_trusted() -> bool:
    """Return whether this process can receive macOS Accessibility input events."""
    if sys.platform != "darwin":
        return True
    try:
        import ctypes
        import ctypes.util

        lib_path = ctypes.util.find_library("ApplicationServices")
        if not lib_path:
            return True
        lib = ctypes.cdll.LoadLibrary(lib_path)
        lib.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(lib.AXIsProcessTrusted())
    except Exception:
        return True


def request_accessibility_trust_prompt() -> bool:
    """Ask macOS to prompt for Accessibility trust for this process."""
    if sys.platform != "darwin":
        return True
    try:
        from Quartz import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt

        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True}))
    except Exception:
        return is_accessibility_trusted()


def get_hotkey_listener_status() -> dict[str, Any]:
    """Return diagnostic state for the background hotkey listener."""
    with _status_lock:
        status = dict(_listener_status)
    status["accessibility_trusted"] = is_accessibility_trusted()
    return status


def _set_listener_status(state: str, error: str | None = None) -> None:
    with _status_lock:
        _listener_status.update(
            {
                "state": state,
                "error": error,
            }
        )


def _ensure_keyboard_api() -> dict[str, Any]:
    """Import pynput and build keyboard lookup tables on first actual use."""
    if _keyboard_api:
        return _keyboard_api
    with _keyboard_init_lock:
        if _keyboard_api:
            return _keyboard_api
        from pynput.keyboard import Controller, Key, KeyCode, Listener

        _KEY_TO_TOKEN.update(
            {
                Key.ctrl: "ctrl",
                Key.ctrl_l: "ctrl",
                Key.ctrl_r: "ctrl",
                Key.alt: "alt",
                Key.alt_l: "alt",
                Key.alt_r: "alt",
                Key.shift: "shift",
                Key.shift_l: "shift",
                Key.shift_r: "shift",
                Key.cmd: "meta",
                Key.cmd_l: "meta",
                Key.cmd_r: "meta",
                Key.space: "space",
            }
        )
        space_key_vk = getattr(Key.space.value, "vk", None)
        if space_key_vk is not None:
            _SPACE_VKS.add(space_key_vk)
        for fk in Key:
            name = fk.name  # "f1", "f2", ..., "f20"
            if name.startswith("f") and name[1:].isdigit():
                fk_vk = getattr(fk.value, "vk", None)
                if fk_vk is not None:
                    _VK_TO_FKEY_TOKEN[fk_vk] = name
        _keyboard_api.update(
            {
                "Controller": Controller,
                "Key": Key,
                "KeyCode": KeyCode,
                "Listener": Listener,
            }
        )
        return _keyboard_api


def _normalize_combo_part(part: str) -> str:
    """Match frontend hotkey tokens (lowercase, ctrl not control)."""
    p = part.strip().lower()
    return _PART_ALIASES.get(p, p)


def _parse_hotkey_combo(raw: str) -> list[str]:
    """Split stored combo into normalized token list (order ignored for matching)."""
    t = raw.strip()
    if not t:
        return []
    return [_normalize_combo_part(p) for p in t.split("+") if p.strip()]


def _key_event_token(key: object | None) -> str | None:
    """Map pynput key to the same token space as the UI (ctrl, space, a, …)."""
    api = _ensure_keyboard_api()
    Key = api["Key"]
    KeyCode = api["KeyCode"]
    if key is None:
        return None
    if key in _KEY_TO_TOKEN:
        return _KEY_TO_TOKEN[key]
    if isinstance(key, KeyCode):
        vk = getattr(key, "vk", None)
        if vk in _SPACE_VKS:
            return "space"
        if key.char in {" ", "\xa0"}:
            return "space"
        # On macOS, pynput may resolve a function key to a KeyCode whose vk
        # matches a Key.fN entry instead of returning the Key enum member
        # directly.  Check the vk table before falling back to key.char so
        # that private-use Unicode chars produced by macOS for F-keys do not
        # shadow the correct "f1"/"f5"/... token.
        if vk is not None:
            fkey = _VK_TO_FKEY_TOKEN.get(vk)
            if fkey:
                return fkey
        if key.char:
            return key.char.lower()
    if isinstance(key, Key):
        name = key.name
        if isinstance(name, str):
            n = name.lower()
            if n == "space":
                return "space"
            return n
    return None


def _float32_mono_to_wav_bytes(samples: Any, sample_rate: int) -> bytes:
    """Encode mono float32 [-1,1] PCM as WAV bytes (16-bit)."""
    import numpy as np

    if samples.size == 0:
        return b""
    clipped = np.clip(samples.astype(np.float64, copy=False), -1.0, 1.0)
    pcm = (clipped * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def _paste_at_cursor(text: str, suppress_until: list[float]) -> None:
    """Put text on the clipboard and send the platform paste chord."""
    if not text.strip():
        return
    import pyperclip

    api = _ensure_keyboard_api()
    Controller = api["Controller"]
    Key = api["Key"]
    pyperclip.copy(text)
    time.sleep(0.04)
    suppress_until[0] = time.monotonic() + 0.35
    kbd = Controller()
    mod = Key.cmd if sys.platform == "darwin" else Key.ctrl
    kbd.press(mod)
    kbd.press("v")
    kbd.release("v")
    kbd.release(mod)


def _run_hotkey_loop() -> None:
    """Listen for hold-to-talk; on release run transcribe + paste in a worker thread."""
    Listener = _ensure_keyboard_api()["Listener"]

    combo_parts: list[str] = []
    counts: dict[str, int] = defaultdict(int)
    prev_active = False
    stream_holder: list[Any | None] = [None]
    chunks_holder: list[list[Any]] = [[]]
    transcribing = threading.Event()
    suppress_until: list[float] = [0.0]
    set_icon_state("idle")

    def is_suppressed() -> bool:
        return time.monotonic() < suppress_until[0]

    def combo_requirements_met() -> bool:
        if not combo_parts:
            return False
        return all(counts.get(p, 0) >= 1 for p in combo_parts)

    def stop_stream() -> Any:
        import numpy as np

        st = stream_holder[0]
        stream_holder[0] = None
        chunks = chunks_holder[0]
        chunks_holder[0] = []
        if st is not None:
            st.stop()
            st.close()
        if not chunks:
            return np.array([], dtype=np.float32)
        return np.concatenate(chunks, axis=0).reshape(-1)

    def start_stream() -> None:
        import sounddevice as sd

        chunks_holder[0] = []

        def callback(indata: Any, _frames: int, _t: object, status: object) -> None:
            if status:
                pass
            chunks_holder[0].append(indata.copy())

        stream_holder[0] = sd.InputStream(
            samplerate=16_000,
            channels=1,
            dtype="float32",
            callback=callback,
        )
        stream_holder[0].start()

    def worker_transcribe(wav_bytes: bytes) -> None:
        if transcribing.is_set():
            return
        transcribing.set()
        set_icon_state("processing")
        try:
            from scythe_transcribe.transcribe_pipeline import (
                text_to_paste,
                transcribe_job_from_preferences,
                transcribe_wav_bytes,
            )

            prefs = load_preferences()
            job = transcribe_job_from_preferences(prefs)
            result = transcribe_wav_bytes(job, wav_bytes)
            t_after_pipeline = time.perf_counter()
            text = text_to_paste(prefs, result)
            t_before_paste = time.perf_counter()
            _paste_at_cursor(text, suppress_until)
            t_after_paste = time.perf_counter()
            post_api_to_paste_ms = (t_before_paste - t_after_pipeline) * 1000.0
            paste_chord_ms = (t_after_paste - t_before_paste) * 1000.0
            patch_transcription_history_entry(
                result.id,
                {
                    "hotkey_post_api_to_paste_ms": post_api_to_paste_ms,
                    "hotkey_paste_chord_ms": paste_chord_ms,
                },
            )
        except Exception:
            pass
        finally:
            transcribing.clear()
            set_icon_state("idle")

    def on_press(key: object | None) -> None:
        nonlocal prev_active
        if is_suppressed():
            return
        if transcribing.is_set():
            return
        tok = _key_event_token(key)
        if not tok:
            return
        combo_parts[:] = _parse_hotkey_combo(load_preferences().hotkey_toggle_recording)
        if not combo_parts:
            return
        if tok in _MODIFIER_TOKENS:
            counts[tok] = counts.get(tok, 0) + 1
        else:
            counts[tok] = 1

        active = combo_requirements_met()
        if active and not prev_active:
            try:
                start_stream()
                set_icon_state("recording")
            except Exception:
                prev_active = False
                return
        prev_active = active

    def on_release(key: object | None) -> None:
        nonlocal prev_active
        if is_suppressed():
            return
        tok = _key_event_token(key)
        if not tok:
            return
        combo_parts[:] = _parse_hotkey_combo(load_preferences().hotkey_toggle_recording)
        if tok in _MODIFIER_TOKENS:
            if counts.get(tok, 0) > 0:
                counts[tok] -= 1
                if counts[tok] <= 0:
                    del counts[tok]
        else:
            counts.pop(tok, None)

        active = combo_requirements_met()
        if prev_active and not active:
            samples = stop_stream()
            min_samps = int(16_000 * 0.12)
            if samples.size >= min_samps and not transcribing.is_set():
                wav_bytes = _float32_mono_to_wav_bytes(samples, 16_000)
                set_icon_state("processing")
                threading.Thread(target=worker_transcribe, args=(wav_bytes,), daemon=True).start()
            else:
                set_icon_state("idle")
        prev_active = active

    with Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()


def _run_hotkey_manager() -> None:
    """Keep the hotkey listener alive once macOS Accessibility allows it."""
    while True:
        if not is_accessibility_trusted():
            _set_listener_status("waiting_for_accessibility")
            time.sleep(_HOTKEY_RETRY_SECONDS)
            continue
        try:
            _set_listener_status("running")
            _run_hotkey_loop()
            _set_listener_status("stopped")
            return
        except Exception as exc:
            _set_listener_status("error", f"{type(exc).__name__}: {exc}")
            set_icon_state("idle")
            time.sleep(_HOTKEY_RETRY_SECONDS)


def start_hotkey_listener() -> None:
    """Start a background thread that listens for the configured hold-to-talk hotkey.

    Safe to call multiple times; only the first call has an effect.
    """
    global _listener_manager_started
    with _started:
        if _listener_manager_started:
            return
        t = threading.Thread(
            target=_run_hotkey_manager,
            daemon=True,
            name="scythe-hotkey-manager",
        )
        t.start()
        _listener_manager_started = True
