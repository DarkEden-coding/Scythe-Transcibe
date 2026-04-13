"""Tests for Groq transcription metadata handling."""

from __future__ import annotations

from scythe_transcribe.groq_client import _transcription_result_from_response


def test_groq_transcription_result_detects_silence_from_verbose_metadata() -> None:
    """Verbose segment metadata should mark silent audio as silence."""
    payload = {
        "text": "hallucinated text",
        "duration": 2.5,
        "language": "en",
        "segments": [
            {
                "id": 0,
                "start": 0.0,
                "end": 2.5,
                "text": "hallucinated text",
                "avg_logprob": -1.2,
                "compression_ratio": 1.5,
                "no_speech_prob": 0.96,
            }
        ],
    }

    result = _transcription_result_from_response(payload)

    assert result.text == "hallucinated text"
    assert result.silence_detected is True
    assert result.metadata["provider"] == "groq"
    assert result.metadata["is_silence"] is True
    assert result.metadata["segment_count"] == 1
    assert result.metadata["max_no_speech_prob"] == 0.96


def test_groq_transcription_result_keeps_speech() -> None:
    """Speech metadata should not be collapsed into silence."""
    payload = {
        "text": "hello world",
        "segments": [
            {
                "id": 0,
                "text": "hello world",
                "avg_logprob": -0.08,
                "compression_ratio": 1.2,
                "no_speech_prob": 0.02,
            }
        ],
    }

    result = _transcription_result_from_response(payload)

    assert result.text == "hello world"
    assert result.silence_detected is False
    assert result.metadata["is_silence"] is False
