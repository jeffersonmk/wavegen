"""Estilo "Vinil com capa": capa quadrada (imagem personalizada), disco de vinil girando
saindo de trás dela e um waveform de barras arredondadas que nasce no disco e corre para o lado.
"""

from __future__ import annotations

import math
import os

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QImage, QLinearGradient,
                           QPainterPath, QPen, QRadialGradient)

from .base import ParamSpec, Style, qcolor, register

# ----------------------------------------------------------------------------- imagens

_IMG_CACHE: dict = {}


def cached_image(path: str) -> QImage | None:
    """Carrega (uma vez) a imagem da capa; None se vazia/ausente/inválida."""
    if not path or not os.path.isfile(path):
        return None
    st = os.stat(path)
    key = (path, st.st_mtime_ns, st.st_size)
    if key not in _IMG_CACHE:
        try:
            from ..compositor import load_image
            img = load_image(path)
        except Exception:  # noqa: BLE001 — imagem inválida: desenha a capa padrão
            img = None
        if len(_IMG_CACHE) > 8:
            _IMG_CACHE.clear()
        _IMG_CACHE[key] = img
    return _IMG_CACHE[key]


def _square_crop(img: QImage) -> QRectF:
    w, h = img.width(), img.height()
    s = min(w, h)
    return QRectF((w - s) / 2, (h - s) / 2, s, s)


@register
class Vinyl(Style):
    id = "vinyl"
    name = "Vinil com capa"
    description = ("Capa do álbum (imagem sua) com um disco de vinil girando e um waveform de "
                   "barras que corre a partir do disco.")
    uses = frozenset({"env"})
    common = ("color1", "color2", "gradient", "opacity", "glow")
    overrides = {"color1": "#ffffff", "color2": "#ffffff", "gradient": False}
    params = [
        ParamSpec("cover", "Imagem da capa", "image", "", group="Capa",
                  tip="Vazio = usa a imagem de fundo do vídeo"),
        ParamSpec("border", "Borda da capa", "float", 5, 0, 30, 0.5, group="Capa", suffix=" px"),
        ParamSpec("border_color", "Cor da borda", "color", "#000000", group="Capa"),
        ParamSpec("corner", "Cantos arredondados", "float", 0, 0, 80, 1, group="Capa", suffix=" px"),
        ParamSpec("shadow", "Sombra", "float", 0.35, 0.0, 1.0, 0.05, group="Capa"),
        ParamSpec("disc_out", "Disco para fora da capa", "float", 0.5, 0.0, 1.0, 0.05, group="Disco",
                  tip="0 = escondido atrás da capa, 1 = totalmente para fora"),
        ParamSpec("disc_size", "Tamanho do disco", "float", 0.96, 0.6, 1.2, 0.01, group="Disco",
                  tip="Em relação ao tamanho da capa"),
        ParamSpec("rpm", "Rotação", "float", 33.3, 0, 78, 0.1, group="Disco", suffix=" rpm",
                  tip="33⅓ ou 45 rpm, como um toca-discos; 0 = parado"),
        ParamSpec("label_mode", "Rótulo do disco", "choice", "color", group="Disco",
                  choices=[("color", "Cor"), ("cover", "Imagem da capa")]),
        ParamSpec("label_color", "Cor do rótulo", "color", "#f7a21b", group="Disco"),
        ParamSpec("disc_color", "Cor do vinil", "color", "#141414", group="Disco"),
        ParamSpec("bars", "Quantidade de barras", "int", 48, 16, 200, 1, group="Tamanho"),
        ParamSpec("bar_width", "Largura das barras", "float", 0.55, 0.15, 0.95, 0.05, group="Tamanho",
                  tip="Fração do espaço de cada barra"),
        ParamSpec("wave_height", "Altura do waveform", "float", 0.4, 0.05, 0.8, 0.01, group="Tamanho",
                  tip="Em relação ao tamanho da capa"),
        ParamSpec("wave_gap", "Distância do disco", "float", 8, 0, 120, 1, group="Tamanho", suffix=" px"),
        ParamSpec("window_s", "Tempo visível", "float", 6, 1, 30, 0.5, group="Comportamento", suffix=" s",
                  tip="Quantos segundos de música cabem no waveform (menor = rola mais rápido)"),
        ParamSpec("react", "Reagir a", "choice", "beats", group="Comportamento",
                  choices=[("beats", "Batidas (picos)"), ("volume", "Volume")],
                  tip="Batidas: barras altas só nos golpes, pontos no resto (como no exemplo)"),
        ParamSpec("contrast", "Contraste", "float", 7, 2, 30, 0.5, group="Comportamento", suffix=" dB",
                  tip="Menor = barras altas com mais facilidade"),
        ParamSpec("fade_tail", "Esmaecer o final", "bool", False, group="Estilo"),
    ]
    default_box = (0.539, 0.5, 0.922, 0.65)

    # ------------------------------------------------------------------ waveform
    def _values(self, d, prm, n: int) -> tuple[np.ndarray, float]:
        """Valores 0..1 das barras (índice 0 = agora, junto do disco) + deslocamento 0..1."""
        span = prm["window_s"]
        dt = span / n
        kb = max(3, int(round(0.3 / dt)))           # ~0,3 s de referência para as batidas
        tq = math.floor(d.t / dt) * dt               # grade fixa: barras estáveis ao rolar
        frac = (d.t - tq) / dt
        m = n + kb + 1
        # d.envelope(janela, pontos) é centrado no instante do quadro; aqui centramos na grade
        # (tq) pedindo o dobro da janela e ficando só com a metade do passado
        env = d.envelope_from(tq, 2 * m * dt, 2 * m)[:m]
        db = 20 * np.log10(np.maximum(env.astype(np.float64) * d.gain, 1e-5))
        if prm["react"] == "volume":
            v = np.clip((db + prm["contrast"]) / prm["contrast"], 0, 1)[kb:] ** 2
        else:
            c = np.cumsum(np.concatenate([[0.0], db]))
            base = (c[kb:-1] - c[:-kb - 1]) / kb        # média dos kb pontos anteriores
            v = np.clip((db[kb:] - base) / prm["contrast"], 0, 1)
            v = v * np.clip((db[kb:] + 40) / 20, 0, 1)   # silêncio não vira batida
            v = v ** 1.3
        v = v[::-1]                                   # 0 = agora
        return v[:n + 1], frac

    # ------------------------------------------------------------------ desenho
    def draw(self, p, box, d, prm, u):
        S = box.height()
        cover = QRectF(box.left(), box.top(), S, S)
        R = S * prm["disc_size"] / 2
        # disco: centro vai da metade da capa (escondido) até R além da borda direita
        cx0 = cover.center().x()
        cx1 = cover.right() + R
        c = QPointF(cx0 + (cx1 - cx0) * prm["disc_out"], cover.center().y())
        img = cached_image(prm.get("cover") or prm.get("_project_image", ""))

        x0 = max(c.x() + R, cover.right()) + prm["wave_gap"] * u
        self._draw_wave(p, QRectF(x0, cover.top(), max(0.0, box.right() - x0), S), d, prm, u, S)
        self._draw_disc(p, c, R, d.t, prm, img)
        self._draw_cover(p, cover, prm, u, img)

    def _draw_wave(self, p, area: QRectF, d, prm, u, S):
        if area.width() < 4:
            return
        n = prm["bars"]
        step = area.width() / n
        w = max(1.0, step * prm["bar_width"])
        vals, frac = self._values(d, prm, n)
        cy = area.center().y()
        hmax = S * prm["wave_height"] / 2
        p.save()
        p.setClipRect(QRectF(area.left() - w, area.top() - S, area.width() + w, S * 3))
        p.setPen(Qt.NoPen)
        col1, col2 = prm["color1"], prm["color2"] if prm["gradient"] else prm["color1"]
        g = QLinearGradient(area.left(), 0, area.right(), 0)
        g.setColorAt(0, qcolor(col1))
        g.setColorAt(1, qcolor(col2, 0.25 if prm["fade_tail"] else 1.0))
        if not prm["fade_tail"] and not prm["gradient"]:
            p.setBrush(qcolor(col1))
        else:
            p.setBrush(QBrush(g))
        path = QPainterPath()
        for i, v in enumerate(vals):
            x = area.left() + (i + frac) * step + step / 2
            if x - w / 2 > area.right():
                break
            h = max(w, 2 * v * hmax)
            path.addRoundedRect(QRectF(x - w / 2, cy - h / 2, w, h), w / 2, w / 2)
        p.drawPath(path)
        p.restore()

    def _draw_disc(self, p, c: QPointF, R: float, t: float, prm, img):
        ang = -(t * prm["rpm"] / 60.0 * 360.0) % 360.0
        base = QColor(prm["disc_color"])
        p.save()
        p.setPen(QPen(QColor(0, 0, 0, 220), max(1.0, R * 0.012)))
        p.setBrush(base)
        p.drawEllipse(c, R, R)
        # sulcos
        groove = QColor(base.lighter(260))
        for k in range(18):
            rr = R * (0.40 + 0.56 * k / 17)
            groove.setAlphaF(0.10 + 0.10 * ((k * 7) % 3) / 2)
            p.setPen(QPen(groove, max(0.6, R * 0.004)))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, rr, rr)
        # reflexo que gira junto com o disco
        cg = QConicalGradient(c, ang)
        hi, lo = QColor(255, 255, 255, 95), QColor(255, 255, 255, 0)
        for a, col in ((0.0, lo), (0.08, hi), (0.17, lo), (0.5, lo), (0.58, hi), (0.67, lo), (1.0, lo)):
            cg.setColorAt(a, col)
        ring = QPainterPath()
        ring.addEllipse(c, R * 0.985, R * 0.985)
        inner = QPainterPath()
        inner.addEllipse(c, R * 0.36, R * 0.36)
        p.setPen(Qt.NoPen)
        p.fillPath(ring.subtracted(inner), QBrush(cg))
        # rótulo
        rl = R * 0.34
        p.setPen(QPen(QColor(0, 0, 0, 160), max(1.0, R * 0.01)))
        if prm["label_mode"] == "cover" and img is not None:
            clip = QPainterPath()
            clip.addEllipse(c, rl, rl)
            p.save()
            p.setClipPath(clip)
            p.translate(c)
            p.rotate(-ang)
            p.drawImage(QRectF(-rl, -rl, 2 * rl, 2 * rl), img, _square_crop(img))
            p.restore()
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, rl, rl)
        else:
            lc = QColor(prm["label_color"])
            lg = QRadialGradient(c, rl)
            lg.setColorAt(0, lc.lighter(115))
            lg.setColorAt(1, lc.darker(125))
            p.setBrush(QBrush(lg))
            p.drawEllipse(c, rl, rl)
            sh = QConicalGradient(c, ang)
            sh.setColorAt(0, QColor(255, 255, 255, 0))
            sh.setColorAt(0.25, QColor(255, 255, 255, 45))
            sh.setColorAt(0.5, QColor(255, 255, 255, 0))
            sh.setColorAt(0.75, QColor(255, 255, 255, 45))
            sh.setColorAt(1, QColor(255, 255, 255, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(sh))
            p.drawEllipse(c, rl, rl)
        # furo central
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(20, 20, 20))
        p.drawEllipse(c, R * 0.025, R * 0.025)
        p.restore()

    def _draw_cover(self, p, r: QRectF, prm, u, img):
        rad = prm["corner"] * u
        path = QPainterPath()
        path.addRoundedRect(r, rad, rad)
        p.save()
        if prm["shadow"] > 0:
            for k in range(6, 0, -1):
                g = k * 3 * u
                sp = QPainterPath()
                sp.addRoundedRect(r.adjusted(-g + 6 * u, -g + 10 * u, g + 6 * u, g + 10 * u),
                                  rad + g, rad + g)
                p.fillPath(sp, QColor(0, 0, 0, int(255 * prm["shadow"] * 0.07)))
        p.setClipPath(path)
        if img is not None:
            p.setRenderHint(p.RenderHint.SmoothPixmapTransform)
            p.drawImage(r, img, _square_crop(img))
        else:
            g = QLinearGradient(r.topLeft(), r.bottomRight())
            g.setColorAt(0, QColor("#3a3550"))
            g.setColorAt(1, QColor("#16141f"))
            p.fillRect(r, QBrush(g))
            p.setPen(QColor(255, 255, 255, 120))
            f = p.font()
            f.setPixelSize(max(8, int(r.height() * 0.32)))
            p.setFont(f)
            p.drawText(r, Qt.AlignCenter, "♪")
        p.setClipping(False)
        if prm["border"] > 0:
            bw = prm["border"] * u
            p.setPen(QPen(qcolor(prm["border_color"]), bw))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(bw / 2, bw / 2, -bw / 2, -bw / 2), rad, rad)
        p.restore()
