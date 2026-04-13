#!/usr/bin/env python3
"""Build Scythe-Transcribe distributable.

Steps
-----
1. ``npm install`` (if node_modules missing) + ``npm run build`` in frontend/
2. Copy frontend/dist → src/scythe_transcribe/web_dist  (bundled SPA)
3. ``pyinstaller --clean scythe_transcribe.spec``

Output
------
macOS   → dist/Scythe-Transcribe.app   (drag to /Applications)
Windows → dist/scythe-transcribe/      (run scythe-transcribe.exe inside)
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND     = ROOT / "frontend"
FRONTEND_OUT = FRONTEND / "dist"
WEB_DIST_PKG = ROOT / "src" / "scythe_transcribe" / "web_dist"
PACKAGE_DIR = ROOT / "src" / "scythe_transcribe"
FRONTEND_PUBLIC = FRONTEND / "public"
ICON_ASSETS = [
    PACKAGE_DIR / "icon-blue.webp",
    PACKAGE_DIR / "icon-red.webp",
    PACKAGE_DIR / "icon-yellow.webp",
]


# ── helpers ───────────────────────────────────────────────────────────────────


def _npm() -> str:
    """Return npm executable name, or exit with a helpful message."""
    for name in ("npm", "npm.cmd"):
        path = shutil.which(name)
        if path:
            return path
    sys.exit(
        "ERROR: npm not found in PATH.\n"
        "Install Node.js (https://nodejs.org/) to build the web UI,\n"
        "or copy a pre-built frontend/dist into src/scythe_transcribe/web_dist/\n"
        "and re-run with --skip-frontend."
    )


def _run(*cmd: str, cwd: Path | None = None) -> None:
    print(f"  $ {' '.join(cmd)}")
    subprocess.run(list(cmd), cwd=cwd, check=True)


def _sync_frontend_icons() -> None:
    """Copy the root icon assets into Vite's public directory."""
    FRONTEND_PUBLIC.mkdir(parents=True, exist_ok=True)
    for src in ICON_ASSETS:
        shutil.copy2(src, FRONTEND_PUBLIC / src.name)


# ── build steps ───────────────────────────────────────────────────────────────


def build_frontend() -> None:
    print("\n── 1/3  Build frontend (npm) ────────────────────────────────")
    _sync_frontend_icons()
    npm = _npm()
    if not (FRONTEND / "node_modules").is_dir():
        print("  Installing npm dependencies...")
        _run(npm, "install", cwd=FRONTEND)
    _run(npm, "run", "build", cwd=FRONTEND)

    print(f"\n── 2/3  Copy {FRONTEND_OUT.relative_to(ROOT)} "
          f"→ {WEB_DIST_PKG.relative_to(ROOT)} ───")
    if WEB_DIST_PKG.exists():
        shutil.rmtree(WEB_DIST_PKG)
    shutil.copytree(FRONTEND_OUT, WEB_DIST_PKG)
    print(f"  Copied {sum(1 for _ in WEB_DIST_PKG.rglob('*') if _.is_file())} files.")


def run_pyinstaller() -> None:
    print("\n── 3/3  PyInstaller ─────────────────────────────────────────")
    _run(sys.executable, "-m", "PyInstaller", "--clean", "-y", "scythe_transcribe.spec",
         cwd=ROOT)


# ── entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    skip_frontend = "--skip-frontend" in sys.argv

    if not skip_frontend:
        build_frontend()
    else:
        print("Skipping frontend build (--skip-frontend).")
        if not (WEB_DIST_PKG / "index.html").is_file():
            sys.exit(
                f"ERROR: {WEB_DIST_PKG} has no index.html.\n"
                "Run without --skip-frontend to build it first."
            )

    run_pyinstaller()

    print("\n── Done ─────────────────────────────────────────────────────")
    if sys.platform == "darwin":
        print("  Output: dist/Scythe-Transcribe.app")
        print("  Drag to /Applications or double-click to launch.")
    else:
        print("  Output: dist/scythe-transcribe/")
        print("  Run:    dist\\scythe-transcribe\\scythe-transcribe.exe")


if __name__ == "__main__":
    main()
