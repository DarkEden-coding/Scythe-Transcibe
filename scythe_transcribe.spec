# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Scythe-Transcribe.

Build on macOS  → produces dist/Scythe-Transcribe.app  (menu-bar only, no Dock icon)
Build on Windows → produces dist/scythe-transcribe/     (system-tray, no console)

Run via:  python build.py
      or: pyinstaller --clean scythe_transcribe.spec
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

ROOT = Path(SPECPATH)
SRC  = ROOT / "src"

# ── Data files ────────────────────────────────────────────────────────────────

datas = []

# Bundled frontend SPA (built by npm before calling PyInstaller)
web_dist_src = SRC / "scythe_transcribe" / "web_dist"
if web_dist_src.is_dir():
    datas.append((str(web_dist_src), "scythe_transcribe/web_dist"))

# PortAudio shared library (required by sounddevice at runtime)
try:
    datas += collect_data_files("_sounddevice_data")
except Exception:
    pass

# ── Hidden imports ────────────────────────────────────────────────────────────

hiddenimports = [
    # ── uvicorn ──
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.middleware",
    "uvicorn.middleware.proxy_headers",
    # ── FastAPI / Starlette ──
    "fastapi",
    "fastapi.staticfiles",
    "fastapi.responses",
    "fastapi.middleware",
    "fastapi.middleware.cors",
    "starlette",
    "starlette.routing",
    "starlette.middleware",
    "starlette.middleware.cors",
    "starlette.staticfiles",
    "starlette.responses",
    "starlette.exceptions",
    "starlette.background",
    "starlette.datastructures",
    "starlette.types",
    # ── HTTP ──
    "h11",
    "httpx",
    "httpcore",
    # ── multipart uploads ──
    "multipart",
    "python_multipart",
    # ── audio ──
    "sounddevice",
    "_sounddevice_data",
    "numpy",
    # ── keyboard / clipboard ──
    "pynput",
    "pynput.keyboard",
    "pynput.mouse",
    "pynput._util",
    "pyperclip",
    # ── tray icon ──
    "pystray",
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    # ── misc runtime ──
    "platformdirs",
    "setproctitle",
    "groq",
    "dotenv",
    "six",
    "pydantic",
    "pydantic_core",
    "annotated_types",
    "sniffio",
]

# ── Platform-specific hidden imports ─────────────────────────────────────────

if sys.platform == "darwin":
    hiddenimports += [
        "pynput.keyboard._darwin",
        "pynput.mouse._darwin",
        "pynput._util.darwin",
        "pynput._util.darwin_vks",
        "pystray._darwin",
        # PyObjC frameworks used by pystray and pynput
        "AppKit",
        "Foundation",
        "objc",
        "PyObjCTools",
        "PyObjCTools.MachSignals",
        "Quartz",
        "HIServices",
        "CoreFoundation",
    ]
elif sys.platform == "win32":
    hiddenimports += [
        "pynput.keyboard._win32",
        "pynput.mouse._win32",
        "pystray._win32",
        "msvcrt",
        "winreg",
    ]

# ── Analysis ─────────────────────────────────────────────────────────────────

a = Analysis(
    [str(SRC / "scythe_transcribe" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "jupyter", "_pytest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# ── EXE ──────────────────────────────────────────────────────────────────────

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="scythe-transcribe",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,              # No terminal/console window on either platform
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file="entitlements.plist" if sys.platform == "darwin" else None,
    icon=None,
)

# ── Collect (one-dir bundle) ──────────────────────────────────────────────────

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="scythe-transcribe",
)

# ── macOS: wrap into a proper .app bundle ────────────────────────────────────

if sys.platform == "darwin":
    app = BUNDLE(  # noqa: F821
        coll,
        name="Scythe-Transcribe.app",
        icon=None,
        bundle_identifier="com.scythe-transcribe.app",
        info_plist={
            # LSUIElement=True → background app: menu-bar only, no Dock icon
            "LSUIElement": True,
            "CFBundleDisplayName": "Scythe-Transcribe",
            "CFBundleShortVersionString": "0.2.0",
            "CFBundleVersion": "0.2.0",
            "NSMicrophoneUsageDescription": (
                "Scythe-Transcribe records audio via your microphone to transcribe speech."
            ),
            "NSAppleEventsUsageDescription": (
                "Scythe-Transcribe uses Apple Events to paste transcribed text into other apps."
            ),
            "NSAccessibilityUsageDescription": (
                "Scythe-Transcribe needs Accessibility access to detect the hold-to-talk "
                "hotkey and paste transcribed text."
            ),
        },
    )
