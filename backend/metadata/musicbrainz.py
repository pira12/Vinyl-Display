"""MusicBrainz + Cover Art Archive lookups.

Free, no account required — MusicBrainz only asks for a descriptive
User-Agent and a courtesy rate limit of one request per second. Used to fetch
an album's tracklist (for "up next" and side ordering) and its cover art.
Responses are cached to disk so repeat plays don't hit the network.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger(__name__)

MB_BASE = "https://musicbrainz.org/ws/2"
CAA_BASE = "https://coverartarchive.org"


class MusicBrainzClient:
    def __init__(self, user_agent: str, cache_dir: str) -> None:
        self.user_agent = user_agent
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._last_request = 0.0
        self._lock = asyncio.Lock()

    # -- helpers -------------------------------------------------------------
    def _cache_path(self, kind: str, ident: str) -> Path:
        digest = hashlib.sha1(ident.encode()).hexdigest()[:16]
        return self.cache_dir / f"{kind}_{digest}.json"

    async def _get(self, url: str, params: Dict[str, Any]) -> Optional[dict]:
        # Be a good citizen: at most one MusicBrainz request per second.
        async with self._lock:
            wait = 1.0 - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()
            headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(url, params=params, headers=headers)
                    resp.raise_for_status()
                    return resp.json()
            except Exception as exc:  # noqa: BLE001
                log.warning("MusicBrainz request failed (%s): %s", url, exc)
                return None

    # -- public API ----------------------------------------------------------
    async def get_release(self, release_mbid: str) -> Optional[Dict[str, Any]]:
        """Return album info + a flat tracklist for a release MBID."""
        cache = self._cache_path("release", release_mbid)
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))

        data = await self._get(
            f"{MB_BASE}/release/{release_mbid}",
            {"inc": "recordings+artist-credits", "fmt": "json"},
        )
        if not data:
            return None

        artist = ""
        if data.get("artist-credit"):
            artist = "".join(
                ac.get("name", "") + ac.get("joinphrase", "")
                for ac in data["artist-credit"]
            )

        tracklist: List[Dict[str, Any]] = []
        for medium in data.get("media", []):
            for track in medium.get("tracks", []):
                rec = track.get("recording", {})
                tracklist.append(
                    {
                        "position": track.get("number"),
                        "number": track.get("position"),
                        "title": track.get("title") or rec.get("title"),
                        "length_ms": track.get("length") or rec.get("length"),
                        "recording_mbid": rec.get("id"),
                    }
                )

        result = {
            "release_mbid": release_mbid,
            "title": data.get("title", ""),
            "artist": artist,
            "year": (data.get("date") or "")[:4],
            "art_url": f"{CAA_BASE}/release/{release_mbid}/front-500",
            "tracklist": tracklist,
        }
        cache.write_text(json.dumps(result), encoding="utf-8")
        return result

    async def search_release(
        self, artist: str, album: str
    ) -> Optional[str]:
        """Find the most likely release MBID for an artist + album name."""
        query = f'release:"{album}" AND artist:"{artist}"'
        data = await self._get(
            f"{MB_BASE}/release",
            {"query": query, "fmt": "json", "limit": 5},
        )
        if not data or not data.get("releases"):
            return None
        return data["releases"][0]["id"]

    async def search_releases(self, query: str, limit: int = 12) -> List[Dict[str, Any]]:
        """Free-text release search for the companion app (artist + album).

        MusicBrainz returns one row per *pressing*, so a popular album shows up
        many times (different countries/editions). We collapse to one row per
        release-group, keeping the best edition (Official + has cover art + a
        date), and preserve MusicBrainz's relevance order across groups.
        """
        data = await self._get(
            f"{MB_BASE}/release",
            {"query": query, "fmt": "json", "limit": max(limit * 2, 25)},
        )
        groups: Dict[str, tuple[int, Dict[str, Any]]] = {}
        order: List[str] = []
        for rel in (data or {}).get("releases", []):
            rg_id = (rel.get("release-group") or {}).get("id") or rel["id"]
            rank = self._release_rank(rel)
            if rg_id not in groups:
                groups[rg_id] = (rank, self._release_row(rel))
                order.append(rg_id)
            elif rank > groups[rg_id][0]:
                groups[rg_id] = (rank, self._release_row(rel))
        return [groups[g][1] for g in order][:limit]

    @staticmethod
    def _release_rank(rel: Dict[str, Any]) -> int:
        """Higher is a better edition to represent its release-group."""
        rank = 0
        if (rel.get("status") or "").lower() == "official":
            rank += 4
        if (rel.get("cover-art-archive") or {}).get("front"):
            rank += 2
        if rel.get("date"):
            rank += 1
        return rank

    @staticmethod
    def _release_row(rel: Dict[str, Any]) -> Dict[str, Any]:
        artist = "".join(
            ac.get("name", "") + ac.get("joinphrase", "")
            for ac in rel.get("artist-credit", [])
        )
        return {
            "release_mbid": rel["id"],
            "title": rel.get("title", ""),
            "artist": artist,
            "year": (rel.get("date") or "")[:4],
            "tracks": rel.get("track-count"),
            "country": rel.get("country"),
            "art_url": f"{CAA_BASE}/release/{rel['id']}/front-250",
        }
