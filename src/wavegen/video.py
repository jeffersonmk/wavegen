"""Vídeo como fundo: sondagem, filtro de enquadramento (FFmpeg) e decodificador para o preview.

O enquadramento do vídeo é feito pelo próprio FFmpeg, com o MESMO filtro no preview e na
exportação, então o resultado do preview corresponde ao vídeo final. Na exportação, o
visualizador (com transparência) é sobreposto ao vídeo pelo filtro ``overlay``.

Loop: com ``loop`` ligado o vídeo recomeça até o fim da trilha; desligado, o último quadro
fica parado até o fim.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .ffmpeg import WavegenError, ffmpeg_path, ffprobe_path, media_arg

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg", ".wmv", ".flv",
              ".gif", ".ts", ".mts", ".m2ts", ".ogv"}


def is_video(path: str) -> bool:
    return bool(path) and Path(path).suffix.lower() in VIDEO_EXTS


@dataclass
class VideoInfo:
    width: int          # já considerando a rotação
    height: int
    duration: float
    fps: float
    has_audio: bool


def probe_video(path: str) -> VideoInfo:
    p = Path(path)
    if not p.is_file():
        raise WavegenError(f"Vídeo não encontrado: {p}", code="missing_file")
    r = subprocess.run([ffprobe_path(), "-v", "error", "-show_entries",
                        "stream=codec_type,width,height,avg_frame_rate,duration:"
                        "stream_side_data=rotation:stream_tags=rotate:format=duration",
                        "-of", "json", media_arg(p)], capture_output=True, text=True)
    try:
        data = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        data = {}
    streams = data.get("streams") or []
    vs = [s for s in streams if s.get("codec_type") == "video" and s.get("width")]
    if r.returncode != 0 or not vs:
        raise WavegenError(f"“{p.name}” não parece ser um vídeo válido ou suportado.",
                           details=r.stderr.strip(), code="bad_video")
    v = vs[0]
    w, h = int(v["width"]), int(v["height"])
    rot = 0
    for sd in v.get("side_data_list") or []:
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    rot = int(float((v.get("tags") or {}).get("rotate", rot)))
    if abs(rot) % 180 == 90:
        w, h = h, w
    dur = 0.0
    for src in (v, data.get("format", {})):
        d = src.get("duration")
        if d not in (None, "N/A"):
            dur = float(d)
            break
    try:
        fps = float(Fraction(v.get("avg_frame_rate") or "0/1"))
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    if dur <= 0:
        raise WavegenError(f"Não foi possível determinar a duração de “{p.name}”.", code="bad_video")
    return VideoInfo(w, h, dur, fps, any(s.get("codec_type") == "audio" for s in streams))


def _ffcolor(c: str) -> str:
    c = c.lstrip("#")
    if len(c) == 8:          # #AARRGGBB (Qt) -> RRGGBB
        c = c[2:]
    return "0x" + (c if len(c) == 6 else "101014")


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def background_chain(bg, W: int, H: int, iw: int, ih: int, fps: Fraction, loop: bool,
                     src: str = "[vin]", out: str = "[bg]") -> str:
    """Filtro FFmpeg (filter_complex) que transforma o vídeo de entrada no fundo W×H,
    com a mesma geometria de compositor.compose_background (preencher/inteira, posição, zoom,
    preenchimento desfocado ou cor, escurecer)."""
    f = f"{fps.numerator}/{fps.denominator}"
    head = f"{src}fps={f}"
    if not loop:
        head += ",tpad=stop_mode=clone:stop=-1"        # segura o último quadro
    cover = max(W / iw, H / ih)
    contain = min(W / iw, H / ih)
    dim = ""
    if bg.dim > 0:
        dim = f",drawbox=c=black@{min(bg.dim, 0.95):.3f}:t=fill"
    if bg.fit != "contain":
        s = cover * max(bg.zoom, 1.0)
        dw, dh = max(W, _even(iw * s)), max(H, _even(ih * s))
        cx = int(round((dw - W) * bg.focus_x))
        cy = int(round((dh - H) * bg.focus_y))
        return (f"{head},scale={dw}:{dh}:flags=bicubic,crop={W}:{H}:{cx}:{cy},setsar=1,"
                f"format=bgra{dim}{out}")
    s = contain * bg.zoom
    dw, dh = _even(iw * s), _even(ih * s)
    x = int(round((W - dw) * bg.focus_x))
    y = int(round((H - dh) * bg.focus_y))
    if bg.fill == "blur":
        cw, ch = max(W, _even(iw * cover)), max(H, _even(ih * cover))
        sw, sh = max(2, _even(iw * cover / 48)), max(2, _even(ih * cover / 48))
        base = (f"scale={sw}:{sh}:flags=area,scale={cw}:{ch}:flags=bilinear,"
                f"crop={W}:{H}:{(cw - W) // 2}:{(ch - H) // 2},setsar=1,format=bgra,"
                f"drawbox=c=black@0.431:t=fill")
    else:
        base = f"scale={W}:{H},setsar=1,format=bgra,drawbox=c={_ffcolor(bg.color)}:t=fill"
    return (f"{head},split[vb][vf];[vb]{base}[vbase];[vf]scale={dw}:{dh}:flags=bicubic,"
            f"setsar=1,format=bgra[vfg];[vbase][vfg]overlay={x}:{y}:format=auto,"
            f"format=bgra{dim}{out}")


def input_args(path: str, start: float, info: VideoInfo, loop: bool) -> list[str]:
    """Argumentos da entrada do vídeo começando no instante ``start`` do vídeo final."""
    if loop:
        ss = start % info.duration if info.duration > 0 else 0.0
        return ["-stream_loop", "-1", "-ss", f"{ss:.6f}", "-i", media_arg(path)]
    ss = min(start, max(0.0, info.duration - 0.25))
    return ["-ss", f"{ss:.6f}", "-i", media_arg(path)]


@dataclass
class VideoBackgroundSpec:
    """O que a exportação precisa para usar o vídeo de fundo."""
    args: list[str]      # argumentos de entrada (… -i arquivo)
    chain: str           # filtro "[vin]…[bg]" (o encoder troca [vin] pelo índice da entrada)


def export_spec(bg, W: int, H: int, fps: Fraction, start: float) -> VideoBackgroundSpec:
    info = probe_video(bg.image)
    return VideoBackgroundSpec(input_args(bg.image, start, info, bg.loop_video),
                               background_chain(bg, W, H, info.width, info.height, fps,
                                                bg.loop_video))


class VideoDecoder:
    """Quadros de fundo (já enquadrados em W×H, BGRA) para o preview, na grade de quadros
    do vídeo final. Lê sequencialmente numa thread; um salto reinicia o FFmpeg no ponto."""

    def __init__(self, bg, W: int, H: int, fps: Fraction):
        self.info = probe_video(bg.image)
        self.path = bg.image
        self.loop = bg.loop_video
        self.W, self.H, self.fps = W, H, fps
        self.chain = background_chain(bg, W, H, self.info.width, self.info.height, fps, self.loop,
                                      src="[0:v]", out="[out]")
        self.frame_bytes = W * H * 4
        self.proc: subprocess.Popen | None = None
        self.q: queue.Queue | None = None
        self.next_n = -1           # próximo índice que sairá da fila
        self.last: tuple[int, bytes] | None = None
        self._stop = threading.Event()

    def _start(self, n: int):
        self.close()
        self._stop = threading.Event()
        t0 = n * self.fps.denominator / self.fps.numerator
        cmd = [ffmpeg_path(), "-hide_banner", "-nostdin", "-v", "error"]
        cmd += input_args(self.path, t0, self.info, self.loop)
        cmd += ["-filter_complex", self.chain, "-map", "[out]", "-an", "-f", "rawvideo",
                "-pix_fmt", "bgra", "pipe:1"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     bufsize=self.frame_bytes * 2)
        self.q = queue.Queue(maxsize=8)
        self.next_n = n
        threading.Thread(target=self._reader, args=(self.proc, self.q, self._stop, n),
                         daemon=True).start()

    def _reader(self, proc, q, stop, n):
        out = proc.stdout
        while not stop.is_set():
            buf = out.read(self.frame_bytes)
            if not buf or len(buf) < self.frame_bytes:
                q.put(None)
                return
            while not stop.is_set():
                try:
                    q.put((n, buf), timeout=0.2)
                    break
                except queue.Full:
                    continue
            n += 1

    def frame(self, n: int, timeout: float = 3.0):
        """QImage do fundo no quadro n. Com timeout curto (reprodução), se o quadro ainda
        não estiver pronto devolve o mais recente em vez de travar a interface."""
        from PySide6.QtGui import QImage
        if self.last is not None and self.last[0] == n:
            return self._img(self.last[1])
        if self.proc is None or n < self.next_n or n > self.next_n + 45:
            self._start(n)
        while True:
            try:
                item = self.q.get(timeout=timeout)
            except queue.Empty:
                break
            if item is None:
                self.proc = None          # acabou (vídeo sem loop): fica o último quadro
                break
            k, buf = item
            self.next_n = k + 1
            self.last = (k, buf)
            if k >= n:
                break
        if self.last is None:
            img = QImage(self.W, self.H, QImage.Format_ARGB32_Premultiplied)
            img.fill(0xFF000000)
            return img
        return self._img(self.last[1])

    def _img(self, buf: bytes):
        from PySide6.QtGui import QImage
        img = QImage(buf, self.W, self.H, self.W * 4, QImage.Format_ARGB32)
        return img.convertToFormat(QImage.Format_ARGB32_Premultiplied)

    def close(self):
        self._stop.set()
        p, self.proc = self.proc, None
        if p is not None:
            try:
                p.kill()
                p.wait(timeout=2)
                p.stdout.close()
            except Exception:  # noqa: BLE001
                pass
