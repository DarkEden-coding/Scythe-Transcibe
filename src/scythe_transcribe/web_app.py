"""FastAPI application: REST API for the SPA and optional static file serving."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from scythe_transcribe import groq_client, openrouter_client
from scythe_transcribe.config import API_CORS_ORIGINS
from scythe_transcribe.models import AppPreferences, ChatProvider
from scythe_transcribe.settings_store import (
    get_groq_api_key,
    get_openrouter_api_key,
    load_json_cache,
    load_preferences,
    load_transcription_history,
    openrouter_models_cache_path,
    save_json_cache,
    save_preferences,
    set_groq_api_key,
    set_openrouter_api_key,
)
from scythe_transcribe.transcribe_pipeline import (
    TranscribeJob,
    TranscribeResponse,
    postprocess_transcript_text,
    transcribe_wav_bytes,
)


class ApiKeysPublic(BaseModel):
    """API key presence for the UI (no secret material)."""

    groq_configured: bool
    openrouter_configured: bool


class ApiKeysUpdate(BaseModel):
    """Update stored API keys; omit or null to leave unchanged."""

    groq: str | None = None
    openrouter: str | None = None


def _static_root() -> Path | None:
    """Directory containing built SPA (index.html), or None."""
    pkg = Path(__file__).resolve().parent
    # Prefer a Vite output tree from the repo over bundled ``web_dist`` so ``uv run``
    # picks up fresh builds without reinstalling the package.
    repo_dist = pkg.parent.parent / "frontend" / "dist"
    if repo_dist.is_dir() and (repo_dist / "index.html").is_file():
        return repo_dist
    bundled = pkg / "web_dist"
    if bundled.is_dir() and (bundled / "index.html").is_file():
        return bundled
    return None


def create_app() -> FastAPI:
    """Build FastAPI app with API routes and optional static SPA."""
    app = FastAPI(
        title="Scythe-Transcribe API",
        version="0.2.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(API_CORS_ORIGINS),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    static_root = _static_root()
    if static_root is not None:
        app.mount(
            "/assets",
            StaticFiles(directory=static_root / "assets"),
            name="assets",
        )

    @app.get("/api/health")
    def health() -> dict[str, str]:
        """Liveness probe for the local server."""
        return {"status": "ok"}

    @app.get("/api/preferences", response_model=dict[str, Any])
    def get_preferences() -> dict[str, Any]:
        """Return persisted UI preferences."""
        return load_preferences().to_json()

    @app.put("/api/preferences")
    def put_preferences(body: dict[str, Any]) -> dict[str, Any]:
        """Replace preferences from a JSON object."""
        prefs = AppPreferences.from_json({k: v for k, v in body.items() if isinstance(k, str)})
        save_preferences(prefs)
        return prefs.to_json()

    @app.get("/api/keys", response_model=ApiKeysPublic)
    def get_keys_public() -> ApiKeysPublic:
        """Return whether each provider key is configured."""
        g = get_groq_api_key()
        o = get_openrouter_api_key()
        return ApiKeysPublic(
            groq_configured=bool(g.strip()),
            openrouter_configured=bool(o.strip()),
        )

    @app.put("/api/keys")
    def put_keys(body: ApiKeysUpdate) -> ApiKeysPublic:
        """Update stored API keys."""
        if body.groq is not None:
            set_groq_api_key(body.groq.strip())
        if body.openrouter is not None:
            set_openrouter_api_key(body.openrouter.strip())
        return get_keys_public()

    @app.get("/api/groq/chat-models")
    def list_groq_chat_models() -> dict[str, list[str]]:
        """List Groq chat models (for post-process dropdown)."""
        key = get_groq_api_key()
        if not key:
            return {"models": []}
        models = groq_client.list_chat_models(key)
        return {"models": models[:200]}

    @app.get("/api/openrouter/models")
    def list_openrouter_models_cached() -> dict[str, list[dict[str, Any]]]:
        """Return OpenRouter model metadata from cache, fetching if missing."""
        path = openrouter_models_cache_path()
        raw = load_json_cache(path)
        stale = (
            not isinstance(raw, list)
            or len(raw) == 0
            or (
                isinstance(raw[0], dict)
                and "pricing_prompt" not in raw[0]
                and "pricing_completion" not in raw[0]
            )
        )
        if stale:
            try:
                key = get_openrouter_api_key() or None
                raw_list = openrouter_client.fetch_models_raw(key)
                infos = openrouter_client.parse_model_infos(raw_list)
                save_json_cache(path, [asdict(x) for x in infos])
                return {"models": [asdict(x) for x in infos]}
            except Exception:
                return {"models": []}
        out: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, dict):
                out.append(item)
        return {"models": out}

    @app.post("/api/openrouter/models/refresh")
    def refresh_openrouter_models() -> dict[str, Any]:
        """Fetch OpenRouter models and refresh the cache (no API key required)."""
        try:
            key = get_openrouter_api_key() or None
            raw_list = openrouter_client.fetch_models_raw(key)
            infos = openrouter_client.parse_model_infos(raw_list)
            path = openrouter_models_cache_path()
            save_json_cache(path, [asdict(x) for x in infos])
            return {"count": len(infos), "models": [asdict(x) for x in infos]}
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/transcription-history")
    def transcription_history() -> dict[str, Any]:
        """Return persisted transcription history (newest first)."""
        return {"entries": load_transcription_history()}

    @app.post("/api/transcribe", response_model=TranscribeResponse)
    async def transcribe(
        meta: Annotated[str, Form(description="JSON TranscribeJob")],
        audio: Annotated[UploadFile, File()],
    ) -> TranscribeResponse:
        """Transcribe uploaded WAV audio; optional LLM post-process in one request."""
        try:
            job = TranscribeJob.model_validate_json(meta)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Invalid meta JSON: {exc}") from exc

        raw = await audio.read()
        return transcribe_wav_bytes(job, raw)

    @app.post("/api/postprocess", response_model=dict[str, str])
    def postprocess_only(body: dict[str, Any]) -> dict[str, str]:
        """Run LLM post-process on existing transcript text."""
        transcript = str(body.get("transcript", ""))
        sys_prompt = str(body.get("postprocess_prompt", "") or "You are a helpful assistant.")
        post_model = str(body.get("postprocess_model", "")).strip()
        pprov = str(body.get("postprocess_provider", ChatProvider.OPENROUTER.value))
        groq_eff = str(body.get("postprocess_groq_reasoning_effort", "") or "").strip() or None
        or_eff = str(body.get("postprocess_openrouter_reasoning_effort", "") or "").strip() or None
        if not post_model:
            raise HTTPException(status_code=400, detail="postprocess_model required.")
        out, _, _, _ = postprocess_transcript_text(
            transcript=transcript,
            sys_prompt=sys_prompt,
            post_model=post_model,
            postprocess_provider=pprov,
            groq_reasoning_effort=groq_eff,
            openrouter_reasoning_effort=or_eff,
        )
        return {"processed": out}

    if static_root is not None:

        @app.get("/{full_path:path}")
        def spa_fallback(full_path: str) -> FileResponse:
            """Serve SPA index for client-side routes."""
            target = static_root / full_path
            if target.is_file():
                return FileResponse(target)
            return FileResponse(static_root / "index.html")

    return app
