"""The recognition loop — the heart of the system.

Runtime is fully offline: it matches the playing side against the local
fingerprint DB and reads cached lyrics + album art straight from the index
(both fetched once at enrollment). No network calls happen here.

Cadence is adaptive: it polls cheaply while the platter is silent, locks on
quickly when audio starts or a track changes (fast interval), then relaxes to
the slow interval to just correct clock drift.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from ..state import StateManager
from .models import Match, Resolved, TrackIndex

log = logging.getLogger(__name__)


def _art_url(art_path: Optional[str]) -> Optional[str]:
    return f"/art/{Path(art_path).name}" if art_path else None


def publish_resolved(state: StateManager, r: Resolved) -> None:
    """Push a resolved match into the shared state as the now-playing track."""
    log.info("now playing: %s — %s", r.album.artist, r.track.title)
    track = {
        "title": r.track.title,
        "artist": r.album.artist,
        "position": r.track.position,
        "number": r.track.number,
        "duration_ms": r.track.length_ms,
    }
    album = {
        "title": r.album.title,
        "artist": r.album.artist,
        "year": r.album.year,
        "art_url": _art_url(r.album.art_path),
    }
    tracklist = [
        {"position": t.position, "number": t.number, "title": t.title,
         "length_ms": t.length_ms}
        for t in r.album.tracklist
    ]
    next_track: Optional[Dict[str, Any]] = None
    if r.next_track is not None:
        next_track = {"title": r.next_track.title, "position": r.next_track.position}

    state.set_now_playing(
        track=track,
        album=album,
        tracklist=tracklist,
        current_index=r.index,
        next_track=next_track,
        lyrics=r.track.lyrics or {"synced": False, "lines": []},
        position_ms=r.position_ms,
    )


def publish_album_track(state: StateManager, album, idx: int,
                        position_ms: int = 0) -> None:
    """Show an album track on the display without an Olaf side reference.

    Used by AcoustID auto-label: we know the album + which track, but not the
    in-track position, so position starts at 0 (best-effort, no precise sync).
    """
    tl = album.tracklist
    if not tl:
        return
    idx = max(0, min(idx, len(tl) - 1))
    t = tl[idx]
    track = {
        "title": t.title,
        "artist": album.artist,
        "position": t.position,
        "number": t.number,
        "duration_ms": t.length_ms,
    }
    album_d = {
        "title": album.title,
        "artist": album.artist,
        "year": album.year,
        "art_url": _art_url(album.art_path),
    }
    tracklist = [
        {"position": x.position, "number": x.number, "title": x.title,
         "length_ms": x.length_ms}
        for x in tl
    ]
    next_track = None
    if idx + 1 < len(tl):
        next_track = {"title": tl[idx + 1].title, "position": tl[idx + 1].position}
    state.current_ident = None
    state.set_now_playing(
        track=track, album=album_d, tracklist=tracklist, current_index=idx,
        next_track=next_track, lyrics=t.lyrics or {"synced": False, "lines": []},
        position_ms=position_ms,
    )


def apply_match(state: StateManager, index: TrackIndex,
                match: Optional[Match]) -> Optional[Resolved]:
    """Resolve a match and update shared state, deduping repeat hits.

    Used by both the local recognition loop and the request-driven
    ``/api/recognize`` endpoint. Returns the resolved track, or None.
    """
    if match is None:
        if state.status != "playing":
            state.set_status("listening")
        state.current_ident = None
        return None

    offset_ms = int(match.offset_seconds * 1000)
    resolved = index.resolve(match.key, offset_ms)
    if resolved is None:
        state.set_status("unknown")
        state.current_ident = None
        return None

    ident = (match.key, resolved.index)
    if ident == state.current_ident:
        state.resync(resolved.position_ms)   # same track -> correct drift
        return resolved

    state.current_ident = ident
    publish_resolved(state, resolved)
    return resolved


class RecognitionService:
    def __init__(self, config, state: StateManager, index: TrackIndex,
                 backend, capture=None, tmp_dir: Optional[str] = None) -> None:
        self.cfg = config
        self.state = state
        self.index = index
        self.backend = backend
        self.capture = capture
        self.is_mock = config.recognition.backend == "mock"
        self._query_wav = Path(tmp_dir or ".") / "vinyl-query.wav"

    async def run(self) -> None:
        log.info("Recognition loop started (backend=%s)", self.cfg.recognition.backend)
        while True:
            try:
                locked = await self._tick()
            except Exception:  # noqa: BLE001 - never let the loop die
                log.exception("recognition tick failed")
                locked = False
            # Read the cadences each iteration so settings changes apply live.
            slow = self.cfg.recognition.interval_seconds
            fast = self.cfg.recognition.fast_interval_seconds
            await asyncio.sleep(fast if (self.is_mock or not locked) else slow)

    async def _tick(self) -> bool:
        """Run one recognition cycle. Returns True when locked on a track."""
        # Paused by the user => do no recognition until switched back on.
        if not self.state.listening:
            self.state.set_status("paused")
            self.state.current_ident = None
            return False

        # Silence => idle. Poll quickly so we notice audio resuming.
        if self.capture is not None and not self.is_mock:
            if self.capture.rms() < self.cfg.audio.silence_rms:
                self.state.set_status("idle")
                self.state.current_ident = None
                return False

        match = await self._recognize()
        return apply_match(self.state, self.index, match) is not None

    async def _recognize(self) -> Optional[Match]:
        if self.is_mock:
            return self.backend.query()
        if self.capture is None:
            return None
        await asyncio.to_thread(self._dump_query_wav)
        return await asyncio.to_thread(self.backend.query, str(self._query_wav))

    def _dump_query_wav(self) -> None:
        import soundfile as sf  # lazy

        audio = self.capture.get_recent(self.cfg.audio.query_seconds)
        self._query_wav.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(self._query_wav), audio, self.cfg.audio.samplerate)
