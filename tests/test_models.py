"""Tests for preference serialization."""

from __future__ import annotations

import json

from scythe_transcribe.models import AppPreferences, ChatProvider, TranscriptionProvider
from scythe_transcribe.prompts import OPENROUTER_TRANSCRIPTION_INSTRUCTION


def test_preferences_json_roundtrip() -> None:
    """Preferences survive JSON encode/decode with new fields."""
    p = AppPreferences(
        transcription_provider=TranscriptionProvider.OPENROUTER.value,
        transcription_model_groq="whisper-large-v3",
        transcription_model_openrouter="x/y",
        postprocess_enabled=True,
        postprocess_prompt="Do the thing.",
        postprocess_provider=ChatProvider.GROQ.value,
        postprocess_model="llama-3.3-70b-versatile",
        postprocess_groq_reasoning_effort="medium",
        postprocess_openrouter_reasoning_effort="",
        openrouter_models_cache_hint="",
        keyword_replacement_spec="teh -> the",
        openrouter_transcription_instruction="Listen carefully.",
        hotkey_toggle_recording="ctrl+shift+r",
    )
    raw = json.dumps(p.to_json())
    back = AppPreferences.from_json(json.loads(raw))
    assert back == p


def test_preferences_defaults_for_missing_keys() -> None:
    """Older preference files without new keys still load."""
    minimal = {"postprocess_enabled": False}
    p = AppPreferences.from_json(minimal)
    assert p.keyword_replacement_spec == ""
    assert p.openrouter_transcription_instruction == OPENROUTER_TRANSCRIPTION_INSTRUCTION
