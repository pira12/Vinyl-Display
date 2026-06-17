"""Enrollment — build the fingerprint database from your own records.

Recognition is offline and matches against *your* collection, so each record is
enrolled once. Recording the reference from the same turntable makes the live
match far more reliable than a clean digital copy.

Typical flow:

    # 1. Record a side straight off the line-in (stop early with Ctrl-C):
    python -m backend.enroll record --out sideA.wav --minutes 25

    # 2. Split it into tracks, fingerprint them, and tag from MusicBrainz:
    python -m backend.enroll add sideA.wav --release <RELEASE_MBID> --side A

The split is by silence between bands; pass --tracks N if the auto-split is off.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from .config import load_config
from .metadata.musicbrainz import MusicBrainzClient
from .recognition.models import TrackIndex, TrackRef

log = logging.getLogger("enroll")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# -- recording ---------------------------------------------------------------
def record(out: str, minutes: float, cfg) -> None:
    import sounddevice as sd
    import soundfile as sf

    sr = cfg.audio.samplerate
    ch = cfg.audio.channels
    frames = int(sr * minutes * 60)
    print(f"Recording up to {minutes:g} min to {out} — press Ctrl-C to stop early.")
    buf: List[np.ndarray] = []
    try:
        with sd.InputStream(device=cfg.audio.device, samplerate=sr, channels=ch,
                            dtype="float32") as stream:
            captured = 0
            while captured < frames:
                block, _ = stream.read(sr)  # 1s blocks
                buf.append(block.copy())
                captured += len(block)
    except KeyboardInterrupt:
        print("\nStopped.")
    audio = np.concatenate(buf) if buf else np.zeros((0, ch), dtype=np.float32)
    sf.write(out, audio, sr)
    print(f"Wrote {len(audio) / sr:.1f}s to {out}")


# -- splitting ---------------------------------------------------------------
def split_by_silence(
    audio: np.ndarray,
    sr: int,
    silence_rms: float = 0.01,
    min_silence_s: float = 1.5,
    min_track_s: float = 30.0,
) -> List[Tuple[float, float]]:
    """Return (start_s, end_s) segments separated by sufficiently long silence."""
    mono = audio.mean(axis=1) if audio.ndim > 1 else audio
    win = int(sr * 0.1)  # 100ms windows
    n_win = len(mono) // win
    loud = np.array([
        np.sqrt(np.mean(np.square(mono[i * win:(i + 1) * win]))) > silence_rms
        for i in range(n_win)
    ])

    segments: List[Tuple[float, float]] = []
    start: Optional[int] = None
    silence_run = 0
    min_silence_win = int(min_silence_s / 0.1)
    for i, is_loud in enumerate(loud):
        if is_loud:
            if start is None:
                start = i
            silence_run = 0
        else:
            if start is not None:
                silence_run += 1
                if silence_run >= min_silence_win:
                    end = i - silence_run
                    if (end - start) * 0.1 >= min_track_s:
                        segments.append((start * 0.1, end * 0.1))
                    start = None
                    silence_run = 0
    if start is not None and (n_win - start) * 0.1 >= min_track_s:
        segments.append((start * 0.1, n_win * 0.1))
    return segments


# -- enrollment --------------------------------------------------------------
async def add_side(wav: str, release_mbid: str, side: str, cfg,
                   forced_tracks: Optional[int] = None) -> None:
    import soundfile as sf

    from .recognition.olaf import OlafRecognizer

    audio, sr = sf.read(wav, dtype="float32", always_2d=True)
    segments = split_by_silence(audio, sr, silence_rms=cfg.audio.silence_rms)
    print(f"Detected {len(segments)} track(s) in {wav}.")
    if forced_tracks and len(segments) != forced_tracks:
        print(f"WARNING: expected {forced_tracks}; auto-split found {len(segments)}. "
              "Re-run with a tuned --silence/--min-gap if this is wrong.")

    mb = MusicBrainzClient(cfg.metadata.musicbrainz_useragent, cfg.metadata.cache_dir)
    release = await mb.get_release(release_mbid)
    if not release:
        print("Could not fetch release from MusicBrainz; aborting.", file=sys.stderr)
        return

    # Tracks on this side, in order.
    side_tracks = [
        t for t in release["tracklist"]
        if str(t.get("position", "")).upper().startswith(side.upper())
    ] or release["tracklist"]

    index_path = Path(cfg.recognition.olaf_db).parent / "index.json"
    index = TrackIndex(str(index_path))
    olaf = OlafRecognizer(cfg.recognition.olaf_bin, cfg.recognition.olaf_db)
    refs_dir = Path(cfg.recognition.olaf_db).parent / "refs"
    refs_dir.mkdir(parents=True, exist_ok=True)

    for i, (start_s, end_s) in enumerate(segments):
        meta = side_tracks[i] if i < len(side_tracks) else {}
        title = meta.get("title", f"{side}{i + 1}")
        key = f"{_slug(release['title'])}-{_slug(str(meta.get('position') or side + str(i+1)))}"
        ref_wav = refs_dir / f"{key}.wav"
        sf.write(str(ref_wav), audio[int(start_s * sr):int(end_s * sr)], sr)
        olaf.store(str(ref_wav))

        index.add(TrackRef(
            key=key,
            title=title,
            artist=release["artist"],
            album=release["title"],
            release_mbid=release_mbid,
            recording_mbid=meta.get("recording_mbid"),
            track_number=meta.get("number"),
            position=meta.get("position"),
            duration_ms=int((end_s - start_s) * 1000),
        ))
        print(f"  + {meta.get('position') or key}: {title}")

    index.save()
    print(f"Enrolled {len(segments)} track(s). Index: {index_path}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description="Enroll records into the fingerprint DB")
    parser.add_argument("--config", default="config.yaml")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_rec = sub.add_parser("record", help="record a side off the line-in")
    p_rec.add_argument("--out", required=True)
    p_rec.add_argument("--minutes", type=float, default=30.0)

    p_add = sub.add_parser("add", help="split + fingerprint + tag a recorded side")
    p_add.add_argument("wav")
    p_add.add_argument("--release", required=True, help="MusicBrainz release MBID")
    p_add.add_argument("--side", default="A")
    p_add.add_argument("--tracks", type=int, default=None,
                       help="expected track count (sanity check)")

    args = parser.parse_args()
    cfg = load_config(args.config)

    if args.cmd == "record":
        record(args.out, args.minutes, cfg)
    elif args.cmd == "add":
        asyncio.run(add_side(args.wav, args.release, args.side, cfg, args.tracks))


if __name__ == "__main__":
    main()
