"""AcoustID auto-label: best-effort track identification with no enrollment.

We fingerprint a clip with Chromaprint's ``fpcalc`` and look it up against the
free AcoustID database, mapping the result to a MusicBrainz release so the rest
of the pipeline (tracklist, art, lyrics) can use it.

This is genuinely free, but Chromaprint is built for clean audio files, so
recognition from a room microphone is hit-or-miss. Treat a None result as
normal, not an error, and fall back to manual album search.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from typing import Any, Dict, Optional

import httpx

log = logging.getLogger(__name__)

LOOKUP_URL = "https://api.acoustid.org/v2/lookup"


class AcoustIDClient:
    def __init__(self, api_key: Optional[str]) -> None:
        self.api_key = api_key
        self.fpcalc = shutil.which("fpcalc")

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.fpcalc)

    def _fingerprint(self, wav_path: str) -> Optional[Dict[str, Any]]:
        try:
            proc = subprocess.run(
                [self.fpcalc, "-json", wav_path],
                check=True, capture_output=True, text=True, timeout=30,
            )
            data = json.loads(proc.stdout)
            return {"duration": int(data["duration"]), "fingerprint": data["fingerprint"]}
        except (subprocess.SubprocessError, KeyError, ValueError) as exc:
            log.warning("fpcalc failed: %s", exc)
            return None

    async def identify(self, wav_path: str) -> Optional[Dict[str, Any]]:
        """Return {release_mbid, title, artist, score} for the best match, or None."""
        if not self.available:
            return None
        fp = await _to_thread(self._fingerprint, wav_path)
        if fp is None:
            return None
        params = {
            "client": self.api_key,
            "duration": fp["duration"],
            "fingerprint": fp["fingerprint"],
            "meta": "recordings releases",
        }
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(LOOKUP_URL, params=params)
                resp.raise_for_status()
                body = resp.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("acoustid lookup failed: %s", exc)
            return None

        for result in body.get("results", []):
            score = result.get("score", 0)
            for rec in result.get("recordings", []) or []:
                releases = rec.get("releases") or []
                if not releases:
                    continue
                artist = ", ".join(a.get("name", "") for a in rec.get("artists", []))
                return {
                    "release_mbid": releases[0]["id"],
                    "title": rec.get("title", ""),
                    "artist": artist,
                    "score": score,
                }
        return None


async def _to_thread(fn, *args):
    import asyncio
    return await asyncio.to_thread(fn, *args)
