"""Análise de áudio, sincronizada quadro a quadro.

Sincronia: o quadro n representa o instante t = n / fps (fps como fração exata, p.ex.
30000/1001), medido a partir da amostra 0 da trilha. A janela da FFT é centrada em t.

A análise guarda dados "crus" em alta resolução (256 bandas em dBFS por quadro, nível RMS e
envoltória a 100 Hz). Ajustes de sensibilidade, suavização e número de barras são aplicados
depois, rapidamente — assim mudar um controle não exige reanalisar a música.
A referência é absoluta (dBFS), então um trecho de preview gera exatamente os mesmos
valores que a renderização completa.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Callable

import numpy as np

from .ffmpeg import WavegenError

N_BANDS = 256
FMIN, FMAX = 25.0, 16000.0
WINDOW = 4096
LEVEL_WIN = 2048
ENV_RATE = 100           # pontos por segundo da envoltória (estilo "waveform preenchido")
FEATURES_VERSION = 3


def frame_count(n_samples: int, sr: int, fps: Fraction) -> int:
    """Quadros necessários para cobrir todo o áudio (o último quadro pode passar do fim)."""
    return int(-(-n_samples * fps.numerator // (sr * fps.denominator)))  # ceil


def frame_center(n, sr: int, fps: Fraction):
    return (np.asarray(n, dtype=np.int64) * sr * fps.denominator) // fps.numerator


def band_edges() -> np.ndarray:
    return np.geomspace(FMIN, FMAX, N_BANDS + 1)


def _band_matrix(sr: int) -> np.ndarray:
    freqs = np.fft.rfftfreq(WINDOW, 1.0 / sr)
    df = freqs[1]
    edges = band_edges()
    m = np.zeros((N_BANDS, freqs.size), dtype=np.float32)
    for i in range(N_BANDS):
        lo, hi = edges[i], edges[i + 1]
        idx = np.where((freqs >= lo) & (freqs < hi))[0]
        if idx.size >= 2:
            m[i, idx] = 1.0 / idx.size
        else:  # banda mais estreita que um bin: interpolação linear no centro
            c = np.sqrt(lo * hi) / df
            k = int(c)
            m[i, k] += 1 - (c - k)
            m[i, min(k + 1, freqs.size - 1)] += c - k
    return m


@dataclass
class Features:
    fps: Fraction
    sr: int
    n_samples: int
    band_db: np.ndarray      # (n_frames, 256) float16, dBFS
    level_db: np.ndarray     # (n_frames,) float32, dBFS RMS
    env: np.ndarray          # (n_samples/sr*100,) float16, pico linear 0..1+
    peak: float              # pico absoluto da trilha (linear)

    @property
    def n_frames(self) -> int:
        return self.band_db.shape[0]

    @property
    def duration(self) -> float:
        return self.n_samples / self.sr

    def save(self, path: Path) -> None:
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "wb") as f:
            np.savez(f, fps=np.array([self.fps.numerator, self.fps.denominator]),
                     sr=self.sr, n_samples=self.n_samples, band_db=self.band_db,
                     level_db=self.level_db, env=self.env, peak=self.peak,
                     version=FEATURES_VERSION)
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "Features":
        z = np.load(path)
        if int(z["version"]) != FEATURES_VERSION:
            raise ValueError("versão antiga")
        return cls(Fraction(int(z["fps"][0]), int(z["fps"][1])), int(z["sr"]), int(z["n_samples"]),
                   z["band_db"], z["level_db"], z["env"], float(z["peak"]))


def analyze_pcm(pcm: np.ndarray, sr: int, fps: Fraction,
                progress: Callable[[float], None] | None = None,
                cancel: threading.Event | None = None) -> Features:
    """pcm: (amostras, canais) float32 — pode ser um memmap (memória constante)."""
    n_samples = pcm.shape[0]
    n_frames = max(1, frame_count(n_samples, sr, fps))
    half = WINDOW // 2
    win = np.hanning(WINDOW).astype(np.float32)
    ref = win.sum() / 2.0                     # senoide em fundo de escala -> 0 dB
    bands = _band_matrix(sr).T.copy()         # (bins, 256)
    lwin_half = LEVEL_WIN // 2

    band_db = np.empty((n_frames, N_BANDS), dtype=np.float16)
    level_db = np.empty(n_frames, dtype=np.float32)
    batch = 512
    for a in range(0, n_frames, batch):
        if cancel is not None and cancel.is_set():
            raise WavegenError("Operação cancelada.", code="cancelled")
        b = min(n_frames, a + batch)
        centers = frame_center(np.arange(a, b), sr, fps)
        lo, hi = int(centers[0]) - half, int(centers[-1]) + half
        s0, s1 = max(lo, 0), min(hi, n_samples)
        seg = np.zeros(hi - lo, dtype=np.float32)
        if s1 > s0:
            chunk = np.asarray(pcm[s0:s1], dtype=np.float32)
            seg[s0 - lo:s1 - lo] = chunk.mean(axis=1) if chunk.ndim == 2 else chunk
        idx = (centers - lo - half)[:, None] + np.arange(WINDOW)[None, :]
        frames = seg[idx]
        mag = np.abs(np.fft.rfft(frames * win, axis=1)) / ref
        band_db[a:b] = (20 * np.log10(mag @ bands + 1e-9)).astype(np.float16)
        mid = frames[:, half - lwin_half:half + lwin_half]
        level_db[a:b] = 10 * np.log10(np.mean(mid * mid, axis=1) * 2 + 1e-12)  # senoide FS = 0 dB
        if progress:
            progress(b / n_frames * 0.9)

    # envoltória (pico por bloco de 10 ms) e pico global
    blk = sr // ENV_RATE
    n_env = -(-n_samples // blk)
    env = np.zeros(n_env, dtype=np.float32)
    step = blk * 48000
    for s in range(0, n_samples, step):
        chunk = np.asarray(pcm[s:s + step], dtype=np.float32)
        mono = np.abs(chunk).max(axis=1) if chunk.ndim == 2 else np.abs(chunk)
        pad = (-mono.size) % blk
        if pad:
            mono = np.concatenate([mono, np.zeros(pad, np.float32)])
        e = mono.reshape(-1, blk).max(axis=1)
        env[s // blk:s // blk + e.size] = e
    if progress:
        progress(1.0)
    return Features(fps, sr, n_samples, band_db, level_db, env.astype(np.float16),
                    float(env.max()) if env.size else 0.0)


def ensure_features(mix_path: Path, fps: Fraction,
                    progress: Callable[[float], None] | None = None,
                    cancel: threading.Event | None = None) -> Features:
    """Análise com cache em disco ao lado da trilha mixada."""
    from .playlist import open_pcm
    key = hashlib.sha1(f"{mix_path.name}|{fps}|{FEATURES_VERSION}".encode()).hexdigest()[:12]
    cache = mix_path.with_name(f"{mix_path.stem}.{key}.features.npz")
    if cache.exists():
        try:
            return Features.load(cache)
        except Exception:
            cache.unlink(missing_ok=True)
    pcm, sr = open_pcm(mix_path)
    feats = analyze_pcm(pcm, sr, fps, progress, cancel)
    feats.save(cache)
    return feats


# ----------------------------------------------------------------------------- resposta visual

@dataclass
class Response:
    """Como os dados de áudio viram movimento (controles de 'Comportamento')."""
    sensitivity_db: float = 0.0   # ganho visual
    range_db: float = 42.0        # faixa dinâmica exibida
    tilt_db: float = 4.5          # reforço dos agudos por oitava (acima de 1 kHz)
    attack: float = 0.85          # 0..1 velocidade de subida
    release: float = 0.25         # 0..1 velocidade de descida (menor = mais suave)
    top_db: float = -28.0         # nível (dBFS por banda) que enche a barra com sensibilidade 0

    @classmethod
    def from_dict(cls, d: dict) -> "Response":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


def bar_matrix(n_bars: int, fmin: float = FMIN, fmax: float = FMAX) -> np.ndarray:
    """(256, n_bars): média (em amplitude) das bandas finas que caem em cada barra."""
    centers = np.sqrt(band_edges()[:-1] * band_edges()[1:])
    edges = np.geomspace(max(fmin, FMIN), min(fmax, FMAX), n_bars + 1)
    m = np.zeros((N_BANDS, n_bars), dtype=np.float32)
    for j in range(n_bars):
        sel = np.where((centers >= edges[j]) & (centers < edges[j + 1]))[0]
        if sel.size == 0:
            c = np.sqrt(edges[j] * edges[j + 1])
            pos = float(np.interp(np.log(c), np.log(centers), np.arange(N_BANDS)))
            k = int(pos)
            m[k, j] += 1 - (pos - k)
            m[min(k + 1, N_BANDS - 1), j] += pos - k
        else:
            m[sel, j] = 1.0 / sel.size
    return m


def tilt_vector(n_bars: int, tilt_db: float, fmin: float = FMIN, fmax: float = FMAX) -> np.ndarray:
    edges = np.geomspace(max(fmin, FMIN), min(fmax, FMAX), n_bars + 1)
    c = np.sqrt(edges[:-1] * edges[1:])
    return (tilt_db * np.log2(c / 1000.0)).astype(np.float32)


def normalize_db(db: np.ndarray, r: Response, offset=0.0) -> np.ndarray:
    top = r.top_db - r.sensitivity_db
    return np.clip((db + offset - (top - r.range_db)) / r.range_db, 0.0, 1.0)


def smooth(values: np.ndarray, attack: float, release: float,
           state: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Suavização ataque/decaimento sequencial (determinística). Retorna (saída, estado final)."""
    out = np.empty_like(values)
    cur = np.zeros(values.shape[1:], dtype=np.float32) if state is None else state.copy()
    a, r = np.float32(attack), np.float32(release)
    for i in range(values.shape[0]):
        v = values[i]
        cur += (v - cur) * np.where(v > cur, a, r)
        out[i] = cur
    return out, cur


class SeriesProvider:
    """Entrega, para qualquer quadro, os valores já normalizados e suavizados.

    Cada bloco de quadros é calculado de forma independente, "aquecendo" a suavização com
    os 8 segundos anteriores. Assim o valor de um quadro depende só do seu índice — o
    mesmo resultado no preview, na exportação e com qualquer número de processos.
    """

    BLOCK = 256
    WARMUP_S = 8.0

    def __init__(self, feats: Features, n_bars: int, resp: Response,
                 fmin: float = FMIN, fmax: float = FMAX, peak_fall: float = 0.02):
        self.f = feats
        self.n_bars = n_bars
        self.resp = resp
        self.mat = bar_matrix(n_bars, fmin, fmax)
        self.tilt = tilt_vector(n_bars, resp.tilt_db, fmin, fmax)
        self.peak_fall = np.float32(peak_fall)
        self._block = -1
        self._data = None
        self.warmup = int(np.ceil(float(feats.fps) * self.WARMUP_S))

    def _compute(self, a: int, b: int):
        """Calcula [a, b) partindo do repouso em max(0, a - warmup) e devolve só [a, b)."""
        w0 = max(0, a - self.warmup)
        db = self.f.band_db[w0:b].astype(np.float32)
        lin = (10 ** (db / 20)) @ self.mat
        vals = normalize_db(20 * np.log10(lin + 1e-9), self.resp, self.tilt).astype(np.float32)
        # nível RMS: -30 dBFS = 0, -3 dBFS = 1 (música masterizada fica ~0,6–0,8)
        lvl = np.clip((self.f.level_db[w0:b, None].astype(np.float32)
                       + self.resp.sensitivity_db + 30.0) / 27.0, 0.0, 1.0)
        bands, _ = smooth(vals, self.resp.attack, self.resp.release)
        level, _ = smooth(lvl.astype(np.float32), self.resp.attack, self.resp.release)
        peaks = np.empty_like(bands)
        cur = np.zeros(self.n_bars, np.float32)
        for i in range(bands.shape[0]):
            cur = np.maximum(bands[i], cur - self.peak_fall)
            peaks[i] = cur
        k = a - w0
        return bands[k:], level[k:, 0], peaks[k:]

    def _load_block(self, blk: int):
        a = blk * self.BLOCK
        b = min(self.f.n_frames, a + self.BLOCK)
        self._data = self._compute(a, b)
        self._block = blk

    def frame(self, n: int) -> tuple[np.ndarray, float, np.ndarray]:
        n = int(min(max(n, 0), self.f.n_frames - 1))
        blk = n // self.BLOCK
        if blk != self._block:
            self._load_block(blk)
        i = n - blk * self.BLOCK
        bands, level, peaks = self._data
        return bands[i], float(level[i]), peaks[i]


def scope(pcm: np.ndarray, sr: int, t: float, window_s: float, n_points: int) -> np.ndarray:
    """Forma de onda (osciloscópio) centrada em t, mono, média em n_points pontos."""
    n = pcm.shape[0]
    span = max(int(window_s * sr), n_points)
    c = int(round(t * sr))
    a, b = c - span // 2, c - span // 2 + span
    seg = np.zeros(span, dtype=np.float32)
    s0, s1 = max(a, 0), min(b, n)
    if s1 > s0:
        chunk = np.asarray(pcm[s0:s1], dtype=np.float32)
        seg[s0 - a:s1 - a] = chunk.mean(axis=1) if chunk.ndim == 2 else chunk
    edges = np.linspace(0, span, n_points + 1).astype(np.int64)
    sums = np.add.reduceat(seg, edges[:-1])
    return sums / np.maximum(np.diff(edges), 1)


def envelope_window(feats: Features, t: float, window_s: float, n_points: int) -> np.ndarray:
    """Envoltória (pico) de t - w/2 a t + w/2 em n_points (fora do áudio = 0)."""
    env = feats.env
    pos = (t - window_s / 2 + np.arange(n_points + 1) * (window_s / n_points)) * ENV_RATE
    idx = np.floor(pos).astype(np.int64)
    a = idx[:-1]
    b = np.maximum(idx[1:], a + 1)
    # índices fora do áudio leem um zero extra colocado no fim
    lo, hi = int(a.min()), int(b.max())
    seg = np.zeros(hi - lo + 1, dtype=np.float32)
    s0, s1 = max(lo, 0), min(hi, env.size)
    if s1 > s0:
        seg[s0 - lo:s1 - lo] = env[s0:s1]
    return np.maximum.reduceat(seg, a - lo)[: n_points] * (b > a)
