"""Áudio de demonstração sintético (usado nas miniaturas e no preview sem músicas)."""

from __future__ import annotations

import functools
from fractions import Fraction

import numpy as np

from ..analysis import Features, analyze_pcm

SR = 48000


@functools.lru_cache(maxsize=1)
def demo_pcm(seconds: float = 16.0) -> np.ndarray:
    rng = np.random.default_rng(7)
    n = int(SR * seconds)
    t = np.arange(n) / SR
    y = np.zeros(n, np.float32)
    beat = 60 / 172                                   # drum & bass
    for k in range(int(seconds / beat)):
        s = int(k * beat * SR)
        if k % 4 in (0, 2) or k % 8 == 5:             # bumbo
            m = min(int(0.3 * SR), n - s)
            tt = np.arange(m) / SR
            y[s:s + m] += 0.9 * np.sin(2 * np.pi * (48 + 140 * np.exp(-tt * 35)) * tt) * np.exp(-tt * 9)
        if k % 4 in (1, 3):                           # caixa
            m = min(int(0.18 * SR), n - s)
            y[s:s + m] += 0.45 * rng.uniform(-1, 1, m) * np.exp(-np.arange(m) / SR * 22)
        h = s + int(beat / 2 * SR)                    # chimbal
        m = min(int(0.04 * SR), n - h)
        if m > 0:
            y[h:h + m] += 0.18 * rng.uniform(-1, 1, m) * np.exp(-np.arange(m) / SR * 90)
    notes = [55.0, 65.41, 49.0, 73.42]
    for i in range(int(seconds / 2) + 1):
        f = notes[i % 4]
        s, e = int(i * 2 * SR), min(int((i + 1) * 2 * SR), n)
        if s >= n:
            break
        tt = t[s:e] - t[s]
        y[s:e] += 0.32 * np.sin(2 * np.pi * f * tt)
        for mult, a in ((4, 0.07), (5, 0.06), (6, 0.05), (8, 0.03)):
            y[s:e] += a * np.sin(2 * np.pi * f * mult * tt) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.7 * tt))
    y = y / np.abs(y).max() * 0.8
    return np.stack([y, y], axis=1).astype(np.float32)


@functools.lru_cache(maxsize=4)
def demo_features(fps: Fraction) -> Features:
    return analyze_pcm(demo_pcm(), SR, fps)
