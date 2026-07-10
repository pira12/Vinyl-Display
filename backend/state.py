"""Now-playing state and websocket fan-out.

Holds the single source of truth for what the UI should show. Position is sent
as ``position_ms`` together with ``updated_at`` (server epoch ms); the frontend
advances its own clock between updates for smooth progress and lyric scrolling,
and snaps to the server value whenever a fresh recognition arrives.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Set

log = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


class StateManager:
    def __init__(self, speed_factor: float = 1.0) -> None:
        self.speed_factor = speed_factor
        self.listening: bool = True        # recognition loop on/off (user control)

        self.status: str = "idle"          # idle | listening | playing | unknown | paused | error
        self.track: Optional[Dict[str, Any]] = None
        self.album: Dict[str, Any] = {}
        self.tracklist: List[Dict[str, Any]] = []
        self.current_index: Optional[int] = None
        self.next_track: Optional[Dict[str, Any]] = None
        self.lyrics: Dict[str, Any] = {"synced": False, "lines": []}
        self.position_ms: int = 0
        self.updated_at: int = _now_ms()
        # Last (side_key, track_index) we locked on, so request-driven
        # recognition can tell "same track -> resync" from "new track".
        self.current_ident: Optional[tuple] = None
        # Consecutive failed recognitions. One miss during a quiet passage is
        # normal; a streak means the side ended or the needle lifted.
        self.miss_streak: int = 0
        # How many times in a row we've optimistically rolled to the next album
        # track without a recognition confirming it (bridges a missed boundary
        # so the display stays ongoing; capped so a stopped record can't run
        # through the whole side). Reset whenever a real match lands.
        self.predicted_advances: int = 0
        # An out-of-collection track seen once while a known album was playing.
        # A swap-boundary clip (old song + new song) can resolve to a bogus
        # one-off track; we only switch to it once a second read agrees.
        self.pending_external: Optional[tuple] = None

        self._listeners: Set[Any] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the event loop so non-async callers can trigger broadcasts."""
        self._loop = loop

    # -- listeners -----------------------------------------------------------
    def register(self, ws: Any) -> None:
        self._listeners.add(ws)

    def unregister(self, ws: Any) -> None:
        self._listeners.discard(ws)

    # -- mutations -----------------------------------------------------------
    def set_status(self, status: str) -> None:
        if status != self.status:
            self.status = status
            if status != "playing":
                self.track = None
                self.lyrics = {"synced": False, "lines": []}
            self._touch()
            self.publish()

    def set_now_playing(
        self,
        track: Dict[str, Any],
        album: Dict[str, Any],
        tracklist: List[Dict[str, Any]],
        current_index: Optional[int],
        next_track: Optional[Dict[str, Any]],
        lyrics: Dict[str, Any],
        position_ms: int,
    ) -> None:
        self.status = "playing"
        self.track = track
        self.album = album
        self.tracklist = tracklist
        self.current_index = current_index
        self.next_track = next_track
        self.lyrics = lyrics
        self.position_ms = position_ms
        self._touch()
        self.publish()

    def set_listening(self, enabled: bool) -> None:
        """Turn the recognition loop on or off (user control)."""
        enabled = bool(enabled)
        if enabled == self.listening:
            return
        self.listening = enabled
        if not enabled:
            self.status = "paused"
            self.track = None
            self.lyrics = {"synced": False, "lines": []}
        self._touch()
        self.publish()

    def resync(self, position_ms: int) -> None:
        """Update position for the current track without rebuilding metadata."""
        self.position_ms = position_ms
        self._touch()
        self.publish()

    def predicted_position_ms(self) -> int:
        """Where the play clock is now, extrapolated from the last update.

        Mirrors the frontend clock: last published position plus wall-clock
        elapsed since, scaled by the turntable speed factor.
        """
        elapsed = (_now_ms() - self.updated_at) * self.speed_factor
        return int(self.position_ms + max(0.0, elapsed))

    def _touch(self) -> None:
        self.updated_at = _now_ms()

    # -- serialization -------------------------------------------------------
    def payload(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "listening": self.listening,
            "updated_at": self.updated_at,
            "position_ms": self.position_ms,
            "speed_factor": self.speed_factor,
            "track": self.track,
            "album": self.album,
            "tracklist": self.tracklist,
            "current_index": self.current_index,
            "next_track": self.next_track,
            "lyrics": self.lyrics,
        }

    # -- fan-out -------------------------------------------------------------
    def publish(self) -> None:
        """Schedule a broadcast from any thread."""
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(self.broadcast())
        )

    async def broadcast(self) -> None:
        if not self._listeners:
            return
        payload = self.payload()
        dead = []
        for ws in list(self._listeners):
            try:
                await ws.send_json(payload)
            except Exception:  # noqa: BLE001 - connection dropped
                dead.append(ws)
        for ws in dead:
            self.unregister(ws)
