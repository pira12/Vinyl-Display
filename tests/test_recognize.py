"""Tests for the request-driven recognition + client-fed enrollment endpoints."""

import numpy as np
import pytest

from backend.config import load_config
from backend.recognition.mock import _SIDE_KEY, build_simulated_index
from backend.recognition.models import Match


class FakeBackend:
    """Olaf-shaped stand-in: query() returns the simulated side, store() records."""

    def __init__(self, index):
        build_simulated_index(index)
        self.stored = []

    def query(self, wav_path=None):
        return Match(key=_SIDE_KEY, offset_seconds=0.0, score=99)

    def store(self, wav_path):
        self.stored.append(wav_path)


def _app(tmp_path, token="t"):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from backend.enrollment import EnrollmentService
    from backend.metadata.lyrics import LyricsClient
    from backend.metadata.musicbrainz import MusicBrainzClient
    from backend.recognition.models import TrackIndex
    from backend.server import create_app
    from backend.state import StateManager

    cfg = load_config("nope.yaml")
    cfg.recognition.olaf_db = str(tmp_path / "db" / "db")
    index = TrackIndex(str(tmp_path / "i.json"))
    backend = FakeBackend(index)
    mb = MusicBrainzClient("UA", str(tmp_path / "c"))
    state = StateManager()
    enr = EnrollmentService(cfg, index, backend, mb, LyricsClient("UA"),
                            capture=None, art_dir=str(tmp_path / "art"))
    app = create_app(state, index, enr, art_dir=str(tmp_path / "art"),
                     auth_token=token, tmp_dir=str(tmp_path))
    return TestClient(app), state, enr, index


H = {"X-Auth-Token": "t"}


# -- recognize --------------------------------------------------------------

def test_recognize_updates_state(tmp_path):
    client, state, _, _ = _app(tmp_path)
    r = client.post("/api/recognize", headers=H, content=b"RIFFfakewav")
    assert r.status_code == 200
    body = r.json()
    assert body["matched"] is True
    assert body["status"] == "playing"
    assert body["track"]["title"] == "Tangled Up in Blue"
    assert state.status == "playing"


def test_recognize_noop_when_paused(tmp_path):
    client, state, _, _ = _app(tmp_path)
    state.set_listening(False)
    r = client.post("/api/recognize", headers=H, content=b"x")
    assert r.status_code == 200
    assert r.json() == {"status": "paused", "matched": False}


def test_recognize_requires_token(tmp_path):
    client, _, _, _ = _app(tmp_path)
    assert client.post("/api/recognize", content=b"x").status_code == 401


def test_recognize_rejects_empty_body(tmp_path):
    client, _, _, _ = _app(tmp_path)
    assert client.post("/api/recognize", headers=H, content=b"").status_code == 400


# -- client-fed enrollment session ------------------------------------------

def test_record_session_enrolls_a_side(tmp_path):
    client, _, enr, index = _app(tmp_path)
    album_id = "sim-blood-on-the-tracks"

    r = client.post("/api/record/start", headers=H,
                    json={"album_id": album_id, "side": "A"})
    assert r.status_code == 200 and r.json()["recording"] is True

    pcm = np.zeros(16000, dtype=np.int16).tobytes()   # 1s of audio per chunk
    assert client.post("/api/record/chunk", headers=H, content=pcm).status_code == 200
    client.post("/api/record/chunk", headers=H, content=pcm)

    r = client.post("/api/record/stop", headers=H)
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["tracks"] >= 1
    assert any(s.album_id == album_id for s in index.sides.values())
    assert enr.backend.stored          # olaf store was called on the reference


def test_record_chunk_without_start_is_400(tmp_path):
    client, _, _, _ = _app(tmp_path)
    assert client.post("/api/record/chunk", headers=H,
                       content=b"\x00\x00").status_code == 400


def test_record_start_requires_token(tmp_path):
    client, _, _, _ = _app(tmp_path)
    assert client.post("/api/record/start",
                       json={"album_id": "x", "side": "A"}).status_code == 401
