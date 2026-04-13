"""Domain types and constants for transcription providers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from scythe_transcribe.prompts import OPENROUTER_TRANSCRIPTION_INSTRUCTION


class TranscriptionProvider(StrEnum):
    """Backend used for speech-to-text."""

    GROQ = "groq"
    OPENROUTER = "openrouter"


class ChatProvider(StrEnum):
    """Backend used for post-processing chat completions."""

    GROQ = "groq"
    OPENROUTER = "openrouter"


# Default Groq Whisper / ASR model IDs (see Groq speech-to-text docs).
GROQ_STT_MODEL_DEFAULTS: tuple[str, ...] = (
    "whisper-large-v3",
    "whisper-large-v3-turbo",
    "distil-whisper-large-v3-en",
)


@dataclass
class AppPreferences:
    """Persisted UI state (no API keys)."""

    transcription_provider: str = TranscriptionProvider.GROQ.value
    transcription_model_groq: str = "whisper-large-v3-turbo"
    transcription_model_openrouter: str = ""
    postprocess_enabled: bool = False
    postprocess_prompt: str = "Summarize the transcript in bullet points."
    postprocess_provider: str = ChatProvider.OPENROUTER.value
    postprocess_model: str = "openai/gpt-4o-mini"
    postprocess_groq_reasoning_effort: str = ""
    postprocess_openrouter_reasoning_effort: str = ""
    openrouter_models_cache_hint: str = ""
    keyword_replacement_spec: str = ""
    openrouter_transcription_instruction: str = OPENROUTER_TRANSCRIPTION_INSTRUCTION
    hotkey_toggle_recording: str = "ctrl+shift+space"

    def to_json(self) -> dict[str, object]:
        """Serialize preferences to a JSON-compatible dict."""
        return {
            "transcription_provider": self.transcription_provider,
            "transcription_model_groq": self.transcription_model_groq,
            "transcription_model_openrouter": self.transcription_model_openrouter,
            "postprocess_enabled": self.postprocess_enabled,
            "postprocess_prompt": self.postprocess_prompt,
            "postprocess_provider": self.postprocess_provider,
            "postprocess_model": self.postprocess_model,
            "postprocess_groq_reasoning_effort": self.postprocess_groq_reasoning_effort,
            "postprocess_openrouter_reasoning_effort": self.postprocess_openrouter_reasoning_effort,
            "openrouter_models_cache_hint": self.openrouter_models_cache_hint,
            "keyword_replacement_spec": self.keyword_replacement_spec,
            "openrouter_transcription_instruction": self.openrouter_transcription_instruction,
            "hotkey_toggle_recording": self.hotkey_toggle_recording,
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> AppPreferences:
        """Load preferences from a dict with defaults for missing keys."""
        defaults = cls()
        return cls(
            transcription_provider=str(
                data.get("transcription_provider", defaults.transcription_provider)
            ),
            transcription_model_groq=str(
                data.get("transcription_model_groq", defaults.transcription_model_groq)
            ),
            transcription_model_openrouter=str(
                data.get("transcription_model_openrouter", defaults.transcription_model_openrouter)
            ),
            postprocess_enabled=bool(data.get("postprocess_enabled", False)),
            postprocess_prompt=str(data.get("postprocess_prompt", defaults.postprocess_prompt)),
            postprocess_provider=str(
                data.get("postprocess_provider", defaults.postprocess_provider)
            ),
            postprocess_model=str(data.get("postprocess_model", defaults.postprocess_model)),
            postprocess_groq_reasoning_effort=str(
                data.get(
                    "postprocess_groq_reasoning_effort",
                    defaults.postprocess_groq_reasoning_effort,
                )
            ),
            postprocess_openrouter_reasoning_effort=str(
                data.get(
                    "postprocess_openrouter_reasoning_effort",
                    defaults.postprocess_openrouter_reasoning_effort,
                )
            ),
            openrouter_models_cache_hint=str(
                data.get("openrouter_models_cache_hint", defaults.openrouter_models_cache_hint)
            ),
            keyword_replacement_spec=str(
                data.get("keyword_replacement_spec", defaults.keyword_replacement_spec)
            ),
            openrouter_transcription_instruction=str(
                data.get(
                    "openrouter_transcription_instruction",
                    defaults.openrouter_transcription_instruction,
                )
            ),
            hotkey_toggle_recording=str(
                data.get("hotkey_toggle_recording", defaults.hotkey_toggle_recording)
            ),
        )


@dataclass
class OpenRouterModelInfo:
    """Minimal OpenRouter model metadata for dropdowns."""

    model_id: str
    name: str = ""
    supports_audio_input: bool = False
    supports_text: bool = True
    pricing_prompt: str = ""
    pricing_completion: str = ""
