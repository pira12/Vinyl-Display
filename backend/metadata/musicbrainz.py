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
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger(__name__)

MB_BASE = "https://musicbrainz.org/ws/2"
CAA_BASE = "https://coverartarchive.org"

# Lucene metacharacters break MusicBrainz's query parser when a user types
# them ("AC/DC", "What's Going On?"). Escape them so free text stays free text.
_LUCENE_SPECIAL = re.compile(r'(&&|\|\||[+\-!(){}\[\]^"~*?:\\/])')


def _escape_lucene(query: str) -> str:
    return _LUCENE_SPECIAL.sub(r"\\\1", query)


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
            {"inc": "recordings+artist-credits+release-groups", "fmt": "json"},
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

        rg_mbid = (data.get("release-group") or {}).get("id")
        # Not every pressing has its own scan in the Cover Art Archive; the
        # release-group front image is the fallback.
        art_urls = [f"{CAA_BASE}/release/{release_mbid}/front-500"]
        if rg_mbid:
            art_urls.append(f"{CAA_BASE}/release-group/{rg_mbid}/front-500")

        result = {
            "release_mbid": release_mbid,
            "release_group_mbid": rg_mbid,
            "title": data.get("title", ""),
            "artist": artist,
            "year": (data.get("date") or "")[:4],
            "art_url": art_urls[0],
            "art_urls": art_urls,
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

    async def search_albums(self, query: str, limit: int = 12) -> List[Dict[str, Any]]:
        """Free-text album search for the companion app.

        Searches *release-groups* rather than releases: a release-group is
        "the album" while releases are its individual pressings, so results
        are inherently one-row-per-album with the canonical title and the
        original release year. MusicBrainz's relevance score is nudged so
        studio albums outrank live/compilation/remix variants of themselves.
        """
        data = await self._get(
            f"{MB_BASE}/release-group",
            {"query": _escape_lucene(query.strip()), "fmt": "json", "limit": 25},
        )
        ranked: List[tuple[int, int, Dict[str, Any]]] = []
        for pos, rg in enumerate((data or {}).get("release-groups", [])):
            releases = rg.get("releases") or []
            if not releases:
                continue  # nothing addable
            artist = "".join(
                ac.get("name", "") + ac.get("joinphrase", "")
                for ac in rg.get("artist-credit", [])
            )
            ptype = (rg.get("primary-type") or "").lower()
            bonus = {"album": 8, "ep": 4, "single": 2}.get(ptype, 0)
            bonus -= 3 * len(rg.get("secondary-types") or [])
            row = {
                "release_group_mbid": rg["id"],
                # Fallback for clients that add by concrete release; the add
                # path re-resolves the best pressing from the group anyway.
                "release_mbid": releases[0].get("id"),
                "title": rg.get("title", ""),
                "artist": artist,
                "year": (rg.get("first-release-date") or "")[:4],
                "type": ptype,
                "art_url": f"{CAA_BASE}/release-group/{rg['id']}/front-250",
            }
            ranked.append((int(rg.get("score", 0)) + bonus, pos, row))
        # Stable: score desc, then MusicBrainz's own order.
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [row for _, _, row in ranked[:limit]]

    async def best_release_for_group(self, rg_mbid: str) -> Optional[str]:
        """Pick the pressing of an album best suited to this app.

        Prefer Official releases on Vinyl (so track positions come out as
        A1/B2 and side grouping works), then dated ones, tie-broken by the
        earliest date (the original pressing, not a 40th-anniversary box).
        """
        data = await self._get(
            f"{MB_BASE}/release",
            {"release-group": rg_mbid, "inc": "media", "fmt": "json",
             "limit": 100},
        )
        releases = (data or {}).get("releases") or []
        if not releases:
            return None

        def rank(rel: Dict[str, Any]) -> tuple:
            score = 0
            if (rel.get("status") or "").lower() == "official":
                score += 8
            formats = " ".join(
                (m.get("format") or "") for m in rel.get("media") or []
            ).lower()
            if "vinyl" in formats:
                score += 4
            if rel.get("date"):
                score += 1
            # Higher score first; among equals the earliest date wins ("" last).
            return (-score, rel.get("date") or "9999")

        return sorted(releases, key=rank)[0].get("id")
