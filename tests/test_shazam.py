"""Tests for the Shazam backend: response parsing, collection matching, and
the state updates driven by /api/recognize."""

import asyncio

import pytest

from backend.recognition.models import Album, AlbumTrack, TrackIndex
from backend.recognition.shazam import (
    ShazamResult,
    apply_shazam,
    parse_response,
)
from backend.state import StateManager

# A trimmed-down real-world Shazam tag response.
_RESPONSE = {
    "matches": [{"id": "x", "offset": 173.25, "timeskew": 0.0}],
    "track": {
        "title": "Money",
        "subtitle": "Pink Floyd",
        "images": {"coverart": "https://img.example/cover.jpg"},
        "sections": [
            {"type": "SONG", "metadata": [
                {"title": "Album", "text": "The Dark Side of the Moon"},
                {"title": "Label", "text": "Pink Floyd Records"},
                {"title": "Released", "text": "1973"},
            ]},
        ],
    },
}


# -- parse_response -----------------------------------------------------------

def test_parse_response_extracts_fields():
    r = parse_response(_RESPONSE)
    assert r.title == "Money"
    assert r.artist == "Pink Floyd"
    assert r.album == "The Dark Side of the Moon"
    assert r.year == "1973"
    assert r.art_url == "https://img.example/cover.jpg"
    assert r.offset_seconds == 173.25


def test_parse_response_no_match_is_none():
    assert parse_response({"matches": [], "timestamp": 1}) is None
    assert parse_response(None) is None
    assert parse_response("garbage") is None


def test_parse_response_without_offset():
    r = parse_response({"matches": [], "track": {"title": "T", "subtitle": "A"}})
    assert r.title == "T"
    assert r.offset_seconds is None


# -- collection matching --------------------------------------------------------

def _index(tmp_path):
    index = TrackIndex(str(tmp_path / "index.json"))
    index.add_album(Album(
        id="dsotm", title="The Dark Side of the Moon", artist="Pink Floyd",
        year="1973",
        tracklist=[
            AlbumTrack(title="Speak to Me", position="A1", number=1,
                       length_ms=90000),
            AlbumTrack(title="Money", position="B1", number=6,
                       length_ms=382000,
                       lyrics={"synced": True, "lines": [{"t": 0, "text": "x"}]}),
        ],
    ))
    index.add_album(Album(
        id="comp", title="Now That's Music", artist="Various Artists",
        tracklist=[AlbumTrack(title="Unique Compilation Song", position="A1")],
    ))
    return index


def test_find_track_exact(tmp_path):
    album, idx = _index(tmp_path).find_track("Pink Floyd", "Money")
    assert album.id == "dsotm" and idx == 1


def test_find_track_survives_remaster_suffixes_and_ampersands(tmp_path):
    index = _index(tmp_path)
    hit = index.find_track("Pink Floyd", "Money - 2011 Remastered Version")
    assert hit and hit[0].id == "dsotm" and hit[1] == 1
    hit = index.find_track("PINK FLOYD", "Speak to Me (Remastered)")
    assert hit and hit[1] == 0


def test_find_track_requires_matching_artist(tmp_path):
    # Same title, different artist: must not match the collection track.
    assert _index(tmp_path).find_track("Metallica", "Money") is None


def test_find_track_on_various_artists_albums(tmp_path):
    hit = _index(tmp_path).find_track("Some Band", "Unique Compilation Song")
    assert hit and hit[0].id == "comp"


def test_find_track_no_weak_matches(tmp_path):
    assert _index(tmp_path).find_track("Pink Floyd", "Wish You Were Here") is None


# -- apply_shazam ----------------------------------------------------------------

class _FakeLyrics:
    def __init__(self):
        self.calls = []

    async def get(self, artist, title, album="", duration_s=None):
        self.calls.append((artist, title))
        return {"synced": True, "lines": [{"t": 0, "text": "hi"}],
                "duration_ms": 200000}


def _apply(state, index, result, lyrics=None, clip_seconds=10.0):
    return asyncio.run(apply_shazam(state, index, result, clip_seconds,
                                    lyrics=lyrics))


def test_apply_in_collection_publishes_album_context(tmp_path):
    state, index = StateManager(), _index(tmp_path)
    matched = _apply(state, index,
                     ShazamResult(title="Money", artist="Pink Floyd",
                                  offset_seconds=100.0))
    assert matched and state.status == "playing"
    assert state.track["title"] == "Money"
    assert state.album["title"] == "The Dark Side of the Moon"
    assert len(state.tracklist) == 2                  # full album context
    assert state.position_ms == 110000                # offset + clip length
    assert state.lyrics["synced"] is True             # cached at add time


def test_apply_same_track_only_resyncs(tmp_path):
    state, index = StateManager(), _index(tmp_path)
    r = ShazamResult(title="Money", artist="Pink Floyd", offset_seconds=100.0)
    _apply(state, index, r)
    ident = state.current_ident
    _apply(state, index, ShazamResult(title="Money", artist="Pink Floyd",
                                      offset_seconds=112.0))
    assert state.current_ident == ident
    assert state.position_ms == 122000                # clock corrected


def test_apply_outside_collection_still_plays_with_live_lyrics(tmp_path):
    state, index = StateManager(), _index(tmp_path)
    lyrics = _FakeLyrics()
    matched = _apply(state, index,
                     ShazamResult(title="Kashmir", artist="Led Zeppelin",
                                  album="Physical Graffiti",
                                  art_url="https://img/x.jpg",
                                  offset_seconds=30.0),
                     lyrics=lyrics)
    assert matched and state.status == "playing"
    assert state.track["title"] == "Kashmir"
    assert state.track["duration_ms"] == 200000       # from LRCLIB
    assert state.album["in_collection"] is False
    assert state.album["art_url"] == "https://img/x.jpg"
    assert state.lyrics["lines"]                      # fetched on the fly
    assert lyrics.calls == [("Led Zeppelin", "Kashmir")]


def test_apply_none_sets_listening(tmp_path):
    state, index = StateManager(), _index(tmp_path)
    assert _apply(state, index, None) is False
    assert state.status == "listening"
    assert state.current_ident is None


def test_apply_clamps_position_to_track_length(tmp_path):
    state, index = StateManager(), _index(tmp_path)
    # Money is 382s; an offset near the end plus the clip would overshoot.
    _apply(state, index, ShazamResult(title="Money", artist="Pink Floyd",
                                      offset_seconds=380.0))
    assert state.position_ms == 382000


def test_quiet_passage_misses_keep_the_track_up(tmp_path):
    state, index = StateManager(), _index(tmp_path)
    _apply(state, index, ShazamResult(title="Money", artist="Pink Floyd",
                                      offset_seconds=10.0))
    ident = state.current_ident
    _apply(state, index, None)
    _apply(state, index, None)
    assert state.status == "playing"          # two misses tolerated
    assert state.current_ident == ident       # next hit resyncs, no re-publish
    _apply(state, index, None)
    assert state.status == "listening"        # streak: the side really ended
    assert state.current_ident is None


def test_miss_streak_resets_on_match(tmp_path):
    state, index = StateManager(), _index(tmp_path)
    r = ShazamResult(title="Money", artist="Pink Floyd", offset_seconds=10.0)
    _apply(state, index, r)
    _apply(state, index, None)
    _apply(state, index, None)
    _apply(state, index, r)                   # lock again before the streak
    _apply(state, index, None)
    _apply(state, index, None)
    assert state.status == "playing"          # counter restarted at the match


# -- /api/recognize with a shazam-style backend ----------------------------------

class _FakeShazamBackend:
    """Shazam-shaped: async recognize(), and deliberately no store()."""

    def __init__(self, result):
        self.result = result

    async def recognize(self, wav_path):
        return self.result


def _wav_bytes(seconds=2.0, rate=16000):
    import io
    import numpy as np
    import soundfile as sf

    buf = io.BytesIO()
    sf.write(buf, np.zeros(int(rate * seconds), dtype="float32"), rate,
             format="WAV", subtype="PCM_16")
    return buf.getvalue()


def _app(tmp_path, backend):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from backend.config import load_config
    from backend.enrollment import EnrollmentService
    from backend.metadata.lyrics import LyricsClient
    from backend.metadata.musicbrainz import MusicBrainzClient
    from backend.server import create_app

    cfg = load_config("nope.yaml")
    cfg.recognition.olaf_db = str(tmp_path / "db" / "db")
    index = _index(tmp_path)
    state = StateManager()
    enr = EnrollmentService(cfg, index, backend,
                            MusicBrainzClient("UA", str(tmp_path / "c")),
                            LyricsClient("UA"), capture=None,
                            art_dir=str(tmp_path / "art"))
    app = create_app(state, index, enr, art_dir=str(tmp_path / "art"),
                     auth_token="t", tmp_dir=str(tmp_path))
    return TestClient(app), state, enr


def test_recognize_endpoint_uses_shazam_backend(tmp_path):
    backend = _FakeShazamBackend(
        ShazamResult(title="Money", artist="Pink Floyd", offset_seconds=50.0))
    client, state, enr = _app(tmp_path, backend)
    r = client.post("/api/recognize", headers={"X-Auth-Token": "t"},
                    content=_wav_bytes(seconds=2.0))
    assert r.status_code == 200
    body = r.json()
    assert body["matched"] is True
    assert body["track"]["title"] == "Money"
    assert state.position_ms == 52000       # offset + real clip duration
    # No store() on this backend -> the recording UI stays hidden.
    assert enr.recording_status()["can_record"] is False


def test_recognize_endpoint_no_match_reports_listening(tmp_path):
    client, state, _ = _app(tmp_path, _FakeShazamBackend(None))
    r = client.post("/api/recognize", headers={"X-Auth-Token": "t"},
                    content=_wav_bytes())
    assert r.json()["matched"] is False
    assert state.status == "listening"


# -- one-tap save from the display ------------------------------------------------

def test_add_current_saves_external_track_and_upgrades_display(tmp_path):
    backend = _FakeShazamBackend(
        ShazamResult(title="Kashmir", artist="Led Zeppelin",
                     album="Physical Graffiti", offset_seconds=30.0))
    client, state, enr = _app(tmp_path, backend)
    client.post("/api/recognize", headers={"X-Auth-Token": "t"},
                content=_wav_bytes(seconds=2.0))
    assert state.album["in_collection"] is False

    async def fake_search(query):
        assert "Physical Graffiti" in query and "Led Zeppelin" in query
        return [{"release_group_mbid": "rg", "release_mbid": "rel",
                 "title": "Physical Graffiti", "artist": "Led Zeppelin"}]

    async def fake_add(release_mbid=None, release_group_mbid=None):
        album = Album(id="pg", title="Physical Graffiti",
                      artist="Led Zeppelin", release_group_mbid="rg",
                      tracklist=[AlbumTrack(title="Kashmir", position="B1",
                                            length_ms=506000)])
        enr.index.add_album(album)
        return enr.album_summary(album)

    enr.search = fake_search
    enr.add_album = fake_add

    r = client.post("/api/collection/add-current", headers={"X-Auth-Token": "t"})
    assert r.status_code == 200
    assert r.json()["album"]["title"] == "Physical Graffiti"
    # The display upgraded in place: full album context, clock preserved.
    assert state.album["title"] == "Physical Graffiti"
    assert state.tracklist and state.current_ident == ("album", "pg", 0)
    assert state.position_ms >= 32000


def test_add_current_rejected_when_nothing_playing(tmp_path):
    client, _, _ = _app(tmp_path, _FakeShazamBackend(None))
    r = client.post("/api/collection/add-current", headers={"X-Auth-Token": "t"})
    assert r.status_code == 400


def test_add_current_rejected_for_collection_tracks(tmp_path):
    backend = _FakeShazamBackend(
        ShazamResult(title="Money", artist="Pink Floyd", offset_seconds=10.0))
    client, state, _ = _app(tmp_path, backend)
    client.post("/api/recognize", headers={"X-Auth-Token": "t"},
                content=_wav_bytes(seconds=2.0))
    assert state.status == "playing"
    r = client.post("/api/collection/add-current", headers={"X-Auth-Token": "t"})
    assert r.status_code == 400
