"""Tests for platform startup registration helpers."""

from __future__ import annotations

import plistlib
from pathlib import Path

from scythe_transcribe import startup


def test_macos_program_arguments_launch_frozen_app_bundle(
    monkeypatch, tmp_path: Path
) -> None:
    """Frozen macOS apps should be launched through the bundle identity."""
    app_bundle = tmp_path / "Scythe-Transcribe.app"
    exe = app_bundle / "Contents" / "MacOS" / "scythe-transcribe"
    (app_bundle / "Contents" / "MacOS").mkdir(parents=True)
    (app_bundle / "Contents" / "Info.plist").write_text("<plist />", encoding="utf-8")
    exe.write_text("", encoding="utf-8")

    monkeypatch.setattr(startup.sys, "executable", str(exe))
    monkeypatch.setattr(startup.sys, "frozen", True, raising=False)

    assert startup._macos_program_arguments() == ["/usr/bin/open", "-g", str(app_bundle)]


def test_macos_program_arguments_preserve_dev_module_launch(monkeypatch) -> None:
    """Development runs should continue using the current Python executable."""
    monkeypatch.setattr(startup.sys, "executable", "/usr/local/bin/python3")
    monkeypatch.delattr(startup.sys, "frozen", raising=False)

    assert startup._macos_program_arguments() == [
        "/usr/local/bin/python3",
        "-m",
        "scythe_transcribe",
    ]


def test_plist_xml_writes_valid_program_arguments() -> None:
    """LaunchAgent plists should remain valid when paths need escaping."""
    payload = plistlib.loads(startup._plist_xml(["/tmp/Scythe & Tools/app"]).encode("utf-8"))

    assert payload["ProgramArguments"] == ["/tmp/Scythe & Tools/app"]
