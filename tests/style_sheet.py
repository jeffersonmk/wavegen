"""Gera uma folha de contato com todos os estilos (para revisão visual).

Uso: QT_QPA_PLATFORM=offscreen uv run python tests/style_sheet.py AUDIO... --out sheet.png [--t 60]
"""

import os as _os
import tempfile as _tf
# preferências e cache isolados: o teste nunca altera ~/.config/wavegen nem ~/.cache/wavegen
_iso = _tf.mkdtemp(prefix="wavegen-iso-")
_os.environ["XDG_CONFIG_HOME"] = _os.path.join(_iso, "config")
_os.environ["XDG_CACHE_HOME"] = _os.path.join(_iso, "cache")


import argparse
import time
from fractions import Fraction

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter

app = QGuiApplication([])

from wavegen.analysis import ensure_features  # noqa: E402
from wavegen.compositor import Compositor  # noqa: E402
from wavegen.playlist import AudioSettings, Track, ensure_mix, open_pcm  # noqa: E402
from wavegen.project import Project  # noqa: E402
from wavegen.styles import style_list  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("audio", nargs="+")
ap.add_argument("--out", default="sheet.png")
ap.add_argument("--t", type=float, default=60.0)
ap.add_argument("--image", default="")
ap.add_argument("--glow", type=float, default=None)
a = ap.parse_args()

tracks = [Track.load(f) for f in a.audio]
mix = ensure_mix(tracks, AudioSettings())
feats = ensure_features(mix, Fraction(30))
pcm, _ = open_pcm(mix)
styles = style_list()
TW, TH = 960, 540
sheet = QImage(TW * 2, TH * ((len(styles) + 1) // 2), QImage.Format_ARGB32_Premultiplied)
sheet.fill(QColor("#000"))
sp = QPainter(sheet)
sp.setFont(QFont("Sans", 16))
n0 = int(a.t * 30)
for i, cls in enumerate(styles):
    pr = Project()
    pr.style = cls.id
    pr.background.color = "#181820"
    pr.background.image = a.image
    if a.glow is not None:
        pr.style_params[cls.id] = {"glow": a.glow}
    c = Compositor(pr, 1920, 1080, feats, pcm)
    for k in range(40):           # aquece a suavização
        c.render(n0 - 40 + k)
    t = time.time()
    for k in range(20):
        img = c.render(n0 + k)
    ms = (time.time() - t) / 20 * 1000
    print(f"{cls.id:16s} {ms:6.1f} ms/quadro (1080p)")
    sp.drawImage(QRectF((i % 2) * TW, (i // 2) * TH, TW, TH), img)
    sp.setPen(QColor("white"))
    sp.drawText((i % 2) * TW + 14, (i // 2) * TH + 30, f"{i + 1}. {cls.name}")
sp.end()
sheet.save(a.out)
print(a.out)
