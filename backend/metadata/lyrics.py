"""Time-synced lyrics from LRCLIB.

LRCLIB (https://lrclib.net) is free and needs no account or API key. We ask for
synced (LRC) lyrics by artist/title/album/duration and parse them into a list
of ``{"t": <ms>, "text": ...}`` lines the frontend can scroll in time. Falls
back to plain (unsynced) lyrics, then to nothing.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger(__name__)

LRCLIB_BASE = "https://lrclib.net/api"
_LRC_LINE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\](.*)")


def parse_lrc(lrc: str) -> List[Dict[str, Any]]:
    """Parse an LRC string into timestamped lines, sorted by time."""
    lines: List[Dict[str, Any]] = []
    for raw in lrc.splitlines():
        # A single source line can carry multiple [mm:ss.xx] timestamps.
        stamps = re.findall(r"\[(\d+):(\d+(?:\.\d+)?)\]", raw)
        text = re.sub(r"\[(\d+):(\d+(?:\.\d+)?)\]", "", raw).strip()
        for mm, ss in stamps:
            t = int((int(mm) * 60 + float(ss)) * 1000)
            lines.append({"t": t, "text": text})
    lines.sort(key=lambda x: x["t"])
    return lines


class LyricsClient:
    def __init__(self, user_agent: str = "VinylDisplay/0.1") -> None:
        self.user_agent = user_agent

    async def get(
        self,
        artist: str,
        title: str,
        album: str = "",
        duration_s: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Return ``{"synced": bool, "lines": [...]}`` for a track."""
        params: Dict[str, Any] = {
            "artist_name": artist,
            "track_name": title,
        }
        if album:
            params["album_name"] = album
        if duration_s:
            params["duration"] = int(duration_s)

        data = await self._request("/get", params)
        if data is None:
            # Retry without the strict duration match via search.
            data = await self._search(artist, title)

        if not data:
            return {"synced": False, "lines": []}

        if data.get("syncedLyrics"):
            return {"synced": True, "lines": parse_lrc(data["syncedLyrics"])}
        if data.get("plainLyrics"):
            lines = [
                {"t": None, "text": line}
                for line in data["plainLyrics"].splitlines()
            ]
            return {"synced": False, "lines": lines}
        return {"synced": False, "lines": []}

    async def _request(self, path: str, params: Dict[str, Any]) -> Optional[dict]:
        headers = {"User-Agent": self.user_agent}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{LRCLIB_BASE}{path}", params=params, headers=headers
                )
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("LRCLIB request failed: %s", exc)
            return None

    async def _search(self, artist: str, title: str) -> Optional[dict]:
        results = await self._request(
            "/search", {"artist_name": artist, "track_name": title}
        )
        if isinstance(results, list) and results:
            return results[0]
        return None
