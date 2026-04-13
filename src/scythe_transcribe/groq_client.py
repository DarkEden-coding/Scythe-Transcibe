"""Groq API: speech-to-text and chat completions."""

from __future__ import annotations

import threading
from typing import Any

from groq import Groq

from scythe_transcribe.config import postprocess_max_completion_tokens

_groq_lock = threading.Lock()
_groq_by_key: dict[str, Groq] = {}


def _groq_client(api_key: str) -> Groq:
    """Return a cached ``Groq`` client to reuse HTTP connections across calls."""
    with _groq_lock:
        client = _groq_by_key.get(api_key)
        if client is None:
            client = Groq(api_key=api_key)
            _groq_by_key[api_key] = client
        return client


def transcribe_audio(
    *,
    api_key: str,
    wav_bytes: bytes,
    model: str,
    filename: str = "recording.wav",
    prompt: str | None = None,
) -> str:
    """Transcribe WAV audio using Groq Whisper-compatible ASR.

    Args:
        api_key: Groq API key.
        wav_bytes: Raw WAV file bytes.
        model: Whisper model id (e.g. whisper-large-v3-turbo).
        filename: Filename hint for multipart upload.
        prompt: Optional Whisper prompt for spelling/context hints (Groq ASR).

    Returns:
        Plain transcript text.

    Raises:
        Exception: On API or network errors (caller may show message).
    """
    client = _groq_client(api_key)
    kwargs: dict[str, object] = {
        "file": (filename, wav_bytes),
        "model": model,
        "response_format": "json",
    }
    if prompt and prompt.strip():
        kwargs["prompt"] = prompt.strip()
    transcription = client.audio.transcriptions.create(**kwargs)
    text = getattr(transcription, "text", None)
    if isinstance(text, str):
        return text.strip()
    if isinstance(transcription, str):
        return transcription.strip()
    return str(transcription).strip()


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
