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

        self.status: str = "idle"          # idle | listening | playing | unknown | error
        self.track: Optional[Dict[str, Any]] = None
        self.album: Dict[str, Any] = {}
        self.tracklist: List[Dict[str, Any]] = []
        self.current_index: Optional[int] = None
        self.next_track: Optional[Dict[str, Any]] = None
        self.lyrics: Dict[str, Any] = {"synced": False, "lines": []}
        self.position_ms: int = 0
        self.updated_at: int = _now_ms()

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

    def resync(self, position_ms: int) -> None:
        """Update position for the current track without rebuilding metadata."""
        self.position_ms = position_ms
        self._touch()
        self.publish()

    def _touch(self) -> None:
        self.updated_at = _now_ms()

    # -- serialization -------------------------------------------------------
    def payload(self) -> Dict[str, Any]:
        return {
            "status": self.status,
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
