"""The recognition loop — the heart of the system.

On a fixed cadence it:
  1. checks the input level (silence => idle / needle up),
  2. asks the recognizer backend what's playing and where we are in it,
  3. on a *new* track, fetches album metadata ("up next") and synced lyrics,
  4. on the *same* track, re-syncs the play clock to correct turntable drift.

All blocking work (olaf subprocess, WAV writing) is pushed off the event loop
with ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..metadata.lyrics import LyricsClient
from ..metadata.musicbrainz import MusicBrainzClient
from ..state import StateManager
from .models import Match, TrackIndex, TrackRef

log = logging.getLogger(__name__)


class RecognitionService:
    def __init__(
        self,
        config,
        state: StateManager,
        index: TrackIndex,
        backend,
        capture=None,
        mb_client: Optional[MusicBrainzClient] = None,
        lyrics_client: Optional[LyricsClient] = None,
    ) -> None:
        self.cfg = config
        self.state = state
        self.index = index
        self.backend = backend
        self.capture = capture
        self.mb = mb_client
        self.lyrics_client = lyrics_client
        self.is_mock = config.recognition.backend == "mock"
        self._query_wav = Path(config.metadata.cache_dir) / "query.wav"

    async def run(self) -> None:
        log.info("Recognition loop started (backend=%s)", self.cfg.recognition.backend)
        while True:
            try:
                await self._tick()
            except Exception:  # noqa: BLE001 - never let the loop die
                log.exception("recognition tick failed")
            await asyncio.sleep(self.cfg.recognition.interval_seconds)

    async def _tick(self) -> None:
        # 1. Silence detection (skip for the mock backend, which has no audio).
        if self.capture is not None and not self.is_mock:
            if self.capture.rms() < self.cfg.audio.silence_rms:
                self.state.set_status("idle")
                return

        # 2. Recognize.
        match = await self._recognize()
        if match is None:
            # A transient miss mid-track is normal; keep showing the track.
            if self.state.status != "playing":
                self.state.set_status("listening")
            return

        ref = self.index.get(match.key)
        if ref is None:
            log.info("matched unknown key %s", match.key)
            self.state.set_status("unknown")
            return

        offset_ms = int(match.offset_seconds * 1000)
        if self.state.track is not None and self.state.track.key == ref.key:
            self.state.resync(offset_ms)        # same track -> just correct drift
        else:
            await self._load_track(ref, offset_ms)

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

    async def _load_track(self, ref: TrackRef, offset_ms: int) -> None:
        log.info("now playing: %s — %s", ref.artist, ref.title)
        album, tracklist, current_index = await self._resolve_album(ref)
        next_track = None
        if current_index is not None and current_index + 1 < len(tracklist):
            next_track = tracklist[current_index + 1]

        lyrics: Dict[str, Any] = {"synced": False, "lines": []}
        if self.cfg.lyrics.enabled and self.lyrics_client is not None:
            duration_s = (ref.duration_ms or 0) / 1000.0 or None
            lyrics = await self.lyrics_client.get(
                ref.artist, ref.title, ref.album, duration_s
            )

        self.state.set_now_playing(
            track=ref,
            album=album,
            tracklist=tracklist,
            current_index=current_index,
            next_track=next_track,
            lyrics=lyrics,
            position_ms=offset_ms,
        )

    async def _resolve_album(self, ref: TrackRef):
        """Return (album dict, tracklist, current_index).

        Prefers MusicBrainz; falls back to a tracklist assembled from the local
        index so the app works fully offline (and in mock mode).
        """
        if self.mb is not None:
            mbid = ref.release_mbid
            if mbid is None and ref.artist and ref.album:
                mbid = await self.mb.search_release(ref.artist, ref.album)
            if mbid:
                release = await self.mb.get_release(mbid)
                if release:
                    album = {
                        "title": release["title"],
                        "artist": release["artist"],
                        "year": release.get("year", ""),
                        "art_url": release.get("art_url"),
                    }
                    tracklist = release["tracklist"]
                    idx = self._locate(ref, tracklist)
                    return album, tracklist, idx

        return self._local_album(ref)

    def _local_album(self, ref: TrackRef):
        siblings: List[TrackRef] = [
            t for t in self.index.tracks.values()
            if t.album == ref.album and t.artist == ref.artist
        ]
        siblings.sort(key=lambda t: (t.track_number or 0))
        tracklist = [
            {
                "position": t.position,
                "number": t.track_number,
                "title": t.title,
                "length_ms": t.duration_ms,
                "recording_mbid": t.recording_mbid,
                "key": t.key,
            }
            for t in siblings
        ]
        current_index = next(
            (i for i, t in enumerate(siblings) if t.key == ref.key), None
        )
        album = {
            "title": ref.album,
            "artist": ref.artist,
            "year": "",
            "art_url": None,
        }
        return album, tracklist, current_index

    @staticmethod
    def _locate(ref: TrackRef, tracklist: List[Dict[str, Any]]) -> Optional[int]:
        for i, t in enumerate(tracklist):
            if ref.recording_mbid and t.get("recording_mbid") == ref.recording_mbid:
                return i
        for i, t in enumerate(tracklist):
            if ref.position and t.get("position") == ref.position:
                return i
        for i, t in enumerate(tracklist):
            if t.get("title", "").lower() == ref.title.lower():
                return i
        return None
