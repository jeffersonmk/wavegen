"""Gera as imagens do README (docs/images/) com conteúdo de demonstração neutro.

Fundo, vídeo e músicas são sintéticos (nada pessoal vai para o repositório).
Uso: QT_QPA_PLATFORM=offscreen uv run python tests/readme_shots.py [PASTA]   (padrão docs/images)
"""

import os as _os
import tempfile as _tf
# preferências e cache isolados: o teste nunca altera ~/.config/wavegen nem ~/.cache/wavegen
_iso = _tf.mkdtemp(prefix="wavegen-iso-")
_os.environ["XDG_CONFIG_HOME"] = _os.path.join(_iso, "config")
_os.environ["XDG_CACHE_HOME"] = _os.path.join(_iso, "cache")


import functools
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

print = functools.partial(print, flush=True)  # noqa: A001

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/images")
OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp(prefix="wavegen-shots-"))

from PySide6.QtCore import QEventLoop, QPointF, QTimer, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QRadialGradient, QLinearGradient  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication(["wavegen"])

from wavegen.ffmpeg import ffmpeg_path  # noqa: E402
from wavegen.gui import app as gapp  # noqa: E402
from wavegen.gui.demo import SR, demo_pcm  # noqa: E402


# ------------------------------------------------------------------ mídia de demonstração
def make_background(path: Path, w=1920, h=1080, hue=265):
    img = QImage(w, h, QImage.Format_RGB32)
    p = QPainter(img)
    g = QLinearGradient(0, 0, w, h)
    g.setColorAt(0, QColor.fromHsv(hue, 170, 70))
    g.setColorAt(1, QColor.fromHsv((hue + 50) % 360, 200, 25))
    p.fillRect(img.rect(), g)
    rng = np.random.default_rng(3)
    for _ in range(14):
        x, y, r = rng.uniform(0, w), rng.uniform(0, h), rng.uniform(80, 420)
        rg = QRadialGradient(QPointF(x, y), r)
        c = QColor.fromHsv(int(hue + rng.uniform(-40, 60)) % 360, 200, 255, 70)
        rg.setColorAt(0, c)
        c2 = QColor(c)
        c2.setAlpha(0)
        rg.setColorAt(1, c2)
        p.setPen(Qt.NoPen)
        p.setBrush(rg)
        p.drawEllipse(QPointF(x, y), r, r)
    p.end()
    img.save(str(path))


def make_cover(path: Path):
    make_background(path, 800, 800, hue=20)


def make_video(path: Path):
    subprocess.run([ffmpeg_path(), "-v", "error", "-y", "-f", "lavfi", "-i",
                    "gradients=s=1280x720:c0=0x2a1b5c:c1=0x0b5e7a:c2=0x5c1b4a:n=3:speed=0.02:d=8:r=30",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)


def make_track(path: Path, title: str, seconds: float, pitch: float):
    y = demo_pcm(16.0)
    reps = int(seconds / 16) + 1
    y = np.tile(y, (reps, 1))[: int(seconds * SR)]
    idx = np.clip((np.arange(len(y)) * pitch).astype(int), 0, len(y) - 1)
    y = y[idx]
    fade = np.linspace(0, 1, SR)
    y[:SR] *= fade[:, None]
    y[-SR:] *= fade[::-1, None]
    raw = TMP / "t.f32"
    y.astype(np.float32).tofile(raw)
    subprocess.run([ffmpeg_path(), "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2",
                    "-i", str(raw), "-metadata", f"title={title}",
                    "-c:a", "flac", str(path)], check=True)


bg = TMP / "fundo.png"
cover = TMP / "capa.png"
video = TMP / "fundo animado.mp4"
make_background(bg)
make_cover(cover)
make_video(video)
tracks = []
for i, (t, s, pch) in enumerate([("Neon Skyline", 95, 1.0), ("Midnight Drive", 80, 0.94),
                                 ("Afterglow", 110, 1.06)]):
    f = TMP / f"{i + 1:02d} {t}.flac"
    make_track(f, t, s, pch)
    tracks.append(str(f))
print("mídia pronta em", TMP)


# ------------------------------------------------------------------ janela
def wait(ms=300, until=None, timeout=90.0):
    t0 = time.time()
    while True:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()
        if until is None or until() or time.time() - t0 > timeout:
            return


def shot(widget, name, width=None):
    wait(300)
    img = widget.grab().toImage()
    if width and img.width() > width:
        img = img.scaledToWidth(width, Qt.SmoothTransformation)
    img.save(str(OUT / name))
    print("imagem:", OUT / name)


gapp.setup_theme(app)
w = gapp.MainWindow()
w.resize(1600, 950)
w.show()
wait(800)
w.set_image(str(bg))
w.add_files(tracks)
wait(300, until=lambda: len(w.project.tracks) == len(tracks))
wait(300, until=lambda: not w.using_demo and w.mix_task is None, timeout=180)


def seek(sec):
    w.player.seek(int(sec * w.sr))
    w._render_current(force=True)


def style(sid, **params):
    w.gallery.select(sid)
    w.set_style(sid)
    for k, v in params.items():
        w.set_param(k, v)
    wait(300)


# 1. janela principal com a galeria de estilos
style("circle_spectrum")
seek(40)
w.tabs.setCurrentIndex(0)
shot(w, "01-janela-principal.png", 1600)

# 2. Vinil com capa + nome da faixa e contador
style("vinyl", cover=str(cover))
seek(118)
w.tabs.setCurrentIndex(1)
shot(w, "02-vinil-nome-contador.png", 1600)
w.preview.grab().toImage().save(str(TMP / "preview_vinil.png"))

# 3. fundo em vídeo (com loop)
w.set_image(str(video))
wait(500)
style("neon")
seek(60)
w.tabs.setCurrentIndex(2)
shot(w, "03-fundo-video-loop.png", 1600)

# 4. exportação
w.set_image(str(bg))
style("bars")
seek(30)
w.tabs.setCurrentIndex(4)
shot(w, "04-exportar.png", 1600)

# 5. lista de faixas para o YouTube
from wavegen.gui.tracklist_dialog import TracklistDialog  # noqa: E402

d = TracklistDialog(w.project, str(TMP), w)
d.resize(760, 520)
d.show()
shot(d, "05-lista-de-faixas.png")
d.close()

w.dirty = False
w.close()

# 6. galeria de todos os estilos (renderizados de verdade, com o mesmo áudio)
from fractions import Fraction  # noqa: E402

from PySide6.QtGui import QFont  # noqa: E402

from wavegen.analysis import ensure_features  # noqa: E402
from wavegen.compositor import Compositor  # noqa: E402
from wavegen.playlist import AudioSettings, Track, ensure_mix, open_pcm  # noqa: E402
from wavegen.project import Project  # noqa: E402
from wavegen.styles import style_list  # noqa: E402

tr = [Track.load(t) for t in tracks]
mix = ensure_mix(tr, AudioSettings())
feats = ensure_features(mix, Fraction(30))
pcm, _ = open_pcm(mix)
styles = style_list()
cw, ch, cols = 480, 270, 3
rows = (len(styles) + cols - 1) // cols
sheet = QImage(cols * cw, rows * ch, QImage.Format_RGB32)
sheet.fill(QColor("#111"))
p = QPainter(sheet)
f = QFont()
f.setPixelSize(17)
f.setBold(True)
p.setFont(f)
for i, cls in enumerate(styles):
    pr = Project()
    pr.tracks = tr
    pr.style = cls.id
    pr.background.image = str(bg)
    if cls.id == "vinyl":
        pr.style_params["vinyl"] = {"cover": str(cover)}
    img = Compositor(pr, 960, 540, feats, pcm).render(int(41.3 * 30))
    x, y = (i % cols) * cw, (i // cols) * ch
    p.drawImage(x, y, img.scaled(cw - 4, ch - 4, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
    p.setPen(QColor(0, 0, 0, 180))
    p.drawText(x + 13, y + 29, cls.name)
    p.setPen(QColor("white"))
    p.drawText(x + 12, y + 28, cls.name)
p.end()
sheet.save(str(OUT / "06-estilos.png"))
print("imagem:", OUT / "06-estilos.png")

# 7. quadro do vídeo final (Vinil com capa em 1920×1080)
pr = Project()
pr.tracks = tr
pr.style = "vinyl"
pr.background.image = str(bg)
pr.style_params["vinyl"] = {"cover": str(cover)}
Compositor(pr, 1920, 1080, feats, pcm).render(int(118 * 30)).scaledToWidth(
    1280, Qt.SmoothTransformation).save(str(OUT / "07-resultado-vinil.jpg"), quality=88)
print("imagem:", OUT / "07-resultado-vinil.jpg")
app.quit()
