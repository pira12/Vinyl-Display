"""Mock recognizer for development without hardware or an olaf database.

Walks through a fake two-track "record" in real time so the full pipeline
(state, metadata, lyrics, frontend, websocket) can be exercised end to end.
Enabled via ``recognition.backend: mock`` or the ``--simulate`` flag.
"""

from __future__ import annotations

import time
from typing import List, Optional

from .models import Match, TrackIndex, TrackRef

# A tiny built-in "record". The recognizer pretends these play back to back.
SIMULATED_TRACKS = [
    TrackRef(
        key="sim-a1",
        title="Tangled Up in Blue",
        artist="Bob Dylan",
        album="Blood on the Tracks",
        track_number=1,
        position="A1",
        duration_ms=341000,
    ),
    TrackRef(
        key="sim-a2",
        title="Simple Twist of Fate",
        artist="Bob Dylan",
        album="Blood on the Tracks",
        track_number=2,
        position="A2",
        duration_ms=247000,
    ),
]


class MockRecognizer:
    def __init__(self, index: Optional[TrackIndex] = None) -> None:
        self._start = time.monotonic()
        self.tracks: List[TrackRef] = SIMULATED_TRACKS
        # Ensure the index knows about the simulated tracks.
        if index is not None:
            for t in self.tracks:
                index.add(t)

    def query(self, wav_path: Optional[str] = None) -> Optional[Match]:
        elapsed = time.monotonic() - self._start
        cursor = 0.0
        for track in self.tracks:
            dur = (track.duration_ms or 0) / 1000.0
            if elapsed < cursor + dur:
                return Match(
                    key=track.key,
                    offset_seconds=elapsed - cursor,
                    score=99,
                )
            cursor += dur
        # Loop the record from the top.
        self._start = time.monotonic()
        return self.query()
