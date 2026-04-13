"""Track open frontend sessions so the settings server can sleep when idle."""

from __future__ import annotations

import threading
import time


class FrontendActivity:
    """Thread-safe registry of recently active browser sessions."""

    def __init__(self, *, session_ttl_seconds: float = 75.0) -> None:
        self._session_ttl_seconds = session_ttl_seconds
        self._lock = threading.Lock()
        self._sessions: dict[str, float] = {}
        self._last_seen = time.monotonic()

    def mark_seen(self, session_id: str) -> None:
        """Record that a frontend session is open or recently active."""
        sid = session_id.strip()
        if not sid:
            return
        now = time.monotonic()
        with self._lock:
            self._sessions[sid] = now
            self._last_seen = now

    def close(self, session_id: str) -> None:
        """Remove a frontend session when the page unloads."""
        sid = session_id.strip()
        if not sid:
            return
        now = time.monotonic()
        with self._lock:
            self._sessions.pop(sid, None)
            self._last_seen = now

    def has_active_sessions(self) -> bool:
        """Return whether any frontend has heartbeated within the TTL."""
        now = time.monotonic()
        with self._lock:
            self._purge_locked(now)
            return bool(self._sessions)

    def seconds_since_last_seen(self) -> float:
        """Seconds since the last frontend heartbeat or close event."""
        now = time.monotonic()
        with self._lock:
            self._purge_locked(now)
            return now - self._last_seen

    def reset_idle_clock(self) -> None:
        """Start a fresh idle window, used when the backend starts."""
        now = time.monotonic()
        with self._lock:
            self._last_seen = now

    def _purge_locked(self, now: float) -> None:
        stale_before = now - self._session_ttl_seconds
        stale = [sid for sid, seen in self._sessions.items() if seen < stale_before]
        for sid in stale:
            self._sessions.pop(sid, None)
