"""Lightweight unit tests for the pure-logic pieces (no hardware/network)."""

import numpy as np

from backend.audio.split import split_by_silence
from backend.config import load_config
from backend.metadata.lyrics import parse_lrc
from backend.recognition.models import (
    Album, AlbumTrack, Side, SideTrack, TrackIndex,
)


def test_config_defaults():
    cfg = load_config("does-not-exist.yaml")
    assert cfg.server.port == 8080
    assert cfg.recognition.backend == "olaf"
    assert cfg.recognition.fast_interval_seconds == 3.0


def test_parse_lrc_sorts_and_converts():
    lines = parse_lrc("[00:01.00]first\n[00:03.50]second\n[00:00.00]intro")
    assert [l["t"] for l in lines] == [0, 1000, 3500]
    assert lines[0]["text"] == "intro"


def test_split_by_silence_finds_two_tracks():
    sr = 8000
    tone = np.ones((sr * 40, 1), dtype=np.float32) * 0.5
    gap = np.zeros((sr * 3, 1), dtype=np.float32)
    audio = np.concatenate([tone, gap, tone])
    segments = split_by_silence(audio, sr, silence_rms=0.01,
                                min_silence_s=1.5, min_track_s=10.0)
    assert len(segments) == 2


def _index_with_side(tmp_path):
    idx = TrackIndex(str(tmp_path / "index.json"))
    idx.add_album(Album(
        id="al1", title="Album", artist="Artist",
        tracklist=[
            AlbumTrack(title="One", position="A1", number=1, length_ms=200000),
            AlbumTrack(title="Two", position="A2", number=2, length_ms=180000),
        ],
    ))
    idx.add_side(Side(key="al1-side-a", album_id="al1", side="A", tracks=[
        SideTrack(album_track_index=0, start_ms=0),
        SideTrack(album_track_index=1, start_ms=200000),
    ]))
    return idx


def test_resolve_maps_offset_to_track(tmp_path):
    idx = _index_with_side(tmp_path)

    r = idx.resolve("al1-side-a", 5000)
    assert r.track.title == "One" and r.index == 0
    assert r.position_ms == 5000
    assert r.next_track.title == "Two"

    r2 = idx.resolve("al1-side-a", 205000)   # 5s into track two
    assert r2.track.title == "Two" and r2.index == 1
    assert r2.position_ms == 5000
    assert r2.next_track is None


def test_index_round_trip(tmp_path):
    _index_with_side(tmp_path).save()
    reloaded = TrackIndex(str(tmp_path / "index.json"))
    assert "al1" in reloaded.albums
    assert reloaded.albums["al1"].tracklist[1].title == "Two"
    assert reloaded.sides["al1-side-a"].tracks[1].start_ms == 200000
