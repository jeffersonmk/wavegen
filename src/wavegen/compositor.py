"""Composição dos quadros: fundo + visualizador (+ brilho). Usado pelo preview E pela exportação.

Por usar exatamente o mesmo código nos dois casos, posição, proporções, cores e movimento
do preview correspondem ao vídeo final (o preview só usa uma resolução menor).
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter

from .analysis import Features, SeriesProvider, envelope_window, scope
from .ffmpeg import WavegenError, ffmpeg_path, media_arg
from .project import BackgroundSettings, Box, Project
from .styles import FrameData, get_style
from .subscribe import active_start, schedule
from .subscribe import draw as draw_subscribe
from .video import is_video

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
FMT = QImage.Format_ARGB32_Premultiplied


def load_image(path: str) -> QImage:
    """Carrega a imagem respeitando a rotação EXIF. Se o Qt não ler, tenta via FFmpeg."""
    p = Path(path)
    if not p.is_file():
        raise WavegenError(f"Imagem não encontrada: {p}", code="missing_file")
    if is_video(path):
        return video_thumbnail(path)
    reader = QImageReader(str(p))
    reader.setAutoTransform(True)
    img = reader.read()
    if img.isNull():
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "img.png"
            r = subprocess.run([ffmpeg_path(), "-v", "error", "-y", "-i", media_arg(p),
                                "-frames:v", "1", media_arg(out)], capture_output=True, text=True)
            if r.returncode == 0 and out.exists():
                img = QImage(str(out))
        if img.isNull():
            raise WavegenError(f"“{p.name}” não é uma imagem válida ou o formato não é suportado.",
                               details=reader.errorString(), code="bad_image")
    return img.convertToFormat(FMT)


def video_thumbnail(path: str, at: float | None = None) -> QImage:
    """Um quadro do vídeo (por padrão a ~10% da duração) como imagem."""
    from .video import probe_video
    info = probe_video(path)
    t = info.duration * 0.1 if at is None else at
    t = max(0.0, min(t, info.duration - 0.05))
    r = subprocess.run([ffmpeg_path(), "-v", "error", "-ss", f"{t:.3f}", "-i", media_arg(path),
                        "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "pipe:1"],
                       capture_output=True)
    img = QImage.fromData(r.stdout, "PNG")
    if img.isNull():
        raise WavegenError(f"Não foi possível ler um quadro de “{Path(path).name}”.",
                           details=r.stderr.decode(errors="replace"), code="bad_video")
    return img.convertToFormat(FMT)


def compose_background(src: QImage | None, W: int, H: int, bg: BackgroundSettings,
                       transparent: bool = False) -> QImage:
    out = QImage(W, H, FMT)
    if transparent:
        out.fill(Qt.transparent)
        return out
    out.fill(QColor(bg.color))
    if src is None or src.isNull():
        return out
    p = QPainter(out)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    iw, ih = src.width(), src.height()
    cover = max(W / iw, H / ih)
    contain = min(W / iw, H / ih)
    if bg.fit == "contain":
        if bg.fill == "blur":
            # imagem desfocada preenchendo o fundo (reduz muito e amplia de volta)
            small = src.scaled(max(1, int(iw * cover / 24)), max(1, int(ih * cover / 24)),
                               Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            small = small.scaled(max(1, small.width() // 2), max(1, small.height() // 2),
                                 Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            dw, dh = iw * cover, ih * cover
            p.drawImage(QRectF((W - dw) / 2, (H - dh) / 2, dw, dh), small)
            p.fillRect(0, 0, W, H, QColor(0, 0, 0, 110))
        s = contain * bg.zoom
    else:
        s = cover * max(bg.zoom, 1.0)
    dw, dh = iw * s, ih * s
    x = (W - dw) * bg.focus_x
    y = (H - dh) * bg.focus_y
    p.drawImage(QRectF(x, y, dw, dh), src)
    if bg.dim > 0:
        p.fillRect(0, 0, W, H, QColor(0, 0, 0, int(255 * min(bg.dim, 0.95))))
    p.end()
    return out


def box_rect(box: Box, W: int, H: int) -> QRectF:
    w, h = box.w * box.scale * W, box.h * box.scale * H
    return QRectF(box.x * W - w / 2, box.y * H - h / 2, w, h)


class Compositor:
    def __init__(self, project: Project, W: int, H: int, feats: Features,
                 pcm: np.ndarray | None, transparent: bool = False,
                 background: QImage | None = None, source_image: QImage | None = None,
                 bg_provider=None):
        """bg_provider: função n -> QImage W×H (fundo em vídeo no preview); tem prioridade
        sobre ``background``."""
        self.W, self.H = W, H
        self.bg_provider = bg_provider
        self.feats = feats
        self.pcm = pcm
        self.transparent = transparent
        self.style = get_style(project.style)
        self.prm = project.params_for()
        # capa vazia = imagem de fundo (com fundo em vídeo, usa a capa padrão)
        img = project.background.image
        self.prm["_project_image"] = "" if is_video(img) else img
        self.box = project.box_for()
        self._init_tracks(project)
        self.sub = project.subscribe
        self.sub_starts = schedule(self.sub, float(feats.duration))
        self.resp = project.response
        self.u = min(W, H) / 1080.0
        self.gain = 10 ** (self.resp.sensitivity_db / 20)
        if background is None:
            if source_image is None and project.background.image and not transparent:
                source_image = load_image(project.background.image)
            background = compose_background(source_image, W, H, project.background, transparent)
        self.bg = background
        self.layer = QImage(W, H, FMT)
        self.provider = None
        nb = self.style.n_bars(self.prm)
        if self.style.uses & {"bands", "level", "peaks"}:
            fmin, fmax = self.style.freq_range(self.prm)
            self.provider = SeriesProvider(feats, nb or 32, self.resp, fmin, fmax,
                                           self.style.peak_fall(self.prm))

    def frame_data(self, n: int) -> FrameData:
        t = n * self.feats.fps.denominator / self.feats.fps.numerator
        d = FrameData(t=t, n=n, gain=self.gain)
        if self.provider is not None:
            d.bands, d.level, d.peaks = self.provider.frame(n)
        if self.pcm is not None:
            pcm, sr = self.pcm, self.feats.sr
            d.scope = lambda win, k: scope(pcm, sr, t, win, k)
        else:
            d.scope = lambda win, k: np.zeros(k, np.float32)
        d.envelope = lambda win, k: envelope_window(self.feats, t, win, k)
        d.envelope_from = lambda t0, win, k: envelope_window(self.feats, t0, win, k)
        self._track_info(d)
        return d

    def _init_tracks(self, project: Project):
        from .tracklist import entries
        try:
            self.entries = entries(project.tracks, project.audio, mark="middle")
        except Exception:  # noqa: BLE001 — sem durações (projeto incompleto): sem nomes
            self.entries = []
        self._starts = np.array([e.start for e in self.entries], dtype=np.float64)

    def _track_info(self, d: FrameData):
        if not self.entries:
            d.track_title, d.track_index, d.track_count = "Nome da música", 1, 1
            d.track_start, d.track_len, d.track_pos = 0.0, float(self.feats.duration), d.t
            return
        i = max(0, int(np.searchsorted(self._starts, d.t, side="right")) - 1)
        e = self.entries[i]
        d.track_title, d.track_index, d.track_count = e.title, e.index, len(self.entries)
        d.track_start, d.track_len = e.start, max(0.0, e.end - e.start)
        d.track_pos = min(max(0.0, d.t - e.start), d.track_len)

    def render(self, n: int, out: QImage | None = None) -> QImage:
        rect = box_rect(self.box, self.W, self.H)
        self.layer.fill(Qt.transparent)
        p = QPainter(self.layer)
        try:
            p.setRenderHint(QPainter.Antialiasing)
            self.style.draw(p, rect, self.frame_data(n), self.prm, self.u)
        finally:
            p.end()

        bg = self.bg_provider(n) if self.bg_provider is not None else self.bg
        frame = out if out is not None else QImage(bg).convertToFormat(FMT)
        if out is not None:
            p = QPainter(frame)
            p.setCompositionMode(QPainter.CompositionMode_Source)
            p.drawImage(0, 0, bg)
            p.end()
        p = QPainter(frame)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        glow = float(self.prm.get("glow", 0.0))
        opacity = float(self.prm.get("opacity", 1.0))
        if glow > 0:
            p.setCompositionMode(QPainter.CompositionMode_Plus)
            for div, k in ((6, 0.9), (16, 0.8)):
                small = self.layer.scaled(max(1, self.W // div), max(1, self.H // div),
                                          Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                p.setOpacity(min(1.0, glow * k * opacity))
                p.drawImage(QRectF(0, 0, self.W, self.H), small)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.setOpacity(opacity)
        p.drawImage(0, 0, self.layer)
        if self.sub_starts:                       # animação "Inscreva-se" (por cima de tudo)
            t = n * self.feats.fps.denominator / self.feats.fps.numerator
            a = active_start(self.sub_starts, t, max(1.0, self.sub.duration))
            if a is not None:
                p.setOpacity(1.0)
                draw_subscribe(p, self.W, self.H, t, a, self.sub)
        p.end()
        return frame
