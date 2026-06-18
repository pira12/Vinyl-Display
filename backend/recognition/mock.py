"""Mock recognizer for development without hardware, network, or a real DB.

Builds a one-album / one-side index in memory and "plays" the side in real
time, returning the offset within the side — exactly like the real backend.
Includes cached synced lyrics so the Spotify-style view works fully offline.
Enabled via ``recognition.backend: mock`` or ``--simulate``.
"""

from __future__ import annotations

import time
from typing import Optional

from .models import Album, AlbumTrack, Match, Side, SideTrack, TrackIndex

_SIDE_KEY = "sim-blood-on-the-tracks-a"

_LYRICS_A1 = {
    "synced": True,
    "lines": [
        {"t": 0, "text": "Early one mornin' the sun was shinin'"},
        {"t": 4000, "text": "I was layin' in bed"},
        {"t": 8000, "text": "Wond'rin' if she'd changed at all"},
        {"t": 12000, "text": "If her hair was still red"},
        {"t": 16000, "text": "Her folks they said our lives together"},
        {"t": 20000, "text": "Sure was gonna be rough"},
        {"t": 24000, "text": "They never did like Mama's homemade dress"},
        {"t": 28000, "text": "Papa's bankbook wasn't big enough"},
    ],
}


def build_simulated_index(index: TrackIndex) -> None:
    """Populate the index with a fake record so the pipeline has data."""
    album = Album(
        id="sim-blood-on-the-tracks",
        title="Blood on the Tracks",
        artist="Bob Dylan",
        year="1975",
        tracklist=[
            AlbumTrack(title="Tangled Up in Blue", position="A1", number=1,
                       length_ms=341000, lyrics=_LYRICS_A1),
            AlbumTrack(title="Simple Twist of Fate", position="A2", number=2,
                       length_ms=247000),
        ],
    )
    index.add_album(album)
    index.add_side(Side(
        key=_SIDE_KEY,
        album_id=album.id,
        side="A",
        tracks=[
            SideTrack(album_track_index=0, start_ms=0),
            SideTrack(album_track_index=1, start_ms=341000),
        ],
    ))


class MockRecognizer:
    def __init__(self, index: Optional[TrackIndex] = None) -> None:
        self._start = time.monotonic()
        self._side_ms = 341000 + 247000  # total side length
        if index is not None:
            build_simulated_index(index)

    def query(self, wav_path: Optional[str] = None) -> Optional[Match]:
        elapsed = (time.monotonic() - self._start) * 1000.0
        if elapsed >= self._side_ms:
            self._start = time.monotonic()      # loop the side
            elapsed = 0.0
        return Match(key=_SIDE_KEY, offset_seconds=elapsed / 1000.0, score=99)
