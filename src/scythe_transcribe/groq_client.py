"""Groq API: speech-to-text and chat completions."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from groq import Groq

from scythe_transcribe.config import postprocess_max_completion_tokens

_groq_lock = threading.Lock()
_groq_by_key: dict[str, Groq] = {}

_NO_SPEECH_THRESHOLD = 0.6
_LOGPROB_THRESHOLD = -1.0


@dataclass(frozen=True, slots=True)
class GroqTranscriptionResult:
    """Raw Groq transcription output and metadata."""

    text: str
    silence_detected: bool
    metadata: dict[str, object]


def _groq_client(api_key: str) -> Groq:
    """Return a cached ``Groq`` client to reuse HTTP connections across calls."""
    with _groq_lock:
        client = _groq_by_key.get(api_key)
        if client is None:
            client = Groq(api_key=api_key)
            _groq_by_key[api_key] = client
        return client


def _response_mapping(transcription: object) -> dict[str, Any]:
    """Return a plain mapping for a Groq transcription response."""
    if isinstance(transcription, dict):
        return transcription
    dump = getattr(transcription, "model_dump", None)
    if callable(dump):
        try:
            data = dump(mode="python")
        except TypeError:
            data = dump()
        if isinstance(data, dict):
            return data
    if hasattr(transcription, "__dict__"):
        data = dict(vars(transcription))
        if data:
            return data
    return {}


def _as_float(value: object) -> float | None:
    """Best-effort conversion to float."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: object) -> int | None:
    """Best-effort conversion to int."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _segment_summary(segment: Mapping[str, Any]) -> dict[str, object]:
    """Extract the metadata fields we care about from a Groq segment."""
    text = str(segment.get("text", "") or "").strip()
    return {
        "id": _as_int(segment.get("id")),
        "start": _as_float(segment.get("start")),
        "end": _as_float(segment.get("end")),
        "text": text,
        "no_speech_prob": _as_float(segment.get("no_speech_prob")),
        "avg_logprob": _as_float(segment.get("avg_logprob")),
        "compression_ratio": _as_float(segment.get("compression_ratio")),
    }


def _segment_is_silence(segment: Mapping[str, Any]) -> bool:
    """Apply Whisper-style silence detection to one segment."""
    no_speech_prob = _as_float(segment.get("no_speech_prob"))
    if no_speech_prob is None or no_speech_prob < _NO_SPEECH_THRESHOLD:
        return False
    avg_logprob = _as_float(segment.get("avg_logprob"))
    if avg_logprob is None:
        return True
    return avg_logprob <= _LOGPROB_THRESHOLD


def _transcription_result_from_response(transcription: object) -> GroqTranscriptionResult:
    """Normalize a Groq transcription response into text plus silence metadata."""
    data = _response_mapping(transcription)
    text = str(data.get("text", "") or "").strip()
    duration = _as_float(data.get("duration"))
    language = data.get("language")
    language_text = str(language).strip() if isinstance(language, str) else None

    raw_segments = data.get("segments")
    segments: list[dict[str, object]] = []
    if isinstance(raw_segments, list):
        for item in raw_segments:
            if isinstance(item, Mapping):
                segments.append(_segment_summary(item))
            elif hasattr(item, "model_dump"):
                dumped = item.model_dump(mode="python")  # type: ignore[call-arg]
                if isinstance(dumped, dict):
                    segments.append(_segment_summary(dumped))
            elif hasattr(item, "__dict__"):
                segments.append(_segment_summary(dict(vars(item))))

    silence_detected = False
    silent_segments = 0
    max_no_speech_prob: float | None = None
    min_avg_logprob: float | None = None
    max_compression_ratio: float | None = None
    if segments:
        silent_segments = sum(1 for seg in segments if _segment_is_silence(seg))
        silence_detected = silent_segments == len(segments)
        no_speech_probs = [
            prob
            for prob in (_as_float(seg.get("no_speech_prob")) for seg in segments)
            if prob is not None
        ]
        avg_logprobs = [
            prob
            for prob in (_as_float(seg.get("avg_logprob")) for seg in segments)
            if prob is not None
        ]
        compression_ratios = [
            ratio
            for ratio in (_as_float(seg.get("compression_ratio")) for seg in segments)
            if ratio is not None
        ]
        if no_speech_probs:
            max_no_speech_prob = max(no_speech_probs)
        if avg_logprobs:
            min_avg_logprob = min(avg_logprobs)
        if compression_ratios:
            max_compression_ratio = max(compression_ratios)
    else:
        silence_detected = not text

    metadata: dict[str, object] = {
        "provider": "groq",
        "response_format": "verbose_json",
        "is_silence": silence_detected,
    }
    if language_text:
        metadata["language"] = language_text
    if duration is not None:
        metadata["duration"] = duration
    metadata["segment_count"] = len(segments)
    metadata["silent_segment_count"] = silent_segments
    if max_no_speech_prob is not None:
        metadata["max_no_speech_prob"] = max_no_speech_prob
    if min_avg_logprob is not None:
        metadata["min_avg_logprob"] = min_avg_logprob
    if max_compression_ratio is not None:
        metadata["max_compression_ratio"] = max_compression_ratio
    if silence_detected:
        metadata["raw_text"] = text
    return GroqTranscriptionResult(
        text=text,
        silence_detected=silence_detected,
        metadata=metadata,
    )


def transcribe_audio(
    *,
    api_key: str,
    wav_bytes: bytes,
    model: str,
    filename: str = "recording.wav",
    prompt: str | None = None,
) -> GroqTranscriptionResult:
    """Transcribe WAV audio using Groq Whisper-compatible ASR.

    Args:
        api_key: Groq API key.
        wav_bytes: Raw WAV file bytes.
        model: Whisper model id (e.g. whisper-large-v3-turbo).
        filename: Filename hint for multipart upload.
        prompt: Optional Whisper prompt for spelling/context hints (Groq ASR).

    Returns:
        Raw transcript text plus silence metadata.

    Raises:
        Exception: On API or network errors (caller may show message).
    """
    client = _groq_client(api_key)
    kwargs: dict[str, object] = {
        "file": (filename, wav_bytes),
        "model": model,
        "response_format": "verbose_json",
        "timestamp_granularities": ["segment"],
        "temperature": 0.0,
    }
    if prompt and prompt.strip():
        kwargs["prompt"] = prompt.strip()
    transcription = client.audio.transcriptions.create(**kwargs)
    result = _transcription_result_from_response(transcription)
    return result


def chat_completion(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_content: str,
    reasoning_effort: str | None = None,
) -> str:
    """Run a chat completion on Groq.

    Args:
        api_key: Groq API key.
        model: Chat model id.
        system_prompt: System message text.
        user_content: User message text.
        reasoning_effort: Optional ``reasoning_effort`` for supported models (e.g. Qwen3, GPT-OSS).
            Omitted when empty or None.

    Returns:
        Assistant message content as string.
    """
    client = _groq_client(api_key)
    max_tok = postprocess_max_completion_tokens(
        system_prompt=system_prompt,
        user_content=user_content,
    )
    create_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.3,
        "max_tokens": max_tok,
        "service_tier": "on_demand",
    }
    if reasoning_effort and str(reasoning_effort).strip():
        create_kwargs["reasoning_effort"] = str(reasoning_effort).strip()
    completion = client.chat.completions.create(**create_kwargs)
    choice = completion.choices[0]
    msg = choice.message
    content = getattr(msg, "content", None) if msg else None
    if isinstance(content, str):
        return content.strip()
    return ""


def list_chat_models(api_key: str) -> list[str]:
    """Return chat-capable model ids from Groq (best-effort).

    Args:
        api_key: Groq API key.

    Returns:
        Sorted list of model ids, may be empty on failure.
    """
    try:
        client = _groq_client(api_key)
        models = client.models.list()
        data = getattr(models, "data", None) or []
        ids: list[str] = []
        for m in data:
            mid = getattr(m, "id", None) or (m.get("id") if isinstance(m, dict) else None)
            if isinstance(mid, str) and mid:
                ids.append(mid)
        return sorted(set(ids))
    except Exception:
        return []
