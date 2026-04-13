"""Tests for frontend activity tracking."""

from __future__ import annotations

import time

from scythe_transcribe.frontend_activity import FrontendActivity


def test_frontend_activity_tracks_and_closes_sessions() -> None:
    activity = FrontendActivity(session_ttl_seconds=10.0)

    activity.mark_seen("tab-1")
    assert activity.has_active_sessions()

    activity.close("tab-1")
    assert not activity.has_active_sessions()


def test_frontend_activity_expires_stale_sessions() -> None:
    activity = FrontendActivity(session_ttl_seconds=0.01)

    activity.mark_seen("tab-1")
    time.sleep(0.02)

    assert not activity.has_active_sessions()
