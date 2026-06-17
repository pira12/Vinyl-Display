"""Shared data types for recognition and the fingerprint index."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Optional


@dataclass
class Match:
    """A recognition result: which reference matched, and where we are in it."""

    key: str                  # index key of the matched reference track
    offset_seconds: float     # playback position within that track
    score: int                # match strength (recognizer-specific units)


@dataclass
class TrackRef:
    """Metadata for one enrolled track, keyed by its olaf reference key."""

    key: str
    title: str
    artist: str
    album: str = ""
    release_mbid: Optional[str] = None
    recording_mbid: Optional[str] = None
    track_number: Optional[int] = None
    position: Optional[str] = None        # vinyl position label, e.g. "A3"
    duration_ms: Optional[int] = None


class TrackIndex:
    """Maps olaf reference keys to track metadata, persisted as JSON.

    Built incrementally by the enrollment flow (`backend.enroll`) and read by
    the recognizer to turn a raw match into something the UI can display.
    """

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.tracks: Dict[str, TrackRef] = {}
        self.load()

    def load(self) -> None:
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.tracks = {
                k: TrackRef(**v) for k, v in raw.get("tracks", {}).items()
            }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"tracks": {k: asdict(v) for k, v in self.tracks.items()}}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def add(self, ref: TrackRef) -> None:
        self.tracks[ref.key] = ref

    def get(self, key: str) -> Optional[TrackRef]:
        return self.tracks.get(key)
