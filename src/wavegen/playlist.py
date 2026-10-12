"""Playlist: montagem da trilha sonora final (sequência, crossfade, intervalos, fades, volume).

A trilha é renderizada uma única vez pelo FFmpeg para um WAV float32 (48 kHz, estéreo) em
cache. Esse arquivo é a fonte única de verdade: a análise, o preview e o vídeo final usam
exatamente as mesmas amostras — por isso a sincronia é garantida.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import struct
import subprocess
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from .ffmpeg import WavegenError, ffmpeg_path, media_arg, probe_audio, translate_ffmpeg_error

SR = 48000
CHANNELS = 2
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "wavegen"

AUDIO_EXTS = {".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".opus", ".wma", ".aif", ".aiff",
              ".alac", ".mka", ".webm"}


@dataclass
class Track:
    path: str
    duration: float = 0.0
    codec: str = ""
    title: str = ""             # nome para a lista de faixas (tags da música ou editado)

    @property
    def name(self) -> str:
        return Path(self.path).name

    @property
    def display_title(self) -> str:
        if self.title.strip():
            return self.title.strip()
        stem = Path(self.path).stem.replace("_", " ")
        return re.sub(r"\s+", " ", stem).strip()

    def exists(self) -> bool:
        return Path(self.path).is_file()

    @classmethod
    def load(cls, path: str) -> "Track":
        info = probe_audio(path)
        title = info.title
        if title and info.artist:
            title = f"{info.artist} - {title}"
        return cls(str(Path(path).absolute()), info.duration, info.codec, title)


@dataclass
class AudioSettings:
    volume_db: float = 0.0      # volume geral (0 = original)
    fade_in: float = 0.0        # s, no início da trilha
    fade_out: float = 0.0       # s, no fim da trilha
    crossfade: float = 0.0      # s, sobreposição entre faixas (0 = sem)
    gap: float = 0.0            # s de silêncio entre faixas (ignorado com crossfade)
    sequential: bool = True     # False = ordem embaralhada (fixa pela semente)
    shuffle_seed: int = 1

    @classmethod
    def from_dict(cls, d: dict) -> "AudioSettings":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


def ordered(tracks: list[Track], st: AudioSettings) -> list[Track]:
    if st.sequential or len(tracks) < 2:
        return list(tracks)
    out = list(tracks)
    random.Random(st.shuffle_seed).shuffle(out)
    return out


def effective_crossfade(durations: list[float], st: AudioSettings) -> float:
    """Crossfade limitado à metade da faixa mais curta (evita sobrepor 3 faixas)."""
    if st.crossfade <= 0 or len(durations) < 2:
        return 0.0
    return max(0.0, min(st.crossfade, 0.5 * min(durations)))


def total_duration(durations: list[float], st: AudioSettings) -> float:
    """Duração estimada da trilha final (a exata vem do arquivo mixado)."""
    if not durations:
        return 0.0
    n = len(durations)
    xf = effective_crossfade(durations, st)
    gap = st.gap if xf == 0 else 0.0
    return sum(durations) - (n - 1) * xf + (n - 1) * gap


def build_mix_command(tracks: list[Track], st: AudioSettings, out: Path) -> list[str]:
    """Comando ffmpeg (lista de args, sem shell) que gera a trilha (sem fades; ver apply_fades)."""
    if not tracks:
        raise WavegenError("Adicione pelo menos uma música.", code="no_audio")
    tr = ordered(tracks, st)
    durs = [t.duration for t in tr]
    xf = effective_crossfade(durs, st)
    gap = st.gap if xf == 0 else 0.0

    cmd = [ffmpeg_path(), "-hide_banner", "-nostdin", "-v", "error", "-y"]
    for t in tr:
        cmd += ["-i", media_arg(t.path)]
    parts = []
    n = len(tr)
    for i in range(n):
        chain = (f"[{i}:a:0]aresample={SR}:resampler=soxr:precision=28,"
                 f"aformat=sample_fmts=flt:channel_layouts=stereo,asetpts=N/SR/TB")
        if gap > 0 and i < n - 1:
            chain += f",apad=pad_dur={gap:.6f}"
        parts.append(chain + f"[a{i}]")
    if n == 1:
        last = "a0"
    elif xf > 0:
        prev = "a0"
        for i in range(1, n):
            parts.append(f"[{prev}][a{i}]acrossfade=d={xf:.6f}:c1=tri:c2=tri[x{i}]")
            prev = f"x{i}"
        last = prev
    else:
        parts.append("".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[cat]")
        last = "cat"
    if abs(st.volume_db) > 1e-6:
        parts.append(f"[{last}]volume={st.volume_db:.3f}dB:precision=float[vol]")
        last = "vol"
    cmd += ["-filter_complex", ";".join(parts), "-map", f"[{last}]",
            "-c:a", "pcm_f32le", "-ar", str(SR), "-ac", str(CHANNELS),
            "-rf64", "auto", "-bitexact", "-f", "wav", media_arg(out)]
    return cmd


# ----------------------------------------------------------------------------- WAV em memória mapeada

def wav_layout(path: Path) -> tuple[int, int, int, int]:
    """(offset dos dados, nº de quadros de áudio, canais, taxa) de um WAV/RF64 float32."""
    with open(path, "rb") as f:
        head = f.read(12)
        if head[:4] not in (b"RIFF", b"RF64") or head[8:12] != b"WAVE":
            raise WavegenError("Arquivo de trilha em cache inválido.", code="cache")
        channels = sr = bits = 0
        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                raise WavegenError("Arquivo de trilha em cache sem dados.", code="cache")
            cid, size = hdr[:4], struct.unpack("<I", hdr[4:])[0]
            if cid == b"fmt ":
                fmt = f.read(size)
                _, channels, sr = struct.unpack("<HHI", fmt[:8])
                bits = struct.unpack("<H", fmt[14:16])[0]
                continue
            if cid == b"data":
                offset = f.tell()
                break
            f.seek(size + (size & 1), 1)
    total = path.stat().st_size - offset
    frame_bytes = channels * bits // 8
    return offset, total // frame_bytes, channels, sr


def open_pcm(path: Path, mode: str = "r") -> tuple[np.memmap, int]:
    off, n, ch, sr = wav_layout(path)
    return np.memmap(path, dtype="<f4", mode=mode, offset=off, shape=(n, ch)), sr


def apply_fades(path: Path, st: AudioSettings) -> None:
    """Fade-in/out lineares aplicados in-place com precisão de amostra."""
    if st.fade_in <= 0 and st.fade_out <= 0:
        return
    pcm, sr = open_pcm(path, "r+")
    n = pcm.shape[0]
    if st.fade_in > 0:
        k = min(n, int(round(st.fade_in * sr)))
        pcm[:k] *= np.linspace(0.0, 1.0, k, endpoint=False, dtype=np.float32)[:, None]
    if st.fade_out > 0:
        k = min(n, int(round(st.fade_out * sr)))
        pcm[n - k:] *= np.linspace(1.0, 0.0, k, endpoint=True, dtype=np.float32)[:, None]
    pcm.flush()
    del pcm


# ----------------------------------------------------------------------------- cache

def mix_key(tracks: list[Track], st: AudioSettings) -> str:
    items = []
    for t in tracks:
        p = Path(t.path)
        s = p.stat()
        items.append([str(p.absolute()), s.st_size, s.st_mtime_ns])
    blob = json.dumps({"v": 2, "tracks": items, "audio": asdict(st), "sr": SR}, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def _prune(folder: Path, pattern: str, keep: int) -> None:
    files = sorted((p for p in folder.glob(pattern) if ".tmp." not in p.name),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


def ensure_mix(tracks: list[Track], st: AudioSettings,
               progress: Callable[[float], None] | None = None,
               cancel: threading.Event | None = None) -> Path:
    """Gera (ou reaproveita do cache) a trilha final. Retorna o caminho do WAV."""
    for t in tracks:
        if not t.exists():
            raise WavegenError(f"A música “{t.name}” não foi encontrada em {t.path}.",
                               code="missing_file")
    folder = CACHE_DIR / "mix"
    folder.mkdir(parents=True, exist_ok=True)
    key = mix_key(tracks, st)
    out = folder / f"{key}.wav"
    if out.exists():
        os.utime(out)
        return out
    tmp = folder / f"{key}.tmp.wav"
    cmd = build_mix_command(tracks, st, tmp)
    expected = total_duration([t.duration for t in ordered(tracks, st)], st)
    cmd[-1:-1] = ["-progress", "pipe:1", "-nostats"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    err_chunks: list[str] = []
    t_err = threading.Thread(target=lambda: err_chunks.append(proc.stderr.read()), daemon=True)
    t_err.start()
    for line in proc.stdout:
        if cancel is not None and cancel.is_set():
            proc.kill()
            break
        if line.startswith("out_time_us=") and progress and expected > 0:
            try:
                progress(min(1.0, int(line.split("=")[1]) / 1e6 / expected))
            except ValueError:
                pass
    proc.wait()
    t_err.join(timeout=5)
    proc.stdout.close()
    proc.stderr.close()
    if cancel is not None and cancel.is_set():
        tmp.unlink(missing_ok=True)
        raise WavegenError("Operação cancelada.", code="cancelled")
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise translate_ffmpeg_error("".join(err_chunks),
                                     "Não foi possível montar a trilha sonora a partir das músicas.")
    apply_fades(tmp, st)
    os.replace(tmp, out)
    _prune(folder, "*.wav", keep=4)
    if progress:
        progress(1.0)
    return out
