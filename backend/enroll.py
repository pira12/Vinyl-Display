"""Enrollment CLI (the companion app does the same thing from your phone).

    # add an album's metadata (tracklist + lyrics + art), cached offline:
    python -m backend.enroll album --release <RELEASE_MBID>

    # record a side off the line-in (Ctrl-C to stop), then fingerprint it:
    python -m backend.enroll record --out sideA.wav --minutes 25
    python -m backend.enroll add sideA.wav --release <RELEASE_MBID> --side A

    # search MusicBrainz to find a release MBID:
    python -m backend.enroll search "blood on the tracks dylan"
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
from typing import List

import numpy as np

from .config import load_config
from .enrollment import EnrollmentService
from .metadata.lyrics import LyricsClient
from .metadata.musicbrainz import MusicBrainzClient
from .recognition.models import TrackIndex
from .recognition.olaf import OlafRecognizer

log = logging.getLogger("enroll")


def _service(cfg) -> EnrollmentService:
    db_dir = Path(cfg.recognition.olaf_db).parent
    index = TrackIndex(str(db_dir / "index.json"))
    backend = OlafRecognizer(cfg.recognition.olaf_bin, cfg.recognition.olaf_db)
    mb = MusicBrainzClient(cfg.metadata.musicbrainz_useragent, cfg.metadata.cache_dir)
    lyrics = LyricsClient(cfg.metadata.musicbrainz_useragent)
    return EnrollmentService(cfg, index, backend, mb, lyrics,
                             capture=None, art_dir=str(db_dir / "art"))


def record(out: str, minutes: float, cfg) -> None:
    import sounddevice as sd
    import soundfile as sf

    sr, ch = cfg.audio.samplerate, cfg.audio.channels
    print(f"Recording up to {minutes:g} min to {out} — press Ctrl-C to stop early.")
    buf: List[np.ndarray] = []
    try:
        with sd.InputStream(device=cfg.audio.device, samplerate=sr, channels=ch,
                            dtype="float32") as stream:
            captured, target = 0, int(sr * minutes * 60)
            while captured < target:
                block, _ = stream.read(sr)
                buf.append(block.copy())
                captured += len(block)
    except KeyboardInterrupt:
        print("\nStopped.")
    audio = np.concatenate(buf) if buf else np.zeros((0, ch), dtype=np.float32)
    sf.write(out, audio, sr)
    print(f"Wrote {len(audio) / sr:.1f}s to {out}")


async def cmd_search(query: str, cfg) -> None:
    svc = _service(cfg)
    for r in await svc.search(query):
        print(f"  {r['release_mbid']}  {r['artist']} — {r['title']} "
              f"({r['year']}, {r.get('tracks')} trks, {r.get('country') or '?'})")


async def cmd_album(release: str, cfg) -> None:
    svc = _service(cfg)
    album = await svc.add_album(release)
    print(f"Added: {album['artist']} — {album['title']} "
          f"({album['track_count']} tracks). Sides: {svc.sides_for(release)}")


async def cmd_add(wav: str, release: str, side: str, cfg) -> None:
    import soundfile as sf

    svc = _service(cfg)
    if release not in svc.index.albums:
        await svc.add_album(release)
    audio, sr = sf.read(wav, dtype="float32", always_2d=True)
    result = svc.fingerprint_side(release, side, audio, sr)
    print(f"Enrolled side {side}: {result['tracks']} tracks "
          f"(offsets via {result['method']}).")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="Enroll records into the fingerprint DB")
    parser.add_argument("--config", default="config.yaml")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_s = sub.add_parser("search", help="search MusicBrainz for a release MBID")
    p_s.add_argument("query")

    p_al = sub.add_parser("album", help="cache an album's metadata/lyrics/art")
    p_al.add_argument("--release", required=True)

    p_rec = sub.add_parser("record", help="record a side off the line-in")
    p_rec.add_argument("--out", required=True)
    p_rec.add_argument("--minutes", type=float, default=30.0)

    p_add = sub.add_parser("add", help="fingerprint a recorded side + tag it")
    p_add.add_argument("wav")
    p_add.add_argument("--release", required=True)
    p_add.add_argument("--side", default="A")

    args = parser.parse_args()
    cfg = load_config(args.config)

    if args.cmd == "search":
        asyncio.run(cmd_search(args.query, cfg))
    elif args.cmd == "album":
        asyncio.run(cmd_album(args.release, cfg))
    elif args.cmd == "record":
        record(args.out, args.minutes, cfg)
    elif args.cmd == "add":
        asyncio.run(cmd_add(args.wav, args.release, args.side, cfg))


if __name__ == "__main__":
    main()
