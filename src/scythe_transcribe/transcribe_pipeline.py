"""Shared speech-to-text and optional post-process pipeline (HTTP and hotkey)."""

from __future__ import annotations

import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import HTTPException
from pydantic import BaseModel

from scythe_transcribe import groq_client, openrouter_client
from scythe_transcribe.config import (
    MAX_UPLOAD_BYTES,
    POSTPROCESS_CHUNK_MAX_USER_CHARS,
    POSTPROCESS_MAX_PARALLEL_CHUNKS,
)
from scythe_transcribe.models import AppPreferences, ChatProvider, TranscriptionProvider
from scythe_transcribe.settings_store import (
    append_transcription_history,
    get_groq_api_key,
    get_openrouter_api_key,
)
from scythe_transcribe.text_replacements import (
    apply_replacements,
    groq_asr_prompt_from_replacement_spec,
    parse_replacement_spec,
)

_logger = logging.getLogger(__name__)


class TranscribeJob(BaseModel):
    """JSON metadata for multipart transcribe requests."""

    transcription_provider: str = TranscriptionProvider.GROQ.value
    transcription_model_groq: str = ""
    transcription_model_openrouter: str = ""
    openrouter_transcription_instruction: str = ""
    keyword_replacement_spec: str = ""
    postprocess_enabled: bool = False
    postprocess_prompt: str = ""
    postprocess_provider: str = ChatProvider.OPENROUTER.value
    postprocess_model: str = ""
    postprocess_groq_reasoning_effort: str = ""
    postprocess_openrouter_reasoning_effort: str = ""


class TranscribeResponse(BaseModel):
    """Transcript, optional LLM output, and pipeline timing."""

    transcript: str
    processed: str | None = None
    id: str
    created_at: float
    transcript_chars: int = 0
    transcribe_ms: float
    pre_postprocess_ms: float | None = None
    postprocess_ms: float | None = None
    postprocess_prep_ms: float | None = None
    postprocess_api_ms: float | None = None
    postprocess_chunks: int | None = None
    hotkey_post_api_to_paste_ms: float | None = None
    hotkey_paste_chord_ms: float | None = None
    total_ms: float


def transcribe_job_from_preferences(prefs: AppPreferences) -> TranscribeJob:
    """Build a transcribe job from persisted UI preferences."""
    return TranscribeJob(
        transcription_provider=prefs.transcription_provider,
        transcription_model_groq=prefs.transcription_model_groq,
        transcription_model_openrouter=prefs.transcription_model_openrouter,
        openrouter_transcription_instruction=prefs.openrouter_transcription_instruction,
        keyword_replacement_spec=prefs.keyword_replacement_spec,
        postprocess_enabled=prefs.postprocess_enabled,
        postprocess_prompt=prefs.postprocess_prompt,
        postprocess_provider=prefs.postprocess_provider,
        postprocess_model=prefs.postprocess_model,
        postprocess_groq_reasoning_effort=prefs.postprocess_groq_reasoning_effort,
        postprocess_openrouter_reasoning_effort=prefs.postprocess_openrouter_reasoning_effort,
    )


def _segment_instruction(index: int, total: int) -> str:
    """Append to system prompt so each chunk is scoped when using parallel segments."""
    return (
        f"\n\n[Segment {index + 1} of {total} of the same transcript. "
        "Process only the user's segment below; output only the processed text for this segment.]"
    )


def _chunk_transcript_for_postprocess(text: str, max_chars: int) -> list[str]:
    """Split transcript into segments at paragraph boundaries when possible.

    Args:
        text: Full transcript after replacements.
        max_chars: Maximum characters per segment (user message only).

    Returns:
        Non-empty list of segments whose concatenation with ``\\n\\n`` recovers structure
        approximately (paragraph splits preserved where possible).
    """
    if max_chars < 256:
        max_chars = 256
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    buf = ""
    for part in text.split("\n\n"):
        sep = "\n\n" if buf else ""
        candidate = (buf + sep + part) if buf else part
        if len(candidate) <= max_chars:
            buf = candidate
            continue
        if buf:
            chunks.append(buf)
            buf = ""
        if len(part) <= max_chars:
            buf = part
            continue
        offset = 0
        while offset < len(part):
            end = min(offset + max_chars, len(part))
            chunks.append(part[offset:end])
            offset = end
    if buf:
        chunks.append(buf)
    return chunks


def postprocess_transcript_text(
    *,
    transcript: str,
    sys_prompt: str,
    post_model: str,
    postprocess_provider: str,
    groq_reasoning_effort: str | None,
    openrouter_reasoning_effort: str | None,
) -> tuple[str, float, float, int]:
    """Run LLM post-processing with parallel chunking for long transcripts.

    Args:
        transcript: Text to process (e.g. corrected transcript).
        sys_prompt: System-style instructions.
        post_model: Provider model id.
        postprocess_provider: ``groq`` or ``openrouter``.
        groq_reasoning_effort: Optional Groq ``reasoning_effort``.
        openrouter_reasoning_effort: Optional OpenRouter reasoning effort.

    Returns:
        ``(processed_text, prep_ms, api_wall_ms, chunk_count)`` where ``prep_ms`` is
        time from transcript-ready to first API work, and ``api_wall_ms`` is wall-clock
        time for completion(s) (parallel wall time when chunking).

    Raises:
        HTTPException: On missing keys or configuration errors.
    """
    t_ready = time.perf_counter()
    base_sys = (sys_prompt or "").strip() or "You are a helpful assistant."
    model = (post_model or "").strip()
    if not model:
        raise HTTPException(status_code=400, detail="Post-process model required when enabled.")

    chunks = _chunk_transcript_for_postprocess(
        transcript,
        POSTPROCESS_CHUNK_MAX_USER_CHARS,
    )
    pprov = postprocess_provider or ChatProvider.OPENROUTER.value
    groq_eff = (groq_reasoning_effort or "").strip() or None
    or_eff = (openrouter_reasoning_effort or "").strip() or None

    def run_segment(idx: int, user_chunk: str) -> tuple[int, str]:
        sys_seg = base_sys + _segment_instruction(idx, len(chunks))
        if pprov == ChatProvider.GROQ.value:
            gkey = get_groq_api_key()
            if not gkey:
                raise HTTPException(status_code=400, detail="Groq API key not configured.")
            out = groq_client.chat_completion(
                api_key=gkey,
                model=model,
                system_prompt=sys_seg,
                user_content=user_chunk,
                reasoning_effort=groq_eff,
            )
        else:
            okey = get_openrouter_api_key()
            if not okey:
                raise HTTPException(status_code=400, detail="OpenRouter API key not configured.")
            out = openrouter_client.chat_text(
                api_key=okey,
                model=model,
                system_prompt=sys_seg,
                user_content=user_chunk,
                reasoning_effort=or_eff,
            )
        return idx, out

    t_before_api = time.perf_counter()
    prep_ms = (t_before_api - t_ready) * 1000.0

    if len(chunks) == 1:
        _, text = run_segment(0, chunks[0])
        api_wall_ms = (time.perf_counter() - t_before_api) * 1000.0
        return text, prep_ms, api_wall_ms, 1

    workers = min(POSTPROCESS_MAX_PARALLEL_CHUNKS, len(chunks))
    results: list[str | None] = [None] * len(chunks)
    t_api_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(run_segment, i, ch) for i, ch in enumerate(chunks)]
        for fut in as_completed(futures):
            idx, seg_text = fut.result()
            results[idx] = seg_text
    api_wall_ms = (time.perf_counter() - t_api_start) * 1000.0
    merged = "\n\n".join(s or "" for s in results)
    return merged, prep_ms, api_wall_ms, len(chunks)


def transcribe_wav_bytes(job: TranscribeJob, raw: bytes) -> TranscribeResponse:
    """Transcribe WAV bytes; optional LLM post-process. Raises HTTPException on errors."""
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Audio upload too large.")
    if not raw:
        raise HTTPException(status_code=400, detail="Empty audio upload.")

    entry_id = str(uuid.uuid4())
    pipeline_start = time.perf_counter()
    provider = job.transcription_provider
    transcript = ""

    t_asr_start = time.perf_counter()
    if provider == TranscriptionProvider.GROQ.value:
        key = get_groq_api_key()
        if not key:
            raise HTTPException(status_code=400, detail="Groq API key not configured.")
        model = (job.transcription_model_groq or "").strip() or "whisper-large-v3-turbo"
        whisper_ctx = groq_asr_prompt_from_replacement_spec(job.keyword_replacement_spec or "")
        transcript = groq_client.transcribe_audio(
            api_key=key,
            wav_bytes=raw,
            model=model,
            prompt=whisper_ctx,
        )
    elif provider == TranscriptionProvider.OPENROUTER.value:
        key = get_openrouter_api_key()
        if not key:
            raise HTTPException(status_code=400, detail="OpenRouter API key not configured.")
        model = (job.transcription_model_openrouter or "").strip()
        if not model:
            raise HTTPException(status_code=400, detail="OpenRouter transcription model required.")
        or_instr = (job.openrouter_transcription_instruction or "").strip() or (
            "Transcribe this audio accurately. Reply with only the transcript."
        )
        transcript = openrouter_client.transcribe_with_audio_model(
            api_key=key,
            model=model,
            wav_bytes=raw,
            instruction=or_instr,
        )
    else:
        raise HTTPException(status_code=400, detail="Unknown transcription provider.")
    transcribe_ms = (time.perf_counter() - t_asr_start) * 1000.0

    pairs = parse_replacement_spec(job.keyword_replacement_spec or "")
    corrected = apply_replacements(transcript, pairs)
    t_transcript_ready = time.perf_counter()

    processed: str | None = None
    postprocess_ms: float | None = None
    pre_postprocess_ms: float | None = None
    postprocess_prep_ms: float | None = None
    postprocess_api_ms: float | None = None
    postprocess_chunks: int | None = None
    if job.postprocess_enabled:
        sys_prompt = (job.postprocess_prompt or "").strip() or "You are a helpful assistant."
        post_model = (job.postprocess_model or "").strip()
        if not post_model:
            raise HTTPException(status_code=400, detail="Post-process model required when enabled.")
        pprov = job.postprocess_provider or ChatProvider.OPENROUTER.value
        groq_eff = (job.postprocess_groq_reasoning_effort or "").strip() or None
        or_eff = (job.postprocess_openrouter_reasoning_effort or "").strip() or None
        pre_pp_ms = (time.perf_counter() - t_transcript_ready) * 1000.0
        pre_postprocess_ms = pre_pp_ms
        _logger.info(
            "postprocess_pre_api id=%s pre_postprocess_ms=%.1f "
            "(transcript_ready to postprocess start)",
            entry_id,
            pre_pp_ms,
        )
        t_pp_start = time.perf_counter()
        processed, prep_ms, api_ms, n_chunks = postprocess_transcript_text(
            transcript=corrected,
            sys_prompt=sys_prompt,
            post_model=post_model,
            postprocess_provider=pprov,
            groq_reasoning_effort=groq_eff,
            openrouter_reasoning_effort=or_eff,
            trace_id=entry_id,
        )
        postprocess_ms = (time.perf_counter() - t_pp_start) * 1000.0
        postprocess_prep_ms = prep_ms
        postprocess_api_ms = api_ms
        postprocess_chunks = n_chunks

    total_ms = (time.perf_counter() - pipeline_start) * 1000.0
    created_at = time.time() * 1000.0

    response = TranscribeResponse(
        transcript=corrected,
        processed=processed,
        id=entry_id,
        created_at=created_at,
        transcript_chars=len(corrected),
        transcribe_ms=transcribe_ms,
        pre_postprocess_ms=pre_postprocess_ms,
        postprocess_ms=postprocess_ms,
        postprocess_prep_ms=postprocess_prep_ms,
        postprocess_api_ms=postprocess_api_ms,
        postprocess_chunks=postprocess_chunks,
        total_ms=total_ms,
    )
    try:
        append_transcription_history(response.model_dump())
    except Exception:
        _logger.exception("append_transcription_history failed")
    pp_log = f"{postprocess_ms:.1f}" if postprocess_ms is not None else "none"
    prep_log = (
        f"{postprocess_prep_ms:.1f}" if postprocess_prep_ms is not None else "none"
    )
    api_log = f"{postprocess_api_ms:.1f}" if postprocess_api_ms is not None else "none"
    ch_log = str(postprocess_chunks) if postprocess_chunks is not None else "none"
    _logger.info(
        "transcription pipeline completed id=%s transcribe_ms=%.1f postprocess_ms=%s "
        "postprocess_prep_ms=%s postprocess_api_ms=%s postprocess_chunks=%s total_ms=%.1f",
        entry_id,
        transcribe_ms,
        pp_log,
        prep_log,
        api_log,
        ch_log,
        total_ms,
    )
    return response


def text_to_paste(prefs: AppPreferences, result: TranscribeResponse) -> str:
    """Choose clipboard text: post-processed when enabled, otherwise transcript."""
    if prefs.postprocess_enabled and result.processed:
        return result.processed
    return result.transcript
