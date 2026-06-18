"""Web-editable settings: validate, persist, and apply live.

A small layer over the typed config so the web app can change a curated set of
fields without hand-editing ``config.yaml``. Most fields apply to the running
process immediately (the code reads them at use-time or via a mutable
attribute); the two that re-open the audio stream are persisted and flagged for
a restart.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# Dotted keys the UI is allowed to manage. Everything else (host/port, auth
# token, paths, samplerate, ...) stays file-only on purpose.
MANAGED_FIELDS = (
    "audio.device",
    "audio.silence_rms",
    "recognition.backend",
    "recognition.interval_seconds",
    "recognition.fast_interval_seconds",
    "recognition.min_match_score",
    "playback.speed_factor",
    "lyrics.enabled",
    "metadata.musicbrainz_useragent",
)

# Changing these re-opens the audio capture stream, so they only take effect on
# a restart. The rest apply live.
RESTART_FIELDS = ("audio.device", "recognition.backend")


class SettingsError(ValueError):
    """Validation failure carrying a per-field ``errors`` map."""

    def __init__(self, errors: Dict[str, str]) -> None:
        self.errors = errors
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))


def _as_number(value: Any) -> float:
    # bool is an int subclass; reject it where a real number is expected.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("must be a number")
    return float(value)


def _validate_one(key: str, value: Any, devices: Optional[List[dict]]) -> Any:
    if key == "audio.device":
        if value is None or isinstance(value, int) and not isinstance(value, bool):
            ok_type = True
        elif isinstance(value, str) and value.strip():
            ok_type = True
        else:
            raise ValueError("must be null, a device index, or a device name")
        if value is not None and devices:
            names = {d["name"] for d in devices}
            indices = {d["index"] for d in devices}
            if isinstance(value, str) and value not in names:
                raise ValueError("no input device with that name")
            if isinstance(value, int) and value not in indices:
                raise ValueError("no input device with that index")
        return value

    if key == "audio.silence_rms":
        n = _as_number(value)
        if not 0.0 <= n <= 1.0:
            raise ValueError("must be between 0 and 1")
        return n

    if key == "recognition.backend":
        if value not in ("olaf", "mock"):
            raise ValueError("must be 'olaf' or 'mock'")
        return value

    if key in ("recognition.interval_seconds", "recognition.fast_interval_seconds"):
        n = _as_number(value)
        if n <= 0:
            raise ValueError("must be greater than 0")
        return n

    if key == "recognition.min_match_score":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("must be a whole number")
        if float(value) != int(value):
            raise ValueError("must be a whole number")
        n = int(value)
        if n <= 0:
            raise ValueError("must be greater than 0")
        return n

    if key == "playback.speed_factor":
        n = _as_number(value)
        if not 0.8 <= n <= 1.2:
            raise ValueError("must be between 0.8 and 1.2")
        return n

    if key == "lyrics.enabled":
        if not isinstance(value, bool):
            raise ValueError("must be true or false")
        return value

    if key == "metadata.musicbrainz_useragent":
        if not isinstance(value, str) or not value.strip():
            raise ValueError("must be a non-empty string")
        return value.strip()

    raise ValueError("unknown setting")


def validate(changes: Dict[str, Any],
             devices: Optional[List[dict]] = None,
             current: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Validate a partial settings change. Raises ``SettingsError`` on any bad
    field (no partial application); returns the cleaned values otherwise."""
    cleaned: Dict[str, Any] = {}
    errors: Dict[str, str] = {}
    for key, value in changes.items():
        if key not in MANAGED_FIELDS:
            errors[key] = "unknown setting"
            continue
        try:
            cleaned[key] = _validate_one(key, value, devices)
        except ValueError as exc:
            errors[key] = str(exc)

    # Cross-field: the fast cadence must not be slower than the slow one. Use
    # the merged view (incoming change over current value) when available.
    base = dict(current or {})
    base.update(cleaned)
    slow = base.get("recognition.interval_seconds")
    fast = base.get("recognition.fast_interval_seconds")
    if (slow is not None and fast is not None
            and "recognition.fast_interval_seconds" not in errors
            and "recognition.interval_seconds" not in errors
            and fast > slow):
        errors["recognition.fast_interval_seconds"] = (
            "fast interval must be <= slow interval"
        )

    if errors:
        raise SettingsError(errors)
    return cleaned


def save_config(cfg_path: str, changes: Dict[str, Any]) -> None:
    """Patch managed keys into the YAML file, preserving everything else.

    Re-reads the raw dict from disk and writes only the changed keys back, so
    unmanaged settings keep their values. Note: PyYAML rewrites the file, so any
    inline comments are not preserved.
    """
    path = Path(cfg_path)
    raw: Dict[str, Any] = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for key, value in changes.items():
        section, _, field = key.partition(".")
        if not isinstance(raw.get(section), dict):
            raw[section] = {}
        raw[section][field] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(raw, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


class SettingsManager:
    """Holds the runtime handles needed to read, apply, and persist settings."""

    def __init__(self, cfg, cfg_path: str, state=None, backend=None,
                 mb=None, lyrics=None, device_lister=None) -> None:
        self.cfg = cfg
        self.cfg_path = cfg_path
        self.state = state
        self.backend = backend
        self.mb = mb
        self.lyrics = lyrics
        self._device_lister = device_lister

    def devices(self) -> List[dict]:
        if self._device_lister is not None:
            return self._device_lister()
        from .audio.capture import list_input_devices
        return list_input_devices()

    def current_values(self) -> Dict[str, Any]:
        c = self.cfg
        return {
            "audio.device": c.audio.device,
            "audio.silence_rms": c.audio.silence_rms,
            "recognition.backend": c.recognition.backend,
            "recognition.interval_seconds": c.recognition.interval_seconds,
            "recognition.fast_interval_seconds": c.recognition.fast_interval_seconds,
            "recognition.min_match_score": c.recognition.min_match_score,
            "playback.speed_factor": c.playback.speed_factor,
            "lyrics.enabled": c.lyrics.enabled,
            "metadata.musicbrainz_useragent": c.metadata.musicbrainz_useragent,
        }

    def snapshot(self) -> Dict[str, Any]:
        return {
            "values": self.current_values(),
            "devices": self.devices(),
            "restart_fields": list(RESTART_FIELDS),
        }

    def update(self, changes: Dict[str, Any]) -> Dict[str, Any]:
        cleaned = validate(changes, devices=self.devices(),
                            current=self.current_values())

        # Mutate the in-memory config (most code reads these at use-time).
        for key, value in cleaned.items():
            section, _, field = key.partition(".")
            setattr(getattr(self.cfg, section), field, value)

        # Mirror the few values that were copied out of the config at startup.
        if "playback.speed_factor" in cleaned and self.state is not None:
            self.state.speed_factor = cleaned["playback.speed_factor"]
        if ("recognition.min_match_score" in cleaned and self.backend is not None
                and hasattr(self.backend, "min_score")):
            self.backend.min_score = cleaned["recognition.min_match_score"]
        if "metadata.musicbrainz_useragent" in cleaned:
            ua = cleaned["metadata.musicbrainz_useragent"]
            for client in (self.mb, self.lyrics):
                if client is not None and hasattr(client, "user_agent"):
                    client.user_agent = ua

        save_config(self.cfg_path, cleaned)
        restart_required = [k for k in cleaned if k in RESTART_FIELDS]
        return {"applied": sorted(cleaned), "restart_required": restart_required}
