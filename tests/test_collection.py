"""Tests for collection management: search dedup, delete, edit, re-record."""

import asyncio

import numpy as np

from backend.config import load_config
from backend.metadata.musicbrainz import MusicBrainzClient
from backend.recognition.models import TrackIndex


# -- album search (release-group based) -----------------------------------------
def _mb(tmp_path, payload):
    mb = MusicBrainzClient("UA", str(tmp_path / "cache"))

    async def fake_get(url, params):
        return payload

    mb._get = fake_get  # type: ignore[assignment]
    return mb


def test_search_albums_ranks_studio_albums_over_variants(tmp_path):
    mb = _mb(tmp_path, {"release-groups": [
        {"id": "rg-live", "title": "Album (Live)", "score": 100,
         "primary-type": "Album", "secondary-types": ["Live"],
         "artist-credit": [{"name": "A"}], "releases": [{"id": "r-live"}],
         "first-release-date": "2002-01-01"},
        {"id": "rg-studio", "title": "Album", "score": 100,
         "primary-type": "Album", "artist-credit": [{"name": "A"}],
         "releases": [{"id": "r-studio"}], "first-release-date": "2000-05-01"},
    ]})
    rows = asyncio.run(mb.search_albums("album"))
    assert rows[0]["release_group_mbid"] == "rg-studio"   # live variant demoted
    assert rows[0]["year"] == "2000"
    assert rows[0]["artist"] == "A"
    assert rows[0]["release_mbid"] == "r-studio"          # addable fallback id


def test_search_albums_skips_groups_without_releases(tmp_path):
    mb = _mb(tmp_path, {"release-groups": [
        {"id": "rg1", "title": "Ghost", "score": 100, "primary-type": "Album",
         "artist-credit": [{"name": "A"}], "releases": []},
        {"id": "rg2", "title": "Real", "score": 90, "primary-type": "Album",
         "artist-credit": [{"name": "A"}], "releases": [{"id": "r1"}]},
    ]})
    rows = asyncio.run(mb.search_albums("x"))
    assert [r["release_group_mbid"] for r in rows] == ["rg2"]


def test_best_release_prefers_official_vinyl_earliest(tmp_path):
    mb = _mb(tmp_path, {"releases": [
        {"id": "cd-late", "status": "Official", "date": "1994",
         "media": [{"format": "CD"}]},
        {"id": "vinyl-orig", "status": "Official", "date": "1973-03-01",
         "media": [{"format": "12\" Vinyl"}]},
        {"id": "vinyl-reissue", "status": "Official", "date": "2016",
         "media": [{"format": "Vinyl"}]},
        {"id": "bootleg", "status": "Bootleg", "date": "1972",
         "media": [{"format": "Vinyl"}]},
    ]})
    assert asyncio.run(mb.best_release_for_group("rg")) == "vinyl-orig"


def test_lucene_metacharacters_are_escaped():
    from backend.metadata.musicbrainz import _escape_lucene
    assert _escape_lucene("AC/DC") == "AC\\/DC"
    assert _escape_lucene('what "is" this?') == 'what \\"is\\" this\\?'


# -- enrollment service helpers ------------------------------------------------
def _enrollment(tmp_path):
    from backend.enrollment import EnrollmentService
    from backend.metadata.lyrics import LyricsClient
    from backend.recognition.mock import MockRecognizer

    cfg = load_config("does-not-exist.yaml")
    cfg.metadata.cache_dir = str(tmp_path / "cache")
    cfg.recognition.olaf_db = str(tmp_path / "db" / "db")
    index = TrackIndex(str(tmp_path / "index.json"))
    mb = MusicBrainzClient("UA", str(tmp_path / "cache"))
    enr = EnrollmentService(
        cfg, index, MockRecognizer(), mb, LyricsClient("UA"),
        capture=None, art_dir=str(tmp_path / "art"),
    )
    return cfg, index, enr


def _seed_album(index, art_dir, album_id="al1"):
    from backend.recognition.models import Album, AlbumTrack
    art = art_dir / f"{album_id}.jpg"
    art.parent.mkdir(parents=True, exist_ok=True)
    art.write_bytes(b"jpeg")
    index.add_album(Album(
        id=album_id, title="Album", artist="Artist", year="1999",
        art_path=str(art),
        tracklist=[
            AlbumTrack(title="One", position="A1", number=1, length_ms=200000),
            AlbumTrack(title="Two", position="A2", number=2, length_ms=180000),
        ],
    ))
    index.save()
    return art


# -- add by release-group (best pressing) ---------------------------------------
def test_add_album_by_release_group_resolves_best_pressing(tmp_path):
    _, index, enr = _enrollment(tmp_path)
    rg = "12345678-1234-1234-1234-123456789012"
    rel = "87654321-4321-4321-4321-210987654321"

    async def fake_best(rg_mbid):
        assert rg_mbid == rg
        return rel

    async def fake_get_release(mbid):
        assert mbid == rel
        return {"release_mbid": rel, "release_group_mbid": rg,
                "title": "T", "artist": "A", "year": "1999",
                "art_urls": [], "tracklist": [
                    {"position": "A1", "number": 1, "title": "One",
                     "length_ms": 1000, "recording_mbid": None}]}

    enr.mb.best_release_for_group = fake_best
    enr.mb.get_release = fake_get_release
    enr.cfg.lyrics.enabled = False
    summary = asyncio.run(enr.add_album(release_group_mbid=rg))
    assert summary["id"] == rel
    assert summary["release_group_mbid"] == rg
    assert index.albums[rel].release_group_mbid == rg


def test_add_album_rejects_bad_release_group_id(tmp_path):
    import pytest
    _, _, enr = _enrollment(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(enr.add_album(release_group_mbid="../../etc/passwd"))


# -- delete album --------------------------------------------------------------
def test_delete_album_removes_album_sides_and_files(tmp_path):
    from backend.recognition.models import Side, SideTrack
    _, index, enr = _enrollment(tmp_path)
    art = _seed_album(index, tmp_path / "art")
    # a recorded side + its ref wav on disk
    enr.refs_dir.mkdir(parents=True, exist_ok=True)
    ref = enr.refs_dir / "al1-side-a.wav"
    ref.write_bytes(b"wav")
    index.add_side(Side(key="al1-side-a", album_id="al1", side="A",
                        tracks=[SideTrack(album_track_index=0, start_ms=0)]))
    index.save()

    enr.delete_album("al1")

    assert "al1" not in index.albums
    assert "al1-side-a" not in index.sides
    assert not ref.exists()
    assert not art.exists()
    # reload from disk to confirm it persisted
    reloaded = TrackIndex(str(index.path))
    assert "al1" not in reloaded.albums and "al1-side-a" not in reloaded.sides


def test_delete_unknown_album_raises(tmp_path):
    import pytest
    _, _, enr = _enrollment(tmp_path)
    with pytest.raises(ValueError):
        enr.delete_album("nope")


# -- edit metadata -------------------------------------------------------------
def test_update_album_changes_allowed_fields_only(tmp_path):
    _, index, enr = _enrollment(tmp_path)
    _seed_album(index, tmp_path / "art")
    summary = enr.update_album("al1", {"title": "New", "artist": "Band",
                                       "year": "2020", "id": "hacked"})
    assert summary["title"] == "New"
    assert index.albums["al1"].artist == "Band"
    assert index.albums["al1"].year == "2020"
    assert "al1" in index.albums           # id is not editable
    reloaded = TrackIndex(str(index.path))
    assert reloaded.albums["al1"].title == "New"


def test_update_unknown_album_raises(tmp_path):
    import pytest
    _, _, enr = _enrollment(tmp_path)
    with pytest.raises(ValueError):
        enr.update_album("nope", {"title": "x"})


# -- re-record overwrites without stacking a fingerprint -----------------------
def test_refingerprint_deletes_old_ref_before_store(tmp_path):
    _, index, enr = _enrollment(tmp_path)
    _seed_album(index, tmp_path / "art")
    deleted = []

    class RecordingBackend:
        def store(self, p):
            pass

        def delete(self, p):
            deleted.append(str(p))

    enr.backend = RecordingBackend()
    audio = np.zeros((16000 * 5, 1), dtype=np.float32)

    enr.fingerprint_side("al1", "A", audio, 16000)   # first enrollment
    assert deleted == []                              # nothing to replace yet
    enr.fingerprint_side("al1", "A", audio, 16000)    # re-record
    assert len(deleted) == 1                          # old ref deleted once
    assert deleted[0].endswith("al1-side-a.wav") or "side-a" in deleted[0]
    # only one side entry remains for this key
    sides = [s for s in index.sides.values() if s.album_id == "al1"]
    assert len(sides) == 1


# -- API endpoints -------------------------------------------------------------
def _client(tmp_path, token="secret"):
    from fastapi.testclient import TestClient
    from backend.server import create_app
    from backend.state import StateManager

    _, index, enr = _enrollment(tmp_path)
    _seed_album(index, tmp_path / "art")
    app = create_app(StateManager(), index, enr,
                     art_dir=str(tmp_path / "art"), auth_token=token)
    return TestClient(app), index


def test_patch_and_delete_album_endpoints(tmp_path):
    client, index = _client(tmp_path)
    h = {"X-Auth-Token": "secret"}

    # gated
    assert client.patch("/api/albums/al1", json={"title": "Z"}).status_code == 401

    r = client.patch("/api/albums/al1", json={"title": "Renamed"}, headers=h)
    assert r.status_code == 200 and r.json()["album"]["title"] == "Renamed"

    assert client.patch("/api/albums/nope", json={"title": "Z"},
                        headers=h).status_code == 404

    d = client.delete("/api/albums/al1", headers=h)
    assert d.status_code == 200 and d.json()["deleted"] == "al1"
    assert "al1" not in index.albums
    assert client.delete("/api/albums/al1", headers=h).status_code == 404
