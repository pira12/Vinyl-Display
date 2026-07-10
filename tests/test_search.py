"""Album search: famous-title-by-popularity, artist catalog, text fallback."""

import asyncio

from backend.metadata.musicbrainz import MusicBrainzClient


def _rg(rgid, title, artist, count=1, date="2000", ptype="Album", secondary=None):
    d = {
        "id": rgid,
        "title": title,
        "primary-type": ptype,
        "first-release-date": date,
        "count": count,
        "artist-credit": [{"name": artist}],
        "releases": [{"id": rgid + "-r"}],
    }
    if secondary:
        d["secondary-types"] = secondary
    return d


def _client(tmp_path, handler):
    mb = MusicBrainzClient("UA", str(tmp_path / "cache"))
    mb._get = handler
    return mb


def test_famous_titled_album_beats_same_named_band(tmp_path):
    # "thriller" is both an album and a tiny band; the 85-pressing Michael
    # Jackson album must win over the band's catalog.
    async def _get(url, params):
        query = params.get("query", "")
        if url.endswith("/release-group") and "artist:" not in query and query:
            return {"release-groups": [
                _rg("mj", "Thriller", "Michael Jackson", count=85, date="1982"),
                _rg("b1", "Street Metal", "Thriller", count=2, date="1984"),
            ]}
        if url.endswith("/artist"):
            return {"artists": [{"id": "tb", "name": "Thriller", "score": 100}]}
        if url.endswith("/release-group") and params.get("artist") == "tb":
            return {"release-groups": [_rg("s", "Street Metal", "Thriller")]}
        return {"release-groups": []}

    rows = asyncio.run(_client(tmp_path, _get).search_albums("thriller"))
    assert rows[0]["artist"] == "Michael Jackson"
    assert rows[0]["title"] == "Thriller"


def test_artist_name_returns_studio_catalog_newest_first(tmp_path):
    async def _get(url, params):
        if url.endswith("/artist"):
            return {"artists": [{"id": "aid", "name": "The Weeknd", "score": 100}]}
        if url.endswith("/release-group") and params.get("artist") == "aid":
            return {"release-groups": [
                _rg("r1", "After Hours", "The Weeknd", date="2020-03-20"),
                _rg("r2", "Live at SoFi", "The Weeknd", date="2023",
                    secondary=["Live"]),
                _rg("r3", "Dawn FM", "The Weeknd", date="2022-01-07"),
                _rg("r4", "Starboy", "The Weeknd", date="2016-11-25"),
            ]}
        return {"release-groups": []}  # no album titled "the weeknd"

    rows = asyncio.run(_client(tmp_path, _get).search_albums("the weeknd"))
    titles = [r["title"] for r in rows]
    assert titles == ["Dawn FM", "After Hours", "Starboy"]  # newest first, no Live


def test_title_fallback_orders_by_release_count(tmp_path):
    # Not famous enough to short-circuit, and not an artist — count still ranks.
    async def _get(url, params):
        query = params.get("query", "")
        if url.endswith("/release-group") and "artist:" not in query and query:
            return {"release-groups": [
                _rg("a", "Indie Thing", "Small Band", count=2),
                _rg("b", "Indie Thing", "Bigger Band", count=9),
            ]}
        return {"release-groups": []}

    rows = asyncio.run(_client(tmp_path, _get).search_albums("indie thing"))
    assert [r["artist"] for r in rows] == ["Bigger Band", "Small Band"]


def test_text_search_escapes_lucene_specials(tmp_path):
    seen = {}

    async def _get(url, params):
        seen["query"] = params.get("query", "")
        return {"release-groups": []}  # nothing anywhere -> reaches text search

    asyncio.run(_client(tmp_path, _get).search_albums("AC/DC"))
    assert seen["query"] == r"releasegroup:(AC\/DC) OR artist:(AC\/DC)"
