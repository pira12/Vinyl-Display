"""Enrollment service — add records and fingerprint their sides.

Shared by the phone companion app (HTTP API) and the CLI. Splits cleanly into
two halves:

* **Metadata** (works from anywhere, incl. your phone): search MusicBrainz, then
  add an album — caching its tracklist, album art, and synced lyrics to disk so
  the runtime is fully offline.
* **Audio** (must happen at the Pi, on the turntable): record a side straight off
  the line-in and fingerprint it as one continuous reference, storing each
  track's start offset within the side.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from .audio.split import split_by_silence
from .metadata.lyrics import LyricsClient
from .metadata.musicbrainz import MusicBrainzClient
from .recognition.models import Album, AlbumTrack, Side, SideTrack, TrackIndex

log = logging.getLogger(__name__)

# MusicBrainz IDs are UUIDs. Validating before they touch file paths / the index
# prevents path traversal (the id becomes an art filename and an index key).
_MBID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


class EnrollmentService:
    def __init__(self, cfg, index: TrackIndex, backend, mb: MusicBrainzClient,
                 lyrics: LyricsClient, capture=None, art_dir: str = "") -> None:
        self.cfg = cfg
        self.index = index
        self.backend = backend            # OlafRecognizer (has .store) or mock
        self.mb = mb
        self.lyrics = lyrics
        self.capture = capture
        self.art_dir = Path(art_dir) if art_dir else Path(".")
        self.refs_dir = Path(cfg.recognition.olaf_db).parent / "refs"
        self._session: Optional[Dict[str, str]] = None   # active recording

    # -- metadata (phone-friendly) ------------------------------------------
    async def search(self, query: str) -> List[Dict[str, Any]]:
        return await self.mb.search_releases(query)

    async def add_album(self, release_mbid: str) -> Dict[str, Any]:
        if not _MBID_RE.match(release_mbid or ""):
            raise ValueError("invalid release MBID")
        release = await self.mb.get_release(release_mbid)
        if not release:
            raise ValueError("could not fetch release from MusicBrainz")

        tracklist: List[AlbumTrack] = []
        for t in release["tracklist"]:
            lyr = {"synced": False, "lines": []}
            if self.cfg.lyrics.enabled:
                dur = (t.get("length_ms") or 0) / 1000.0 or None
                lyr = await self.lyrics.get(
                    release["artist"], t["title"], release["title"], dur
                )
            tracklist.append(AlbumTrack(
                title=t["title"],
                position=t.get("position"),
                number=t.get("number"),
                recording_mbid=t.get("recording_mbid"),
                length_ms=t.get("length_ms"),
                lyrics=lyr,
            ))

        art_path = await self._download_art(release_mbid, release.get("art_url"))
        album = Album(
            id=release_mbid,
            title=release["title"],
            artist=release["artist"],
            year=release.get("year", ""),
            release_mbid=release_mbid,
            art_path=art_path,
            tracklist=tracklist,
        )
        self.index.add_album(album)
        self.index.save()
        log.info("added album: %s — %s", album.artist, album.title)
        return self.album_summary(album)

    async def _download_art(self, album_id: str, url: Optional[str]) -> Optional[str]:
        if not url:
            return None
        dest = self.art_dir / f"{album_id}.jpg"
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                self.art_dir.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(resp.content)
                return str(dest)
        except Exception as exc:  # noqa: BLE001
            log.warning("art download failed for %s: %s", album_id, exc)
            return None

    # -- collection view -----------------------------------------------------
    def album_summary(self, album: Album) -> Dict[str, Any]:
        enrolled = sorted({
            s.side for s in self.index.sides.values() if s.album_id == album.id
        })
        return {
            "id": album.id,
            "title": album.title,
            "artist": album.artist,
            "year": album.year,
            "art_url": f"/art/{Path(album.art_path).name}" if album.art_path else None,
            "track_count": len(album.tracklist),
            "tracklist": [
                {"position": t.position, "title": t.title,
                 "has_lyrics": bool(t.lyrics.get("lines"))}
                for t in album.tracklist
            ],
            "enrolled_sides": enrolled,
        }

    def collection(self) -> List[Dict[str, Any]]:
        return [self.album_summary(a) for a in self.index.albums.values()]

    def sides_for(self, album_id: str) -> List[str]:
        """Distinct side labels present in an album's tracklist (e.g. A, B)."""
        album = self.index.albums.get(album_id)
        if not album:
            return []
        sides = []
        for t in album.tracklist:
            label = str(t.position or "")
            if label and label[0].isalpha() and label[0].upper() not in sides:
                sides.append(label[0].upper())
        return sides or ["A"]

    # -- audio enrollment (at the Pi) ---------------------------------------
    def can_record(self) -> bool:
        return self.capture is not None and hasattr(self.backend, "store")

    def recording_status(self) -> Dict[str, Any]:
        rec = bool(self.capture and self.capture.is_recording)
        return {"recording": rec, "session": self._session, "can_record": self.can_record()}

    def start_recording(self, album_id: str, side: str) -> None:
        if not self.can_record():
            raise RuntimeError("recording needs an audio device and the olaf backend")
        if album_id not in self.index.albums:
            raise ValueError("unknown album; add it first")
        self._session = {"album_id": album_id, "side": side.upper()}
        self.capture.start_recording()
        log.info("recording side %s of %s", side, album_id)

    def stop_recording(self) -> Dict[str, Any]:
        if self._session is None:
            raise RuntimeError("no active recording")
        sess = self._session
        self._session = None
        audio = self.capture.stop_recording()
        return self.fingerprint_side(
            sess["album_id"], sess["side"], audio, self.cfg.audio.samplerate
        )

    def cancel_recording(self) -> None:
        self._session = None
        if self.capture is not None:
            self.capture.stop_recording()

    def fingerprint_side(self, album_id: str, side: str, audio, sr: int) -> Dict[str, Any]:
        """Fingerprint a recorded side and record per-track start offsets."""
        import soundfile as sf  # lazy

        album = self.index.albums.get(album_id)
        if album is None:
            raise ValueError("unknown album")

        side_indices = [
            i for i, t in enumerate(album.tracklist)
            if str(t.position or "").upper().startswith(side.upper())
        ] or list(range(len(album.tracklist)))

        segments = split_by_silence(audio, sr, silence_rms=self.cfg.audio.silence_rms)
        if len(segments) == len(side_indices):
            starts = [int(s * 1000) for s, _ in segments]
            method = "silence"
        else:
            # Fall back to cumulative MusicBrainz track lengths.
            starts, cum = [], 0
            for i in side_indices:
                starts.append(cum)
                cum += album.tracklist[i].length_ms or 0
            method = "lengths"

        key = f"{_slug(album.title)}-side-{side.lower()}"
        self.refs_dir.mkdir(parents=True, exist_ok=True)
        ref_wav = self.refs_dir / f"{key}.wav"
        sf.write(str(ref_wav), audio, sr)
        self.backend.store(str(ref_wav))

        self.index.add_side(Side(
            key=key,
            album_id=album_id,
            side=side.upper(),
            tracks=[
                SideTrack(album_track_index=i, start_ms=starts[j])
                for j, i in enumerate(side_indices)
            ],
        ))
        self.index.save()
        log.info("enrolled side %s of %s (%d tracks, via %s)",
                 side, album.title, len(side_indices), method)
        return {"key": key, "tracks": len(side_indices), "method": method,
                "duration_s": round(len(audio) / sr, 1)}
