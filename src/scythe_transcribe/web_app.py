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

from scythe_transcribe.config import API_CORS_ORIGINS
from scythe_transcribe.frontend_activity import FrontendActivity
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
from scythe_transcribe.startup import is_startup_enabled, set_startup_enabled


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


def create_app(frontend_activity: FrontendActivity | None = None) -> FastAPI:
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

    @app.post("/api/frontend-session/{session_id}/heartbeat")
    def frontend_session_heartbeat(session_id: str) -> dict[str, str]:
        """Record that a browser frontend is open."""
        if frontend_activity is not None:
            frontend_activity.mark_seen(session_id)
        return {"status": "ok"}

    @app.delete("/api/frontend-session/{session_id}")
    def frontend_session_close(session_id: str) -> dict[str, str]:
        """Record that a browser frontend has closed."""
        if frontend_activity is not None:
            frontend_activity.close(session_id)
        return {"status": "ok"}

    @app.post("/api/frontend-session/{session_id}/close")
    def frontend_session_close_beacon(session_id: str) -> dict[str, str]:
        """Record frontend close from ``navigator.sendBeacon``."""
        if frontend_activity is not None:
            frontend_activity.close(session_id)
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
        from scythe_transcribe import groq_client

        key = get_groq_api_key()
        if not key:
            return {"models": []}
        models = groq_client.list_chat_models(key)
        return {"models": models[:200]}

    @app.get("/api/openrouter/models")
    def list_openrouter_models_cached() -> dict[str, list[dict[str, Any]]]:
        """Return OpenRouter model metadata from cache, fetching if missing."""
        from scythe_transcribe import openrouter_client

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
        from scythe_transcribe import openrouter_client

        try:
            key = get_openrouter_api_key() or None
            raw_list = openrouter_client.fetch_models_raw(key)
            infos = openrouter_client.parse_model_infos(raw_list)
            path = openrouter_models_cache_path()
            save_json_cache(path, [asdict(x) for x in infos])
            return {"count": len(infos), "models": [asdict(x) for x in infos]}
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/accessibility")
    def get_accessibility() -> dict[str, Any]:
        """Return macOS accessibility trust status."""
        import sys

        if sys.platform != "darwin":
            return {"supported": False, "trusted": True, "hotkey": {"state": "unsupported"}}
        try:
            from scythe_transcribe.hotkey_service import (
                current_app_identity,
                get_hotkey_listener_status,
                is_accessibility_trusted,
                start_hotkey_listener,
            )

            start_hotkey_listener()
            trusted = is_accessibility_trusted()
            return {
                "supported": True,
                "trusted": trusted,
                "hotkey": get_hotkey_listener_status(),
                "identity": current_app_identity(),
            }
        except Exception:
            return {"supported": False, "trusted": True, "hotkey": {"state": "unknown"}}

    @app.post("/api/accessibility/open-settings")
    def open_accessibility_settings() -> dict[str, str]:
        """Open macOS System Settings → Accessibility panel."""
        import subprocess
        import sys

        if sys.platform != "darwin":
            return {"status": "unsupported"}
        try:
            from scythe_transcribe.hotkey_service import request_accessibility_trust_prompt

            request_accessibility_trust_prompt()
        except Exception:
            pass
        subprocess.Popen(
            [
                "open",
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
            ]
        )
        return {"status": "ok"}

    @app.get("/api/startup")
    def get_startup() -> dict[str, bool]:
        """Return whether the app is registered to run at login."""
        return {"enabled": is_startup_enabled()}

    @app.put("/api/startup")
    def put_startup(body: dict[str, bool]) -> dict[str, bool]:
        """Register or unregister the app to run at login."""
        enabled = bool(body.get("enabled", False))
        set_startup_enabled(enabled)
        return {"enabled": is_startup_enabled()}

    @app.get("/api/transcription-history")
    def transcription_history() -> dict[str, Any]:
        """Return persisted transcription history (newest first)."""
        return {"entries": load_transcription_history()}

    @app.post("/api/transcribe")
    async def transcribe(
        meta: Annotated[str, Form(description="JSON TranscribeJob")],
        audio: Annotated[UploadFile, File()],
    ) -> Any:
        """Transcribe uploaded WAV audio; optional LLM post-process in one request."""
        from scythe_transcribe.transcribe_pipeline import TranscribeJob, transcribe_wav_bytes

        try:
            job = TranscribeJob.model_validate_json(meta)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Invalid meta JSON: {exc}") from exc

        raw = await audio.read()
        return transcribe_wav_bytes(job, raw)

    @app.post("/api/postprocess", response_model=dict[str, str])
    def postprocess_only(body: dict[str, Any]) -> dict[str, str]:
        """Run LLM post-process on existing transcript text."""
        from scythe_transcribe.transcribe_pipeline import postprocess_transcript_text

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
