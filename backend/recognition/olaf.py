"""Olaf fingerprinting backend.

Olaf (https://github.com/JorenSix/Olaf) is a small, fast, self-hosted acoustic
fingerprinter that runs comfortably on a Raspberry Pi. We shell out to its CLI:

    olaf store  <file.wav>     # add a reference track to the database
    olaf query  <file.wav>     # identify a query clip + its offset in the match

References are stored under a filename we control (``<key>.wav``); a query
result reports the matched reference's path, whose stem we treat as the index
``key``. See ``backend.recognition.models.TrackIndex``.

NOTE: Olaf's textual query output has shifted slightly between versions. The
column layout is isolated in ``_parse_line`` / the ``COL_*`` constants below so
it is easy to adapt to your build (``olaf query`` once and eyeball the columns).
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import List, Optional

from .models import Match

log = logging.getLogger(__name__)

# Default column indices for `olaf query` CSV output.
# Adjust here if your Olaf version prints a different layout.
COL_MATCH_COUNT = 3      # number of matching fingerprints (used as score)
COL_REF_PATH = 6         # path of the matched reference audio
COL_REF_START = 7        # start time (s) of the match within the reference


class OlafRecognizer:
    def __init__(self, olaf_bin: str = "olaf", db_path: Optional[str] = None,
                 min_score: int = 5) -> None:
        self.olaf_bin = olaf_bin
        self.db_path = db_path
        self.min_score = min_score

    def _env_args(self) -> List[str]:
        # Olaf reads its DB location from a config/env; we pass it through the
        # OLAF_DB env var via the caller's environment when set.
        return []

    def store(self, wav_path: str) -> None:
        """Add a reference recording to the fingerprint database."""
        subprocess.run(
            [self.olaf_bin, "store", str(wav_path)],
            check=True,
            capture_output=True,
            text=True,
        )

    def query(self, wav_path: str) -> Optional[Match]:
        """Identify a query clip; return the best match above ``min_score``."""
        try:
            proc = subprocess.run(
                [self.olaf_bin, "query", str(wav_path)],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            log.warning("olaf query failed: %s", exc)
            return None

        best: Optional[Match] = None
        for line in proc.stdout.splitlines():
            match = self._parse_line(line)
            if match is None:
                continue
            if best is None or match.score > best.score:
                best = match

        if best is not None and best.score >= self.min_score:
            return best
        return None

    @staticmethod
    def _parse_line(line: str) -> Optional[Match]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) <= max(COL_MATCH_COUNT, COL_REF_PATH, COL_REF_START):
            return None
        try:
            score = int(float(parts[COL_MATCH_COUNT]))
            ref_path = parts[COL_REF_PATH]
            offset = float(parts[COL_REF_START])
        except (ValueError, IndexError):
            return None
        if not ref_path:
            return None
        key = Path(ref_path).stem
        return Match(key=key, offset_seconds=offset, score=score)
