"""Shared data types and the on-disk fingerprint index.

The index models a record collection as **albums** and **sides**:

* An ``Album`` holds the full tracklist plus offline-cached lyrics and a local
  album-art file (fetched once at enrollment).
* A ``Side`` is one continuous fingerprint reference (the whole side, not split
  into per-track clips). It stores the start offset of each track *within* the
  side, so a single recognition — which returns the side + an offset — tells us
  both which track is playing and how far into it we are.

This keeps recognition robust (no fragile per-track splitting) and the runtime
fully offline (lyrics + art are already on disk).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class Match:
    """A recognition result: which reference matched, and where in it we are."""

    key: str                  # index key of the matched side reference
    offset_seconds: float     # playback position within that side
    score: int                # match strength (recognizer-specific units)


@dataclass
class AlbumTrack:
    title: str = ""
    position: Optional[str] = None        # vinyl position label, e.g. "A3"
    number: Optional[int] = None          # ordinal within the album
    recording_mbid: Optional[str] = None
    length_ms: Optional[int] = None
    # Offline-cached lyrics: {"synced": bool, "lines": [{"t": ms, "text": ...}]}
    lyrics: Dict[str, Any] = field(default_factory=lambda: {"synced": False, "lines": []})


@dataclass
class Album:
    id: str
    title: str = ""
    artist: str = ""
    year: str = ""
    release_mbid: Optional[str] = None
    art_path: Optional[str] = None        # local cached image file
    tracklist: List[AlbumTrack] = field(default_factory=list)


@dataclass
class SideTrack:
    album_track_index: int                # index into Album.tracklist
    start_ms: int                         # offset of this track within the side


@dataclass
class Side:
    key: str                              # olaf reference key (filename stem)
    album_id: str
    side: str = ""                        # "A", "B", ...
    tracks: List[SideTrack] = field(default_factory=list)


@dataclass
class Resolved:
    """Everything the state layer needs for one recognized moment."""

    album: Album
    track: AlbumTrack
    index: int                            # current track's index in the album
    position_ms: int                      # position within the current track
    next_track: Optional[AlbumTrack]


class TrackIndex:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.albums: Dict[str, Album] = {}
        self.sides: Dict[str, Side] = {}
        self.load()

    # -- persistence ---------------------------------------------------------
    def load(self) -> None:
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        for aid, a in raw.get("albums", {}).items():
            tracks = [AlbumTrack(**t) for t in a.pop("tracklist", [])]
            self.albums[aid] = Album(tracklist=tracks, **a)
        for key, s in raw.get("sides", {}).items():
            st = [SideTrack(**t) for t in s.pop("tracks", [])]
            self.sides[key] = Side(tracks=st, **s)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "albums": {k: asdict(v) for k, v in self.albums.items()},
            "sides": {k: asdict(v) for k, v in self.sides.items()},
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # -- mutation ------------------------------------------------------------
    def add_album(self, album: Album) -> None:
        self.albums[album.id] = album

    def add_side(self, side: Side) -> None:
        self.sides[side.key] = side

    # -- lookup --------------------------------------------------------------
    def resolve(self, key: str, offset_ms: int) -> Optional[Resolved]:
        """Turn a (side, offset) match into the current track + position."""
        side = self.sides.get(key)
        if side is None:
            return None
        album = self.albums.get(side.album_id)
        if album is None or not side.tracks:
            return None

        ordered = sorted(side.tracks, key=lambda t: t.start_ms)
        current = ordered[0]
        for st in ordered:
            if st.start_ms <= offset_ms:
                current = st
            else:
                break

        idx = current.album_track_index
        if idx >= len(album.tracklist):
            return None
        track = album.tracklist[idx]
        next_track = album.tracklist[idx + 1] if idx + 1 < len(album.tracklist) else None
        return Resolved(
            album=album,
            track=track,
            index=idx,
            position_ms=max(0, offset_ms - current.start_ms),
            next_track=next_track,
        )
