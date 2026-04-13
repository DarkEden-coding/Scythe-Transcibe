"""Rebuild the Vite SPA when a source checkout has newer files than ``frontend/dist``."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _package_dir() -> Path:
    """Directory containing this package's modules."""
    return Path(__file__).resolve().parent


def _frontend_dir() -> Path:
    """``frontend/`` at repository root (only exists in a dev tree)."""
    return _package_dir().parent.parent / "frontend"


def _source_tree_mtime(frontend: Path) -> float:
    """Latest modification time among inputs that affect the production bundle."""
    mt = 0.0
    for name in (
        "package.json",
        "package-lock.json",
        "vite.config.ts",
        "tsconfig.json",
        "index.html",
    ):
        p = frontend / name
        if p.is_file():
            mt = max(mt, p.stat().st_mtime)
    src = frontend / "src"
    if src.is_dir():
        for path in src.rglob("*"):
            if path.is_file():
                mt = max(mt, path.stat().st_mtime)
    return mt


def _npm_executable() -> str:
    """Resolve ``npm`` for subprocess (``npm.cmd`` on Windows)."""
    for name in ("npm", "npm.cmd"):
        path = shutil.which(name)
        if path:
            return path
    raise FileNotFoundError(
        "npm not found in PATH; install Node.js to build the web UI, "
        "or set SCYTHE_SKIP_FRONTEND_BUILD=1 to skip automatic builds.",
    )


def _needs_frontend_build(frontend: Path) -> bool:
    """Return True if ``package.json`` exists and dist is missing or stale."""
    if not (frontend / "package.json").is_file():
        return False
    dist_index = frontend / "dist" / "index.html"
    if not dist_index.is_file():
        return True
    return _source_tree_mtime(frontend) > dist_index.stat().st_mtime


def ensure_frontend_built() -> None:
    """Run ``npm install`` (if needed) and ``npm run build`` when sources are newer than dist.

    Skipped when ``SCYTHE_SKIP_FRONTEND_BUILD`` is ``1``/``true``/``yes``, or when no
    ``frontend/package.json`` exists (e.g. installed wheel without sources).
    """
    if os.environ.get("SCYTHE_SKIP_FRONTEND_BUILD", "").strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return
    frontend = _frontend_dir()
    if not (frontend / "package.json").is_file():
        return
    if not _needs_frontend_build(frontend):
        return
    print(
        "scythe-transcribe: rebuilding web UI (npm run build in frontend/)...",
        file=sys.stderr,
    )
    npm = _npm_executable()
    if not (frontend / "node_modules").is_dir():
        print("scythe-transcribe: npm install (first-time frontend deps)...", file=sys.stderr)
        subprocess.run([npm, "install"], cwd=frontend, check=True)
    subprocess.run([npm, "run", "build"], cwd=frontend, check=True)
