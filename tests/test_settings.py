"""Tests for web-editable settings: validation, persistence, live apply."""

import pytest
import yaml

from backend.config import load_config
from backend.settings import (
    RESTART_FIELDS,
    SettingsError,
    SettingsManager,
    save_config,
    validate,
)


# -- validate ---------------------------------------------------------------

def test_validate_accepts_good_values():
    cleaned = validate({
        "audio.silence_rms": 0.02,
        "recognition.backend": "mock",
        "recognition.interval_seconds": 25,
        "recognition.min_match_score": 7,
        "playback.speed_factor": 1.002,
        "lyrics.enabled": False,
        "metadata.musicbrainz_useragent": "App/1.0 ( me@example.com )",
    })
    assert cleaned["audio.silence_rms"] == 0.02
    assert cleaned["recognition.backend"] == "mock"
    assert cleaned["recognition.min_match_score"] == 7
    assert cleaned["lyrics.enabled"] is False


@pytest.mark.parametrize("changes", [
    {"audio.silence_rms": 2.0},                 # out of [0,1]
    {"audio.silence_rms": "loud"},              # not a number
    {"recognition.backend": "shazam"},          # not olaf/mock
    {"recognition.interval_seconds": 0},        # not positive
    {"recognition.min_match_score": -1},        # not positive
    {"recognition.min_match_score": True},      # bool is not a score
    {"playback.speed_factor": 2.0},             # out of band
    {"lyrics.enabled": "yes"},                  # not a bool
    {"metadata.musicbrainz_useragent": "  "},   # blank
    {"not.a.real.setting": 1},                  # unknown key
])
def test_validate_rejects_bad_values(changes):
    with pytest.raises(SettingsError):
        validate(changes)


def test_validate_rejects_fast_slower_than_slow():
    with pytest.raises(SettingsError):
        validate({
            "recognition.interval_seconds": 5,
            "recognition.fast_interval_seconds": 10,
        })


def test_validate_device_membership_enforced_when_known():
    devices = [{"index": 1, "name": "UCA202", "channels": 2}]
    assert validate({"audio.device": 1}, devices=devices)["audio.device"] == 1
    assert validate({"audio.device": "UCA202"}, devices=devices)["audio.device"] == "UCA202"
    assert validate({"audio.device": None}, devices=devices)["audio.device"] is None
    with pytest.raises(SettingsError):
        validate({"audio.device": 9}, devices=devices)
    with pytest.raises(SettingsError):
        validate({"audio.device": "Webcam"}, devices=devices)


# -- save_config ------------------------------------------------------------

def test_save_config_patches_only_managed_keys(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "server": {"host": "0.0.0.0", "port": 8080, "auth_token": "keep-me"},
        "audio": {"device": None, "samplerate": 44100},
    }))

    save_config(str(cfg_path), {"audio.device": 1, "playback.speed_factor": 1.01})

    raw = yaml.safe_load(cfg_path.read_text())
    assert raw["audio"]["device"] == 1            # patched
    assert raw["audio"]["samplerate"] == 44100    # untouched
    assert raw["server"]["auth_token"] == "keep-me"  # untouched
    assert raw["playback"]["speed_factor"] == 1.01   # new section/key created


# -- SettingsManager --------------------------------------------------------

class _FakeBackend:
    def __init__(self):
        self.min_score = 5


class _FakeClient:
    def __init__(self):
        self.user_agent = "old"


def test_manager_snapshot_reports_current_and_restart_fields(tmp_path):
    cfg = load_config("nope.yaml")
    mgr = SettingsManager(cfg, str(tmp_path / "config.yaml"),
                          device_lister=lambda: [])
    snap = mgr.snapshot()
    assert snap["values"]["recognition.backend"] == "olaf"
    assert set(snap["restart_fields"]) == set(RESTART_FIELDS)
    assert snap["devices"] == []


def test_manager_update_applies_live_and_persists(tmp_path):
    from backend.state import StateManager

    cfg = load_config("nope.yaml")
    cfg_path = tmp_path / "config.yaml"
    state = StateManager(speed_factor=1.0)
    backend = _FakeBackend()
    mb, lyrics = _FakeClient(), _FakeClient()
    mgr = SettingsManager(cfg, str(cfg_path), state=state, backend=backend,
                          mb=mb, lyrics=lyrics, device_lister=lambda: [])

    result = mgr.update({
        "playback.speed_factor": 1.01,
        "recognition.min_match_score": 9,
        "metadata.musicbrainz_useragent": "App/2.0 ( me@example.com )",
    })

    # live mirrors
    assert state.speed_factor == 1.01
    assert backend.min_score == 9
    assert mb.user_agent == "App/2.0 ( me@example.com )"
    assert lyrics.user_agent == "App/2.0 ( me@example.com )"
    # cfg mutated
    assert cfg.playback.speed_factor == 1.01
    # persisted
    raw = yaml.safe_load(cfg_path.read_text())
    assert raw["recognition"]["min_match_score"] == 9
    # nothing here needed a restart
    assert result["restart_required"] == []


def test_manager_update_flags_restart_for_device(tmp_path):
    cfg = load_config("nope.yaml")
    mgr = SettingsManager(cfg, str(tmp_path / "config.yaml"),
                          device_lister=lambda: [])
    result = mgr.update({"audio.device": 2})
    assert "audio.device" in result["restart_required"]
    assert cfg.audio.device == 2


# -- routes -----------------------------------------------------------------

def _client(tmp_path, token="secret"):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from backend.enrollment import EnrollmentService
    from backend.metadata.lyrics import LyricsClient
    from backend.metadata.musicbrainz import MusicBrainzClient
    from backend.recognition.models import TrackIndex
    from backend.recognition.mock import MockRecognizer
    from backend.server import create_app
    from backend.state import StateManager

    cfg = load_config("nope.yaml")
    cfg_path = tmp_path / "config.yaml"
    index = TrackIndex(str(tmp_path / "index.json"))
    mb = MusicBrainzClient("UA", str(tmp_path / "cache"))
    lyrics = LyricsClient("UA")
    backend = MockRecognizer()
    state = StateManager()
    enr = EnrollmentService(cfg, index, backend, mb, lyrics, capture=None,
                            art_dir=str(tmp_path / "art"))
    mgr = SettingsManager(cfg, str(cfg_path), state=state, backend=backend,
                          mb=mb, lyrics=lyrics, device_lister=lambda: [])
    app = create_app(state, index, enr, art_dir=str(tmp_path / "art"),
                     auth_token=token, settings=mgr)
    return TestClient(app), cfg_path


def test_settings_routes_require_token(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get("/api/settings").status_code == 401
    ok = client.get("/api/settings", headers={"X-Auth-Token": "secret"})
    assert ok.status_code == 200
    assert ok.json()["values"]["recognition.backend"] == "olaf"


def test_settings_post_applies_and_persists(tmp_path):
    client, cfg_path = _client(tmp_path)
    resp = client.post("/api/settings", headers={"X-Auth-Token": "secret"},
                       json={"lyrics.enabled": False, "playback.speed_factor": 1.01})
    assert resp.status_code == 200
    assert resp.json()["restart_required"] == []
    raw = yaml.safe_load(cfg_path.read_text())
    assert raw["lyrics"]["enabled"] is False


def test_settings_post_rejects_bad_value(tmp_path):
    client, _ = _client(tmp_path)
    resp = client.post("/api/settings", headers={"X-Auth-Token": "secret"},
                       json={"playback.speed_factor": 5.0})
    assert resp.status_code == 400
    assert "playback.speed_factor" in resp.json()["fields"]
