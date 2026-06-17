"""Entry point — wires everything together and runs it on one event loop.

    python -m backend.main --config config.yaml      # normal run
    python -m backend.main --simulate                # no hardware needed
    python -m backend.main --list-devices            # show audio devices
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

import uvicorn

from .audio.capture import AudioCapture, list_devices
from .config import load_config
from .metadata.lyrics import LyricsClient
from .metadata.musicbrainz import MusicBrainzClient
from .recognition.models import TrackIndex
from .recognition.recognizer import RecognitionService
from .server import create_app
from .state import StateManager

log = logging.getLogger("vinyl")


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


async def run(cfg) -> None:
    state = StateManager(speed_factor=cfg.playback.speed_factor)
    state.bind_loop(asyncio.get_running_loop())

    index = TrackIndex(str(Path(cfg.recognition.olaf_db).parent / "index.json"))
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
            log.exception("audio capture failed to start; is the USB interface connected?")
            raise

    mb = MusicBrainzClient(cfg.metadata.musicbrainz_useragent, cfg.metadata.cache_dir)
    lyrics = LyricsClient(cfg.metadata.musicbrainz_useragent)

    recognizer = RecognitionService(
        cfg, state, index, backend, capture=capture, mb_client=mb, lyrics_client=lyrics
    )

    app = create_app(state)
    server = uvicorn.Server(
        uvicorn.Config(app, host=cfg.server.host, port=cfg.server.port, log_level="info")
    )

    log.info("Vinyl Display running at http://%s:%s", cfg.server.host, cfg.server.port)
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
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
