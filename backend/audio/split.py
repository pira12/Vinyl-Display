"""Detect track boundaries within a recorded side by the silent bands."""

from __future__ import annotations

from typing import List, Tuple

import numpy as np


def split_by_silence(
    audio: np.ndarray,
    sr: int,
    silence_rms: float = 0.01,
    min_silence_s: float = 1.5,
    min_track_s: float = 30.0,
) -> List[Tuple[float, float]]:
    """Return (start_s, end_s) segments separated by sufficiently long silence."""
    mono = audio.mean(axis=1) if audio.ndim > 1 else audio
    win = int(sr * 0.1)  # 100 ms windows
    if win == 0:
        return []
    n_win = len(mono) // win
    loud = np.array([
        np.sqrt(np.mean(np.square(mono[i * win:(i + 1) * win]))) > silence_rms
        for i in range(n_win)
    ])

    segments: List[Tuple[float, float]] = []
    start = None
    silence_run = 0
    min_silence_win = int(min_silence_s / 0.1)
    for i, is_loud in enumerate(loud):
        if is_loud:
            if start is None:
                start = i
            silence_run = 0
        elif start is not None:
            silence_run += 1
            if silence_run >= min_silence_win:
                end = i - silence_run
                if (end - start) * 0.1 >= min_track_s:
                    segments.append((start * 0.1, end * 0.1))
                start = None
                silence_run = 0
    if start is not None and (n_win - start) * 0.1 >= min_track_s:
        segments.append((start * 0.1, n_win * 0.1))
    return segments
