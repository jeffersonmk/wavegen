"""Renderização de quadros em paralelo (vários processos + memória compartilhada).

O desenho do Qt não libera o GIL, então threads não aceleram; processos sim. Cada processo
de trabalho tem seu próprio Compositor e renderiza quadros intercalados (o processo i faz
os quadros i, i+N, i+2N…) direto em "vagas" de memória compartilhada. O processo principal lê as
vagas em ordem e envia ao FFmpeg — sem cópias extras.

Como cada quadro depende só do seu índice (ver SeriesProvider), o resultado é idêntico
qualquer que seja o número de processos.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import queue
import traceback
from multiprocessing import shared_memory


def default_workers(width: int, height: int, encoder: str) -> int:
    cpus = os.cpu_count() or 2
    reserve = 1 if encoder == "vaapi" else max(2, cpus // 3)  # deixa CPU para o x264
    n = max(1, cpus - reserve)
    if width * height >= 3840 * 2160:
        n = min(n, 6)
    return min(n, 8)


def _worker(idx, nw, project_dict, mix_path, fps_t, W, H, transparent, n0, n1,
            shm_name, slot_bytes, free_q, done_q, stop):
    try:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from fractions import Fraction
        from pathlib import Path

        import numpy as np
        from PySide6.QtGui import QGuiApplication, QImage

        QGuiApplication.instance() or QGuiApplication([])
        from .analysis import ensure_features
        from .compositor import Compositor
        from .playlist import open_pcm
        from .project import Project

        project = Project.from_dict(project_dict)
        feats = ensure_features(Path(mix_path), Fraction(*fps_t))
        pcm, _ = open_pcm(Path(mix_path))
        comp = Compositor(project, W, H, feats, pcm, transparent=transparent)
        shm = shared_memory.SharedMemory(name=shm_name)
        buf = np.ndarray((shm.size,), dtype=np.uint8, buffer=shm.buf)
        for n in range(n0 + idx, n1, nw):        # quadros intercalados entre os processos
            while True:
                if stop.is_set():
                    return
                try:
                    slot = free_q.get(timeout=0.2)
                    break
                except queue.Empty:
                    continue
            img = comp.render(n)
            if transparent:
                img = img.convertToFormat(QImage.Format_ARGB32)
            off = slot * slot_bytes
            buf[off:off + slot_bytes] = np.frombuffer(img.constBits(), dtype=np.uint8,
                                                      count=slot_bytes)
            done_q.put((n, slot))
        del buf
        shm.close()
    except Exception:
        done_q.put(("error", traceback.format_exc()))


class ParallelFrames:
    """Iterador de quadros (memoryview BGRA) na ordem correta."""

    def __init__(self, project, mix_path, fps, W, H, transparent, n0, n1, workers,
                 slots_per_worker: int | None = None):
        self.W, self.H = W, H
        self.n0, self.n1 = n0, n1
        self.nw = max(1, min(workers, n1 - n0))
        self.slot_bytes = W * H * 4
        spw = slots_per_worker or (2 if W * H >= 3840 * 2160 else 3)
        ctx = mp.get_context("spawn")
        self.shm = shared_memory.SharedMemory(create=True, size=self.slot_bytes * spw * self.nw)
        self.stop = ctx.Event()
        self.free_qs, self.done_qs, self.procs = [], [], []
        pd = project.to_dict()
        for i in range(self.nw):
            fq, dq = ctx.Queue(), ctx.Queue()
            for s in range(spw):
                fq.put(i * spw + s)
            p = ctx.Process(target=_worker, daemon=True,
                            args=(i, self.nw, pd, str(mix_path), (fps.numerator, fps.denominator),
                                  W, H, transparent, n0, n1, self.shm.name, self.slot_bytes,
                                  fq, dq, self.stop))
            p.start()
            self.free_qs.append(fq)
            self.done_qs.append(dq)
            self.procs.append(p)

    def owner(self, n: int) -> int:
        return (n - self.n0) % self.nw

    def __iter__(self):
        mv = self.shm.buf
        try:
            yield from self._iter(mv)
        finally:
            mv.release()

    def _iter(self, mv):
        for n in range(self.n0, self.n1):
            w = self.owner(n)
            while True:
                try:
                    item = self.done_qs[w].get(timeout=1.0)
                    break
                except queue.Empty:
                    if not self.procs[w].is_alive():
                        raise RuntimeError(f"O processo de renderização {w} terminou inesperadamente "
                                           f"(código {self.procs[w].exitcode}).")
            if item[0] == "error":
                raise RuntimeError(item[1])
            m, slot = item
            assert m == n, (m, n)
            off = slot * self.slot_bytes
            view = mv[off:off + self.slot_bytes]
            try:
                yield view
            finally:
                view.release()          # sempre: senão a memória compartilhada não fecha
            self.free_qs[w].put(slot)

    def close(self):
        self.stop.set()
        for p in self.procs:
            p.join(timeout=2)
            if p.is_alive():
                p.terminate()
                p.join(timeout=2)
        for q in self.free_qs + self.done_qs:
            q.close()
            q.cancel_join_thread()
        try:
            self.shm.close()
        except BufferError:
            pass
        try:
            self.shm.unlink()           # remove de /dev/shm mesmo se ainda houver referência
        except FileNotFoundError:
            pass
