"""ASGI entrypoint for the containerized server.

The container runs ``uvicorn backend.asgi:app``. Unlike ``backend.main`` (local
dev, which also runs the line-in recognition loop), this builds a pure server:
recognition and enrollment audio arrive from the iPad mic over HTTP, so there is
no audio capture here. State lives under the ``DATA_DIR`` volume.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .config import load_config
from .enrollment import EnrollmentService
from .metadata.acoustid import AcoustIDClient
from .metadata.lyrics import LyricsClient
from .metadata.musicbrainz import MusicBrainzClient
from .recognition.models import TrackIndex
from .recognition.olaf import OlafRecognizer
from .server import create_app
from .settings import SettingsManager, generate_token
from .state import StateManager

log = logging.getLogger("vinyl")


def _resolve_token(cfg, db_dir: Path):
    if not cfg.server.require_auth:
        return None
    if cfg.server.auth_token:
        return cfg.server.auth_token
    token_file = db_dir / "auth_token.txt"
    if token_file.exists():
        token = token_file.read_text(encoding="utf-8").strip()
        # Replace older long tokens with a short, typeable one.
        if len(token) <= 12:
            return token
    token = generate_token()
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(token, encoding="utf-8")
    try:
        token_file.chmod(0o600)
    except OSError:
        pass
    log.warning("Generated companion-app token (saved to %s)", token_file)
    return token


def build_app_from_env():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    data_dir = Path(os.environ.get("DATA_DIR", "/data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    config_path = os.environ.get("CONFIG", str(data_dir / "config.yaml"))
    cfg = load_config(config_path)

    # Pin all on-disk state to the data volume regardless of the config file.
    cfg.recognition.backend = "olaf"
    cfg.recognition.olaf_db = str(data_dir / "olaf" / "db")
    cfg.metadata.cache_dir = str(data_dir / "cache")
    cfg.metadata.acoustid_api_key = (
        os.environ.get("ACOUSTID_API_KEY") or cfg.metadata.acoustid_api_key
    )
    # Optionally pin your own short, memorable access token.
    if os.environ.get("AUTH_TOKEN"):
        cfg.server.auth_token = os.environ["AUTH_TOKEN"]

    db_dir = Path(cfg.recognition.olaf_db).parent
    art_dir = db_dir / "art"
    art_dir.mkdir(parents=True, exist_ok=True)

    index = TrackIndex(str(db_dir / "index.json"))
    backend = OlafRecognizer(
        olaf_bin=cfg.recognition.olaf_bin,
        db_path=cfg.recognition.olaf_db,
        min_score=cfg.recognition.min_match_score,
    )
    mb = MusicBrainzClient(cfg.metadata.musicbrainz_useragent, cfg.metadata.cache_dir)
    lyrics = LyricsClient(cfg.metadata.musicbrainz_useragent)
    state = StateManager(speed_factor=cfg.playback.speed_factor)
    enrollment = EnrollmentService(
        cfg, index, backend, mb, lyrics, capture=None, art_dir=str(art_dir)
    )
    settings = SettingsManager(
        cfg, config_path, state=state, backend=backend, mb=mb, lyrics=lyrics
    )
    acoustid = AcoustIDClient(cfg.metadata.acoustid_api_key)
    token = _resolve_token(cfg, db_dir)

    tmp_dir = "/dev/shm" if Path("/dev/shm").exists() else None
    log.info("Vinyl Display server ready (data=%s, auto-label=%s)",
             data_dir, "on" if acoustid.available else "off")
    if token:
        log.info("Companion token: append ?token=%s once on your device.", token)
    return create_app(state, index, enrollment, art_dir=str(art_dir),
                      auth_token=token, settings=settings, tmp_dir=tmp_dir,
                      acoustid=acoustid)


app = build_app_from_env()
