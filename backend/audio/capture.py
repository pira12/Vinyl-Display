"""Live audio capture from the USB interface.

Keeps a rolling in-memory ring buffer of the most recent audio so the
recognition loop can grab the last N seconds at any moment, and exposes a
cheap RMS level used for silence detection (needle up / gap between tracks).

`sounddevice` is imported lazily so the rest of the app (and `--simulate`
mode) works on machines without PortAudio installed.
"""

from __future__ import annotations

import threading
from typing import Optional

import numpy as np


class AudioCapture:
    def __init__(
        self,
        device: Optional[object] = None,
        samplerate: int = 44100,
        channels: int = 2,
        buffer_seconds: float = 12.0,
    ) -> None:
        self.device = device
        self.samplerate = samplerate
        self.channels = channels
        self.buffer_seconds = buffer_seconds

        self._frames = int(samplerate * buffer_seconds)
        self._buffer = np.zeros((self._frames, channels), dtype=np.float32)
        self._write_pos = 0
        self._filled = 0
        self._lock = threading.Lock()
        self._stream = None

        # Optional full-length recording (used to enroll a whole side).
        self._recording = False
        self._rec_chunks: list = []

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        import sounddevice as sd  # lazy: only needed for real capture

        self._stream = sd.InputStream(
            device=self.device,
            samplerate=self.samplerate,
            channels=self.channels,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    # -- ring buffer ---------------------------------------------------------
    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        self._write(np.asarray(indata, dtype=np.float32))

    def _write(self, data: np.ndarray) -> None:
        n = len(data)
        with self._lock:
            if self._recording:
                self._rec_chunks.append(data.copy())
            end = self._write_pos + n
            if end <= self._frames:
                self._buffer[self._write_pos:end] = data
            else:
                first = self._frames - self._write_pos
                self._buffer[self._write_pos:] = data[:first]
                self._buffer[: n - first] = data[first:]
            self._write_pos = end % self._frames
            self._filled = min(self._filled + n, self._frames)

    def get_recent(self, seconds: float) -> np.ndarray:
        """Return the most recent `seconds` of audio as (frames, channels)."""
        want = min(int(self.samplerate * seconds), self._frames)
        with self._lock:
            if self._filled < want:
                want = self._filled
            start = (self._write_pos - want) % self._frames
            if start + want <= self._frames:
                out = self._buffer[start:start + want].copy()
            else:
                first = self._frames - start
                out = np.concatenate(
                    [self._buffer[start:], self._buffer[: want - first]]
                )
        return out

    # -- full-side recording -------------------------------------------------
    def start_recording(self) -> None:
        with self._lock:
            self._rec_chunks = []
            self._recording = True

    def stop_recording(self) -> np.ndarray:
        with self._lock:
            self._recording = False
            chunks = self._rec_chunks
            self._rec_chunks = []
        if not chunks:
            return np.zeros((0, self.channels), dtype=np.float32)
        return np.concatenate(chunks)

    @property
    def is_recording(self) -> bool:
        return self._recording

    def rms(self, seconds: float = 1.0) -> float:
        """Root-mean-square level of the most recent audio (0..~1)."""
        recent = self.get_recent(seconds)
        if recent.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(recent))))


def list_devices() -> str:
    """Human-readable list of audio devices (for `--list-devices`)."""
    try:
        import sounddevice as sd
    except Exception as exc:  # pragma: no cover - depends on host
        return f"sounddevice unavailable: {exc}"
    return str(sd.query_devices())


def list_input_devices() -> list:
    """Structured list of capture devices for the settings UI.

    Returns ``[{index, name, channels}]`` for devices that have input
    channels. Degrades to ``[]`` when sounddevice is unavailable.
    """
    try:
        import sounddevice as sd
    except Exception:  # pragma: no cover - depends on host
        return []
    try:
        devices = sd.query_devices()
    except Exception:  # pragma: no cover - depends on host
        return []
    out = []
    for i, d in enumerate(devices):
        channels = int(d.get("max_input_channels", 0) or 0)
        if channels > 0:
            out.append({"index": i, "name": d.get("name", str(i)),
                        "channels": channels})
    return out
