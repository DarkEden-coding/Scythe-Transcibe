"""Shared prompt text and output sentinels."""

from __future__ import annotations


OPENROUTER_TRANSCRIPTION_INSTRUCTION = (
    "Transcribe this audio accurately. If no words are in the audio, reply with exactly None. "
    "Otherwise reply with only the transcript."
)

OPENROUTER_TRANSCRIPTION_NONE_OUTPUT = "None"
