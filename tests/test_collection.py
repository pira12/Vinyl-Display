"""Tests for collection management: search dedup, delete, edit, re-record."""

import asyncio

import numpy as np

from backend.config import load_config
from backend.metadata.musicbrainz import MusicBrainzClient
from backend.recognition.models import TrackIndex


# -- search dedup --------------------------------------------------------------
def _mb(tmp_path, releases):
    mb = MusicBrainzClient("UA", str(tmp_path / "cache"))

    async def fake_get(url, params):
        return {"releases": releases}

    mb._get = fake_get  # type: ignore[assignment]
    return mb


def test_search_collapses_release_groups(tmp_path):
    mb = _mb(tmp_path, [
        {"id": "r1", "title": "Album", "status": "Bootleg", "date": "2001",
         "release-group": {"id": "rg1"}, "artist-credit": [{"name": "A"}],
         "track-count": 10},
        {"id": "r2", "title": "Album", "status": "Official", "date": "2000",
         "cover-art-archive": {"front": True}, "track-count": 10,
         "release-group": {"id": "rg1"}, "artist-credit": [{"name": "A"}]},
        {"id": "r3", "title": "Other", "status": "Official",
         "release-group": {"id": "rg2"}, "artist-credit": [{"name": "B"}]},
    ])
    rows = asyncio.run(mb.search_releases("album"))
    assert len(rows) == 2                       # rg1's two editions collapse to one
    assert rows[0]["release_mbid"] == "r2"      # prefers Official + cover art
    assert rows[0]["artist"] == "A"
    assert rows[1]["release_mbid"] == "r3"      # distinct group preserved, in order


def test_search_falls_back_to_release_id_without_group(tmp_path):
    mb = _mb(tmp_path, [
        {"id": "r1", "title": "X", "artist-credit": [{"name": "A"}]},
        {"id": "r2", "title": "Y", "artist-credit": [{"name": "A"}]},
    ])
    rows = asyncio.run(mb.search_releases("x"))
    assert {r["release_mbid"] for r in rows} == {"r1", "r2"}


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
