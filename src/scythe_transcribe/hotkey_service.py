"""Global hold-to-dictate hotkey: record, transcribe, optional post-process, paste."""

from __future__ import annotations

import io
import sys
import threading
import time
import wave
from collections import defaultdict

import numpy as np
import pyperclip
import sounddevice as sd
from pynput.keyboard import Controller, Key, KeyCode, Listener

from scythe_transcribe.settings_store import load_preferences, patch_transcription_history_entry
from scythe_transcribe.transcribe_pipeline import (
    text_to_paste,
    transcribe_job_from_preferences,
    transcribe_wav_bytes,
)

_started = threading.Lock()
_listener_started = False

_MODIFIER_TOKENS = frozenset({"ctrl", "alt", "shift", "meta"})

_KEY_TO_TOKEN: dict[Key | KeyCode, str] = {
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
_SPACE_KEY_VK = getattr(Key.space.value, "vk", None)
if _SPACE_KEY_VK is not None:
    _SPACE_VKS.add(_SPACE_KEY_VK)

# VK code to f-key token, built from the platform Key enum at import time.
# On macOS, pynput may return a KeyCode (with only a vk, no char) for function
# keys instead of the Key.fN enum member when the OS intercepts the key for a
# media/special action.  Without this table the KeyCode branch returns None and
# the hotkey is silently ignored.
_VK_TO_FKEY_TOKEN: dict[int, str] = {}
for _fk in Key:
    _nm = _fk.name  # "f1", "f2", ..., "f20"
    if _nm.startswith("f") and _nm[1:].isdigit():
        _fk_vk = getattr(_fk.value, "vk", None)
        if _fk_vk is not None:
            _VK_TO_FKEY_TOKEN[_fk_vk] = _nm


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


def _key_event_token(key: Key | KeyCode | None) -> str | None:
    """Map pynput key to the same token space as the UI (ctrl, space, a, …)."""
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


def _float32_mono_to_wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    """Encode mono float32 [-1,1] PCM as WAV bytes (16-bit)."""
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
    combo_parts: list[str] = []
    counts: dict[str, int] = defaultdict(int)
    prev_active = False
    stream_holder: list[sd.InputStream | None] = [None]
    chunks_holder: list[list[np.ndarray]] = [[]]
    transcribing = threading.Event()
    suppress_until: list[float] = [0.0]

    def is_suppressed() -> bool:
        return time.monotonic() < suppress_until[0]

    def combo_requirements_met() -> bool:
        if not combo_parts:
            return False
        return all(counts.get(p, 0) >= 1 for p in combo_parts)

    def stop_stream() -> np.ndarray:
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
        chunks_holder[0] = []

        def callback(indata: np.ndarray, _frames: int, _t: object, status: object) -> None:
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
        try:
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

    def on_press(key: Key | KeyCode | None) -> None:
        nonlocal prev_active
        if is_suppressed():
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
            except Exception:
                prev_active = False
                return
        prev_active = active

    def on_release(key: Key | KeyCode | None) -> None:
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
                threading.Thread(target=worker_transcribe, args=(wav_bytes,), daemon=True).start()
        prev_active = active

    with Listener(on_press=on_press, on_release=on_release) as listener:
        listener.join()


def start_hotkey_listener() -> None:
    """Start a background thread that listens for the configured hold-to-talk hotkey.

    Safe to call multiple times; only the first call has an effect.
    """
    global _listener_started
    with _started:
        if _listener_started:
            return
        t = threading.Thread(target=_run_hotkey_loop, daemon=True, name="scythe-hotkey")
        t.start()
        _listener_started = True
