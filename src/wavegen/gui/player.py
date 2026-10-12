"""Reprodução de áudio para a pré-visualização (PortAudio via sounddevice).

O relógio do preview é a posição do áudio que já foi entregue à placa de som, então a
animação acompanha o que se ouve. Sem placa de som, o player funciona em modo "mudo" com
relógio do sistema.
"""

from __future__ import annotations

import threading
import time

import numpy as np


class AudioPlayer:
    def __init__(self):
        self.pcm: np.ndarray | None = None
        self.sr = 48000
        self.pos = 0                 # amostra atual
        self.playing = False
        self.volume = 1.0
        self.error: str | None = None
        self._lock = threading.Lock()
        self._stream = None
        self._sd = None
        self._mute_t0 = None
        self._mute_pos0 = 0
        try:
            import sounddevice as sd  # noqa: F401
            self._sd = sd
        except Exception as e:  # PortAudio ausente
            self.error = f"Áudio indisponível ({e}). O preview será mudo."

    # ------------------------------------------------------------------ fonte
    def set_source(self, pcm: np.ndarray | None, sr: int):
        with self._lock:
            restart = self.playing
            self._close()
            self.pcm, self.sr = pcm, sr
            n = pcm.shape[0] if pcm is not None else 0
            self.pos = min(self.pos, max(n - 1, 0))
            self.playing = False
        if restart:
            self.play()

    @property
    def length(self) -> int:
        return 0 if self.pcm is None else self.pcm.shape[0]

    # ------------------------------------------------------------------ controle
    def play(self):
        if self.pcm is None or self.length == 0:
            return
        # Fecha a saída anterior (inclusive a que terminou sozinha no fim da música).
        # Antes, cada play depois do fim abria mais uma saída e todas consumiam a mesma
        # música: o áudio e o preview aceleravam 2×, 3×… a cada repetição.
        self._close()
        if self.pos >= self.length - 1:
            self.pos = 0
        self.playing = True
        if self._sd is not None:
            try:
                ch = self.pcm.shape[1] if self.pcm.ndim == 2 else 1
                stream = None

                def cb(out, frames, t, status):
                    self._callback(out, frames, t, status, stream)
                stream = self._sd.OutputStream(samplerate=self.sr, channels=ch, dtype="float32",
                                               callback=cb, latency="low")
                self._stream = stream
                stream.start()
                return
            except Exception as e:
                self.error = f"Não foi possível abrir a saída de áudio ({e}). Preview mudo."
                self._stream = None
        self._mute_t0 = time.monotonic()
        self._mute_pos0 = self.pos

    def pause(self):
        self.position()          # congela posição (modo mudo)
        self.playing = False
        self._close()

    def seek(self, sample: int):
        with self._lock:
            self.pos = int(min(max(sample, 0), max(self.length - 1, 0)))
            self._mute_t0 = time.monotonic()
            self._mute_pos0 = self.pos

    def position(self) -> int:
        if self.playing and self._stream is None and self._mute_t0 is not None:
            p = self._mute_pos0 + int((time.monotonic() - self._mute_t0) * self.sr)
            if p >= self.length:
                p = self.length - 1
                self.playing = False
            self.pos = p
        return self.pos

    def close(self):
        self.playing = False
        self._close()

    # ------------------------------------------------------------------ interno
    def _close(self):
        s, self._stream = self._stream, None
        if s is not None:
            try:
                s.stop()
                s.close()
            except Exception:
                pass

    def _callback(self, out, frames, _time, _status, stream=None):
        with self._lock:
            pcm = self.pcm
            if stream is not None and stream is not self._stream:
                out.fill(0)                      # saída antiga: nunca avança a posição
                raise self._sd.CallbackStop
            if pcm is None or not self.playing:
                out.fill(0)
                return
            a = self.pos
            b = min(a + frames, pcm.shape[0])
            k = b - a
            chunk = np.asarray(pcm[a:b], dtype=np.float32)
            if chunk.ndim == 1:
                chunk = chunk[:, None]
            out[:k] = chunk * self.volume
            self.pos = b
            if k < frames:
                out[k:] = 0
                self.playing = False
                raise self._sd.CallbackStop       # fim da música: encerra esta saída
