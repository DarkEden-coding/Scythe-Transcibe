"""Runtime configuration (module-level constants; no CLI parsing)."""

from __future__ import annotations

# Local API server (Uvicorn).
API_HOST: str = "127.0.0.1"
API_PORT: int = 8765

# Browser URL for the SPA when served by FastAPI static files (production-style).
PUBLIC_BASE_URL: str = f"http://{API_HOST}:{API_PORT}/"

# Vite dev server default port (CORS + proxy in frontend/vite.config).
VITE_DEV_PORT: int = 5173

# Origins allowed for browser API calls (dev + same-origin).
API_CORS_ORIGINS: tuple[str, ...] = (
    f"http://127.0.0.1:{VITE_DEV_PORT}",
    f"http://localhost:{VITE_DEV_PORT}",
    f"http://{API_HOST}:{API_PORT}",
    f"http://localhost:{API_PORT}",
)

# Maximum upload size for recorded audio (bytes).
MAX_UPLOAD_BYTES: int = 50 * 1024 * 1024

# Post-process: split long transcripts into chunks (user text chars) so per-chunk
# ``max_tokens`` stays sufficient; chunks run in parallel.
POSTPROCESS_CHUNK_MAX_USER_CHARS: int = 4500
POSTPROCESS_MAX_PARALLEL_CHUNKS: int = 8


def postprocess_max_completion_tokens(*, system_prompt: str, user_content: str) -> int:
    """Upper bound for post-process completion length to limit decode latency.

    Total LLM latency scales roughly with output tokens; cap completions relative
    to estimated input size so short prompts do not inherit huge default ceilings.

    Args:
        system_prompt: System message text.
        user_content: User message text (e.g. transcript).

    Returns:
        A value suitable for ``max_tokens`` / ``max_completion_tokens``.
    """
    combined = len(system_prompt) + len(user_content)
    approx_input_tokens = max(1, (combined + 3) // 4)
    return min(2048, max(256, approx_input_tokens * 2))
