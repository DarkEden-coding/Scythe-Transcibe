"""OpenRouter: models list, audio chat (transcription), and text chat."""

from __future__ import annotations

import base64
from typing import Any

import httpx

from scythe_transcribe.config import postprocess_max_completion_tokens
from scythe_transcribe.models import OpenRouterModelInfo
from scythe_transcribe.prompts import OPENROUTER_TRANSCRIPTION_INSTRUCTION

OPENROUTER_BASE = "https://openrouter.ai/api/v1"


def _headers(api_key: str | None) -> dict[str, str]:
    """Headers for OpenRouter HTTP requests."""
    h: dict[str, str] = {"Accept": "application/json"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
        h["Content-Type"] = "application/json"
    return h


def fetch_models_raw(api_key: str | None = None) -> list[dict[str, Any]]:
    """Download the full models list from OpenRouter.

    The public ``/models`` endpoint works without an API key; an optional key
    may be sent for provider-specific behavior.

    Args:
        api_key: Optional OpenRouter API key.

    Returns:
        List of model objects as dicts.

    Raises:
        httpx.HTTPError: On HTTP errors.
    """
    url = f"{OPENROUTER_BASE}/models"
    with httpx.Client(timeout=60.0) as client:
        r = client.get(url, headers=_headers(api_key))
        r.raise_for_status()
        data = r.json()
    inner = data.get("data") if isinstance(data, dict) else None
    if not isinstance(inner, list):
        return []
    out: list[dict[str, Any]] = []
    for item in inner:
        if isinstance(item, dict):
            out.append(item)
    return out


def _architecture_modalities(model: dict[str, Any]) -> tuple[set[str], set[str]]:
    """Extract input/output modality strings from a model payload."""
    arch = model.get("architecture")
    in_mod: set[str] = set()
    out_mod: set[str] = set()
    if isinstance(arch, dict):
        for x in arch.get("input_modalities") or []:
            if isinstance(x, str):
                in_mod.add(x.lower())
        for x in arch.get("output_modalities") or []:
            if isinstance(x, str):
                out_mod.add(x.lower())
    top = model.get("top_provider") or {}
    if isinstance(top, dict):
        for x in top.get("input_modalities") or []:
            if isinstance(x, str):
                in_mod.add(x.lower())
    # Some payloads expose modality at root
    for x in model.get("input_modalities") or []:
        if isinstance(x, str):
            in_mod.add(x.lower())
    return in_mod, out_mod


def _format_usd_per_million(token_price: object | None) -> str:
    """Format OpenRouter per-token USD price as dollars per 1M tokens."""
    if token_price is None:
        return ""
    try:
        t = float(token_price)
    except (TypeError, ValueError):
        return ""
    per_m = t * 1_000_000.0
    if per_m <= 0:
        return "free"
    if per_m < 0.0001:
        return f"${per_m:.6f}/M"
    if per_m < 0.01:
        return f"${per_m:.4f}/M"
    if per_m < 1:
        return f"${per_m:.3f}/M"
    if per_m < 100:
        return f"${per_m:.2f}/M"
    return f"${per_m:,.0f}/M"


def _pricing_strings(pricing: object) -> tuple[str, str]:
    """Extract formatted in/out price strings from a ``pricing`` object."""
    if not isinstance(pricing, dict):
        return "", ""
    p_in = _format_usd_per_million(pricing.get("prompt"))
    p_out = _format_usd_per_million(pricing.get("completion"))
    return p_in, p_out


def parse_model_infos(models: list[dict[str, Any]]) -> list[OpenRouterModelInfo]:
    """Build structured model info for UI filtering."""
    result: list[OpenRouterModelInfo] = []
    for m in models:
        mid = m.get("id")
        if not isinstance(mid, str) or not mid:
            continue
        name = m.get("name") if isinstance(m.get("name"), str) else mid
        in_mod, out_mod = _architecture_modalities(m)
        supports_audio = "audio" in in_mod
        supports_text = "text" in out_mod or "text" in in_mod or not out_mod
        # Heuristic when API omits modalities
        lower = mid.lower()
        if not supports_audio and (
            "whisper" in lower or "gpt-4o-transcribe" in lower or "audio" in lower
        ):
            supports_audio = True
        p_in, p_out = _pricing_strings(m.get("pricing"))
        result.append(
            OpenRouterModelInfo(
                model_id=mid,
                name=name or mid,
                supports_audio_input=supports_audio,
                supports_text=bool(supports_text),
                pricing_prompt=p_in,
                pricing_completion=p_out,
            )
        )
    result.sort(key=lambda x: x.model_id.lower())
    return result


def transcribe_with_audio_model(
    *,
    api_key: str,
    model: str,
    wav_bytes: bytes,
    instruction: str = OPENROUTER_TRANSCRIPTION_INSTRUCTION,
) -> str:
    """Transcribe by sending WAV as input_audio to a chat model.

    Args:
        api_key: OpenRouter API key.
        model: Model id that supports audio input.
        wav_bytes: WAV file bytes.
        instruction: User instruction alongside the audio.

    Returns:
        Assistant text (transcript or model reply).

    Raises:
        httpx.HTTPError: On HTTP errors.
    """
    b64 = base64.b64encode(wav_bytes).decode("ascii")
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": instruction},
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": b64,
                            "format": "wav",
                        },
                    },
                ],
            }
        ],
        "temperature": 0.2,
    }
    url = f"{OPENROUTER_BASE}/chat/completions"
    with httpx.Client(timeout=120.0) as client:
        r = client.post(url, headers=_headers(api_key), json=payload)
        r.raise_for_status()
        data = r.json()
    return _extract_assistant_text(data)


def chat_text(
    *,
    api_key: str,
    model: str,
    system_prompt: str,
    user_content: str,
    reasoning_effort: str | None = None,
) -> str:
    """Standard text chat completion via OpenRouter.

    Args:
        api_key: OpenRouter API key.
        model: Chat model id.
        system_prompt: System message.
        user_content: User message.
        reasoning_effort: Maps to ``reasoning.effort`` when set. Omitted when empty.

    Returns:
        Assistant text.

    Raises:
        httpx.HTTPError: On HTTP errors.
    """
    max_tok = postprocess_max_completion_tokens(
        system_prompt=system_prompt,
        user_content=user_content,
    )
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.3,
        "max_tokens": max_tok,
    }
    if reasoning_effort and str(reasoning_effort).strip():
        payload["reasoning"] = {"effort": str(reasoning_effort).strip()}
    url = f"{OPENROUTER_BASE}/chat/completions"
    with httpx.Client(timeout=120.0) as client:
        r = client.post(url, headers=_headers(api_key), json=payload)
        r.raise_for_status()
        data = r.json()
    return _extract_assistant_text(data)


def _extract_assistant_text(data: dict[str, Any]) -> str:
    """Parse assistant message content from chat completion JSON."""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    msg = first.get("message")
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(parts).strip()
    return ""
