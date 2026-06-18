"""Configuration loading.

Reads a YAML file into typed dataclasses with sensible defaults so the rest of
the code never has to reach into raw dicts or worry about missing keys.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Optional

import yaml


def _expand(path: Optional[str]) -> Optional[str]:
    if path is None:
        return None
    return os.path.expanduser(os.path.expandvars(path))


@dataclass
class AudioConfig:
    device: Optional[Any] = None
    samplerate: int = 44100
    channels: int = 2
    buffer_seconds: float = 12.0
    query_seconds: float = 10.0
    silence_rms: float = 0.01
    tmp_dir: Optional[str] = None     # scratch dir for query WAV; defaults to RAM

    def __post_init__(self) -> None:
        self.tmp_dir = _expand(self.tmp_dir)


@dataclass
class RecognitionConfig:
    backend: str = "olaf"            # "olaf" | "mock"
    olaf_bin: str = "olaf"
    olaf_db: str = "~/.olaf/db"
    interval_seconds: float = 20.0           # slow cadence: drift correction
    fast_interval_seconds: float = 3.0       # fast cadence: lock on track changes
    min_match_score: int = 5

    def __post_init__(self) -> None:
        self.olaf_db = _expand(self.olaf_db)


@dataclass
class MetadataConfig:
    musicbrainz_useragent: str = "VinylDisplay/0.1 ( you@example.com )"
    cache_dir: str = "~/.cache/vinyl-display"

    def __post_init__(self) -> None:
        self.cache_dir = _expand(self.cache_dir)


@dataclass
class LyricsConfig:
    enabled: bool = True


@dataclass
class PlaybackConfig:
    speed_factor: float = 1.0


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    require_auth: bool = True          # protect the companion API with a token
    auth_token: Optional[str] = None   # set one, or let it auto-generate on first run


@dataclass
class Config:
    audio: AudioConfig = field(default_factory=AudioConfig)
    recognition: RecognitionConfig = field(default_factory=RecognitionConfig)
    metadata: MetadataConfig = field(default_factory=MetadataConfig)
    lyrics: LyricsConfig = field(default_factory=LyricsConfig)
    playback: PlaybackConfig = field(default_factory=PlaybackConfig)
    server: ServerConfig = field(default_factory=ServerConfig)


def _build(cls: type, data: Optional[dict]) -> Any:
    """Instantiate a (possibly nested) dataclass from a plain dict."""
    data = data or {}
    kwargs = {}
    for f in fields(cls):
        if f.name not in data:
            continue
        value = data[f.name]
        if is_dataclass(f.type) and isinstance(value, dict):
            kwargs[f.name] = _build(f.type, value)
        else:
            kwargs[f.name] = value
    return cls(**kwargs)


def load_config(path: Optional[str] = None) -> Config:
    """Load config from YAML, falling back to defaults when the file is absent."""
    if path and Path(path).exists():
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    else:
        raw = {}
    return _build(Config, raw)
