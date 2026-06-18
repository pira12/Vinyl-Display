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
from typing import Any, Dict, Optional, Tuple

from ..state import StateManager
from .models import Match, Resolved, TrackIndex

log = logging.getLogger(__name__)


def _art_url(art_path: Optional[str]) -> Optional[str]:
    return f"/art/{Path(art_path).name}" if art_path else None


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
        self._current: Optional[Tuple[str, int]] = None   # (side_key, track_index)

    async def run(self) -> None:
        slow = self.cfg.recognition.interval_seconds
        fast = self.cfg.recognition.fast_interval_seconds
        log.info("Recognition loop started (backend=%s)", self.cfg.recognition.backend)
        while True:
            try:
                locked = await self._tick()
            except Exception:  # noqa: BLE001 - never let the loop die
                log.exception("recognition tick failed")
                locked = False
            await asyncio.sleep(fast if (self.is_mock or not locked) else slow)

    async def _tick(self) -> bool:
        """Run one recognition cycle. Returns True when locked on a track."""
        # Silence => idle. Poll quickly so we notice audio resuming.
        if self.capture is not None and not self.is_mock:
            if self.capture.rms() < self.cfg.audio.silence_rms:
                self.state.set_status("idle")
                self._current = None
                return False

        match = await self._recognize()
        if match is None:
            if self.state.status != "playing":
                self.state.set_status("listening")
            self._current = None
            return False

        offset_ms = int(match.offset_seconds * 1000)
        resolved = self.index.resolve(match.key, offset_ms)
        if resolved is None:
            self.state.set_status("unknown")
            self._current = None
            return False

        ident = (match.key, resolved.index)
        if ident == self._current:
            self.state.resync(resolved.position_ms)   # same track -> correct drift
            return True

        self._current = ident
        self._publish(resolved)
        return True

    def _publish(self, r: Resolved) -> None:
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

        self.state.set_now_playing(
            track=track,
            album=album,
            tracklist=tracklist,
            current_index=r.index,
            next_track=next_track,
            lyrics=r.track.lyrics or {"synced": False, "lines": []},
            position_ms=r.position_ms,
        )

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
