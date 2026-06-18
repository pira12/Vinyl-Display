"""Entry point — wires everything together and runs it on one event loop.

    python -m backend.main --config config.yaml      # normal run
    python -m backend.main --simulate                # no hardware needed
    python -m backend.main --list-devices            # show audio devices
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import secrets
from pathlib import Path

import uvicorn

from .audio.capture import AudioCapture, list_devices
from .config import load_config
from .enrollment import EnrollmentService
from .metadata.lyrics import LyricsClient
from .metadata.musicbrainz import MusicBrainzClient
from .recognition.models import TrackIndex
from .recognition.recognizer import RecognitionService
from .server import create_app
from .settings import SettingsManager
from .state import StateManager

log = logging.getLogger("vinyl")


def _tmp_dir(cfg) -> str:
    if cfg.audio.tmp_dir:
        return cfg.audio.tmp_dir
    return "/dev/shm" if Path("/dev/shm").exists() else cfg.metadata.cache_dir


def _resolve_token(cfg, db_dir: Path) -> str | None:
    """Return the companion-API token, generating + persisting one if needed."""
    if not cfg.server.require_auth:
        return None
    if cfg.server.auth_token:
        return cfg.server.auth_token
    token_file = db_dir / "auth_token.txt"
    if token_file.exists():
        return token_file.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(18)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(token, encoding="utf-8")
    try:
        token_file.chmod(0o600)
    except OSError:
        pass
    log.warning("Generated companion-app token (saved to %s)", token_file)
    return token


def build_backend(cfg, index: TrackIndex):
    if cfg.recognition.backend == "mock":
        from .recognition.mock import MockRecognizer

        return MockRecognizer(index=index)
    from .recognition.olaf import OlafRecognizer

    return OlafRecognizer(
        olaf_bin=cfg.recognition.olaf_bin,
        db_path=cfg.recognition.olaf_db,
        min_score=cfg.recognition.min_match_score,
    )


async def run(cfg, cfg_path: str = "config.yaml") -> None:
    state = StateManager(speed_factor=cfg.playback.speed_factor)
    state.bind_loop(asyncio.get_running_loop())

    db_dir = Path(cfg.recognition.olaf_db).parent
    art_dir = db_dir / "art"
    art_dir.mkdir(parents=True, exist_ok=True)
    index = TrackIndex(str(db_dir / "index.json"))
    backend = build_backend(cfg, index)

    capture = None
    if cfg.recognition.backend != "mock":
        capture = AudioCapture(
            device=cfg.audio.device,
            samplerate=cfg.audio.samplerate,
            channels=cfg.audio.channels,
            buffer_seconds=cfg.audio.buffer_seconds,
        )
        try:
            capture.start()
        except Exception:  # noqa: BLE001
            # Don't take the whole app down over a missing/unconfigured input.
            # The web UI (and its Settings panel) must still come up so the
            # device can be picked; recognition simply stays idle until then.
            log.error(
                "audio capture failed to start (device=%r); is the USB "
                "interface connected? The web app will still start so you can "
                "pick a device under Collection > Settings, then restart.",
                cfg.audio.device,
            )
            capture = None

    mb = MusicBrainzClient(cfg.metadata.musicbrainz_useragent, cfg.metadata.cache_dir)
    lyrics = LyricsClient(cfg.metadata.musicbrainz_useragent)

    recognizer = RecognitionService(
        cfg, state, index, backend, capture=capture, tmp_dir=_tmp_dir(cfg)
    )

    # Companion-app backend: search/add albums, capture & fingerprint sides.
    enrollment = EnrollmentService(
        cfg, index, backend, mb, lyrics, capture=capture, art_dir=str(art_dir)
    )

    settings = SettingsManager(
        cfg, cfg_path, state=state, backend=backend, mb=mb, lyrics=lyrics
    )

    token = _resolve_token(cfg, db_dir)
    app = create_app(state, index, enrollment, art_dir=str(art_dir),
                     auth_token=token, settings=settings)
    server = uvicorn.Server(
        uvicorn.Config(app, host=cfg.server.host, port=cfg.server.port, log_level="info")
    )

    log.info("Vinyl Display running at http://%s:%s", cfg.server.host, cfg.server.port)
    if token:
        log.info("Companion app: open /manage?token=%s on your phone (once).", token)
    try:
        await asyncio.gather(server.serve(), recognizer.run())
    finally:
        if capture is not None:
            capture.stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Vinyl Display")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--simulate", action="store_true",
                        help="run with the mock recognizer (no hardware)")
    parser.add_argument("--list-devices", action="store_true",
                        help="print available audio devices and exit")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.list_devices:
        print(list_devices())
        return

    cfg = load_config(args.config)
    if args.simulate:
        cfg.recognition.backend = "mock"

    try:
        asyncio.run(run(cfg, args.config))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
