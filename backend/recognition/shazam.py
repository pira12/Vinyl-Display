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

# When a recognition misses right at a track boundary, keep the album flowing by
# rolling to the next track instead of blanking. Only advance once the play
# clock is within this margin of the current track's end, and cap consecutive
# unconfirmed advances so a stopped record can't run through the whole side.
BOUNDARY_GRACE_MS = 800
MAX_PREDICTED_ADVANCES = 2


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


def _advance_within_album(state: StateManager, index: TrackIndex) -> bool:
    """Roll a locked-on collection album to its next track on a boundary miss.

    Returns True when it advanced (the display now shows the next track). Only
    fires while a collection album is playing, once the play clock has reached
    the current track's end, and up to ``MAX_PREDICTED_ADVANCES`` times before a
    real match must reconfirm — so a stopped record can't run through the side.
    """
    ident = state.current_ident
    if not (isinstance(ident, tuple) and len(ident) == 3 and ident[0] == "album"):
        return False
    if state.status != "playing" or state.predicted_advances >= MAX_PREDICTED_ADVANCES:
        return False

    _, album_id, idx = ident
    album = index.albums.get(album_id)
    if album is None or not album.tracklist or idx + 1 >= len(album.tracklist):
        return False  # unknown album or already on the last track
    length_ms = album.tracklist[idx].length_ms
    if not length_ms or state.predicted_position_ms() < length_ms - BOUNDARY_GRACE_MS:
        return False  # still inside the current track — a genuine miss

    publish_album_track(state, album, idx + 1, 0)
    state.current_ident = ("album", album_id, idx + 1)
    state.predicted_advances += 1
    state.miss_streak = 0
    log.info("optimistic advance to track %d/%d of %s (boundary miss)",
             idx + 2, len(album.tracklist), album.title)
    return True


async def apply_shazam(state: StateManager, index: TrackIndex,
                       result: Optional[ShazamResult], clip_seconds: float,
                       lyrics=None, lyrics_enabled: bool = True) -> bool:
    """Resolve a Shazam result against the collection and update shared state.

    Mirrors ``recognizer.apply_match``: repeat hits on the same track only
    resync the clock; a new track republishes full metadata. Returns True when
    something is playing.
    """
    if result is None:
        # A missed query at a track boundary shouldn't blank a known album —
        # optimistically roll to the next track and hold it until a real match
        # confirms or corrects.
        if _advance_within_album(state, index):
            return True
        note_miss(state)
        return False

    state.miss_streak = 0
    state.predicted_advances = 0  # a real recognition confirms reality
    position_ms: Optional[int] = None
    if result.offset_seconds is not None:
        # The offset marks where the clip *began* inside the track, and the
        # clip ends roughly "now", so playback is clip_seconds further along.
        position_ms = int((result.offset_seconds + clip_seconds) * 1000)

    hit = index.find_track(result.artist, result.title)
    if hit is not None:
        state.pending_external = None  # a solid in-collection read clears doubt
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
        state.pending_external = None
        return True

    # Switching to an out-of-collection track. If a known album is playing, a
    # swap-boundary clip can resolve to a bogus one-off match, so don't abandon
    # the album until this same track shows up twice. Meanwhile keep the album
    # flowing to its next track rather than flashing "something else".
    on_album = (isinstance(state.current_ident, tuple)
                and len(state.current_ident) == 3
                and state.current_ident[0] == "album")
    if on_album and state.pending_external != ident:
        state.pending_external = ident
        _advance_within_album(state, index)  # roll on if we're at the boundary
        return True  # hold the album; wait for a second read to confirm
    state.pending_external = None

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
