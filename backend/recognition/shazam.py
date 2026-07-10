"""Shazam recognition backend — zero-setup identification, no enrollment.

Queries the free Shazam service via ``shazamio`` with the same short mic clip
the iPad already posts. Shazam is built for exactly this input (noisy room
audio) and returns the track *and* the offset where the clip sits inside it,
which is what drives the synced progress bar and scrolling lyrics.

The result is then mapped back onto the user's collection (albums added in
Collection mode) by fuzzy title/artist match, which restores everything Shazam
alone can't give us: the album's tracklist, "up next", and the lyrics + art
cached at add time. A track that isn't in the collection still displays, with
art from Shazam and lyrics fetched on the fly from LRCLIB.

Trade-off vs the Olaf backend: recognition needs internet and rides an
unofficial API, but the user never has to record their records first.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from ..state import StateManager
from .models import TrackIndex
from .recognizer import note_miss, publish_album_track

log = logging.getLogger(__name__)

RECOGNIZE_TIMEOUT_S = 20


@dataclass
class ShazamResult:
    """One identified track, as reported by Shazam."""

    title: str
    artist: str = ""
    album: str = ""
    year: str = ""
    art_url: Optional[str] = None
    # Offset (s) of the *start of the query clip* within the matched track,
    # when Shazam reports one. None means "track known, position unknown".
    offset_seconds: Optional[float] = None


class ShazamRecognizer:
    """Async recognizer; presence of ``recognize`` selects the Shazam path.

    Deliberately has no ``store``: enrollment/recording is meaningless for
    this backend, and its absence is what hides the recording UI.
    """

    def __init__(self) -> None:
        self._shazam = None

    async def recognize(self, wav_path: str) -> Optional[ShazamResult]:
        try:
            from shazamio import Shazam  # lazy: optional dependency
        except ImportError:
            log.error("shazamio is not installed; shazam backend cannot run")
            return None
        if self._shazam is None:
            self._shazam = Shazam()
        try:
            out = await asyncio.wait_for(
                self._shazam.recognize(wav_path), timeout=RECOGNIZE_TIMEOUT_S
            )
        except Exception as exc:  # noqa: BLE001 - network/API hiccups are normal
            log.warning("shazam query failed: %s", exc)
            return None
        return parse_response(out)


def parse_response(out: Any) -> Optional[ShazamResult]:
    """Extract the fields we use from a raw Shazam tag response."""
    if not isinstance(out, dict):
        return None
    track = out.get("track") or {}
    title = track.get("title")
    if not title:
        return None

    album = year = ""
    for section in track.get("sections") or []:
        for meta in section.get("metadata") or []:
            if meta.get("title") == "Album":
                album = meta.get("text") or ""
            elif meta.get("title") == "Released":
                year = meta.get("text") or ""

    offset: Optional[float] = None
    matches = out.get("matches") or []
    if matches and isinstance(matches[0], dict):
        raw = matches[0].get("offset")
        if isinstance(raw, (int, float)) and raw >= 0:
            offset = float(raw)

    return ShazamResult(
        title=title,
        artist=track.get("subtitle") or "",
        album=album,
        year=year,
        art_url=(track.get("images") or {}).get("coverart"),
        offset_seconds=offset,
    )


def publish_external_track(state: StateManager, r: ShazamResult,
                           lyrics: Dict[str, Any],
                           duration_ms: Optional[int],
                           position_ms: int) -> None:
    """Show a track that isn't in the collection (Shazam metadata only)."""
    log.info("now playing (not in collection): %s — %s", r.artist, r.title)
    state.set_now_playing(
        track={"title": r.title, "artist": r.artist, "position": None,
               "number": None, "duration_ms": duration_ms},
        album={"title": r.album, "artist": r.artist, "year": r.year,
               "art_url": r.art_url, "in_collection": False},
        tracklist=[],
        current_index=None,
        next_track=None,
        lyrics=lyrics,
        position_ms=position_ms,
    )


async def apply_shazam(state: StateManager, index: TrackIndex,
                       result: Optional[ShazamResult], clip_seconds: float,
                       lyrics=None, lyrics_enabled: bool = True) -> bool:
    """Resolve a Shazam result against the collection and update shared state.

    Mirrors ``recognizer.apply_match``: repeat hits on the same track only
    resync the clock; a new track republishes full metadata. Returns True when
    something is playing.
    """
    if result is None:
        note_miss(state)
        return False

    state.miss_streak = 0
    position_ms: Optional[int] = None
    if result.offset_seconds is not None:
        # The offset marks where the clip *began* inside the track, and the
        # clip ends roughly "now", so playback is clip_seconds further along.
        position_ms = int((result.offset_seconds + clip_seconds) * 1000)

    hit = index.find_track(result.artist, result.title)
    if hit is not None:
        album, idx = hit
        length_ms = album.tracklist[idx].length_ms
        if position_ms is not None and length_ms:
            position_ms = min(position_ms, length_ms)
        ident = ("album", album.id, idx)
        if ident == state.current_ident:
            if position_ms is not None:
                state.resync(position_ms)
            return True
        publish_album_track(state, album, idx, position_ms or 0)
        state.current_ident = ident
        return True

    ident = ("external", result.artist.lower(), result.title.lower())
    if ident == state.current_ident:
        if position_ms is not None:
            state.resync(position_ms)
        return True

    lyr: Dict[str, Any] = {"synced": False, "lines": []}
    duration_ms: Optional[int] = None
    if lyrics is not None and lyrics_enabled:
        try:
            lyr = await lyrics.get(result.artist, result.title, result.album)
        except Exception:  # noqa: BLE001 - lyrics are best-effort
            pass
        duration_ms = lyr.pop("duration_ms", None)
    publish_external_track(state, result, lyr, duration_ms, position_ms or 0)
    state.current_ident = ident
    return True
