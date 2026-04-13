"""Tests for transcription pipeline helpers."""

from __future__ import annotations

from scythe_transcribe.models import AppPreferences
from scythe_transcribe.prompts import OPENROUTER_TRANSCRIPTION_NONE_OUTPUT
from scythe_transcribe.transcribe_pipeline import TranscribeResponse, text_to_paste


def test_text_to_paste_skips_none_transcript() -> None:
    """Silence sentinel should never be pasted."""
    prefs = AppPreferences(postprocess_enabled=False)
    result = TranscribeResponse(
        transcript=OPENROUTER_TRANSCRIPTION_NONE_OUTPUT,
        processed=None,
        silence_detected=True,
        id="x",
        created_at=0.0,
        transcribe_ms=0.0,
        total_ms=0.0,
    )
    assert text_to_paste(prefs, result) == ""


def test_text_to_paste_skips_none_transcript_even_with_postprocess() -> None:
    """The silence sentinel wins over any processed text."""
    prefs = AppPreferences(postprocess_enabled=True)
    result = TranscribeResponse(
        transcript=OPENROUTER_TRANSCRIPTION_NONE_OUTPUT,
        processed="should not paste",
        silence_detected=True,
        id="x",
        created_at=0.0,
        transcribe_ms=0.0,
        total_ms=0.0,
    )
    assert text_to_paste(prefs, result) == ""
