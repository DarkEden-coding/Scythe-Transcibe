"""Tests for the HTTP application routes."""

from __future__ import annotations

from fastapi.testclient import TestClient

from scythe_transcribe import hotkey_service, runtime_icon
from scythe_transcribe.web_app import create_app


def test_runtime_state_reports_capture_status(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_icon,
        "get_icon_status",
        lambda: {
            "base_state": "idle",
            "override_state": "processing",
            "display_state": "processing",
        },
    )
    monkeypatch.setattr(
        hotkey_service,
        "get_hotkey_listener_status",
        lambda: {"state": "running", "error": None, "capture_state": "recording"},
    )
    monkeypatch.setattr(hotkey_service, "start_hotkey_listener", lambda: None)

    client = TestClient(create_app())
    response = client.get("/api/runtime-state")

    assert response.status_code == 200
    assert response.json() == {
        "icon_state": "processing",
        "capture_state": "recording",
        "capturing_audio": True,
        "processing_audio": False,
        "os_icon": {
            "base_state": "idle",
            "override_state": "processing",
            "display_state": "processing",
        },
        "hotkey": {"state": "running", "error": None, "capture_state": "recording"},
    }


def test_runtime_icon_cycle_endpoint_updates_backend_override(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_icon,
        "cycle_icon_override",
        lambda: {
            "base_state": "idle",
            "override_state": "recording",
            "display_state": "recording",
        },
    )

    client = TestClient(create_app())
    response = client.post("/api/runtime-icon/cycle")

    assert response.status_code == 200
    assert response.json() == {
        "os_icon": {
            "base_state": "idle",
            "override_state": "recording",
            "display_state": "recording",
        }
    }
