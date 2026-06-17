"""Lightweight unit tests for the pure-logic pieces (no hardware/network)."""

import numpy as np

from backend.config import load_config
from backend.enroll import split_by_silence
from backend.metadata.lyrics import parse_lrc


def test_config_defaults():
    cfg = load_config("does-not-exist.yaml")
    assert cfg.server.port == 8080
    assert cfg.recognition.backend == "olaf"
    assert cfg.audio.samplerate == 44100


def test_parse_lrc_sorts_and_converts():
    lrc = "[00:01.00]first\n[00:03.50]second\n[00:00.00]intro"
    lines = parse_lrc(lrc)
    assert [l["t"] for l in lines] == [0, 1000, 3500]
    assert lines[0]["text"] == "intro"


def test_parse_lrc_multi_timestamp_line():
    lines = parse_lrc("[00:01.00][00:05.00]chorus")
    assert [l["text"] for l in lines] == ["chorus", "chorus"]
    assert [l["t"] for l in lines] == [1000, 5000]


def test_split_by_silence_finds_two_tracks():
    sr = 8000
    tone = np.ones((sr * 40, 1), dtype=np.float32) * 0.5  # 40s of "audio"
    gap = np.zeros((sr * 3, 1), dtype=np.float32)         # 3s silence
    audio = np.concatenate([tone, gap, tone])
    segments = split_by_silence(audio, sr, silence_rms=0.01,
                                min_silence_s=1.5, min_track_s=10.0)
    assert len(segments) == 2
