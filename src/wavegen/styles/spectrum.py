"""Estilos baseados no espectro de frequências (barras)."""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QPainter, QPainterPath

from .base import (FrameData, ParamSpec, Style, center_out, linear_brush, mix_color, pen,
                   qcolor, radial_brush, register, smooth_path, spatial_smooth)

P_BARS = ParamSpec("bars", "Quantidade de barras", "int", 64, 8, 256, 1, group="Tamanho")
P_GAP = ParamSpec("gap", "Espaço entre barras", "float", 0.3, 0.0, 0.9, 0.05, group="Tamanho")
P_ROUND = ParamSpec("round", "Arredondamento", "float", 0.0, 0.0, 1.0, 0.1, group="Tamanho")
P_MINH = ParamSpec("min_height", "Altura mínima", "float", 4, 0, 40, 1, group="Tamanho", suffix=" px")


def _bars_path(xs: np.ndarray, bw: float, tops: np.ndarray, bottoms: np.ndarray,
               radius: float) -> QPainterPath:
    path = QPainterPath()
    path.setFillRule(Qt.WindingFill)
    for x, t, b in zip(xs, tops, bottoms):
        h = b - t
        if h <= 0:
            continue
        r = min(radius, bw / 2, h / 2)
        if r > 0.3:
            path.addRoundedRect(QRectF(x, t, bw, h), r, r)
        else:
            path.addRect(QRectF(x, t, bw, h))
    return path


def _slots(box: QRectF, n: int, gap: float):
    slot = box.width() / n
    bw = max(slot * (1 - gap), 1.0)
    xs = box.left() + np.arange(n) * slot + (slot - bw) / 2
    return xs, bw


@register
class Bars(Style):
    id = "bars"
    name = "Barras verticais"
    description = "Barras lado a lado que sobem com cada faixa de frequência."
    uses = frozenset({"bands"})
    params = [P_BARS, P_GAP, P_ROUND, P_MINH,
              ParamSpec("anchor", "Crescer a partir de", "choice", "bottom",
                        choices=[("bottom", "Base"), ("top", "Topo")], group="Estilo")]
    default_box = (0.5, 0.78, 0.84, 0.32)

    def draw(self, p, box, d, prm, u):
        n = prm["bars"]
        xs, bw = _slots(box, n, prm["gap"])
        h = np.maximum(d.bands * box.height(), prm["min_height"] * u)
        if prm["anchor"] == "top":
            tops, bottoms = np.full(n, box.top()), box.top() + h
            brush = linear_brush(prm, 0, box.top(), 0, box.bottom())
        else:
            tops, bottoms = box.bottom() - h, np.full(n, box.bottom())
            brush = linear_brush(prm, 0, box.bottom(), 0, box.top())
        p.fillPath(_bars_path(xs, bw, tops, bottoms, prm["round"] * bw / 2), brush)


@register
class MirrorBars(Style):
    id = "mirror"
    name = "Espectro espelhado"
    description = "Barras que crescem para cima e para baixo a partir de um eixo central."
    uses = frozenset({"bands"})
    overrides = {"color1": "#00e5ff", "color2": "#2979ff"}
    params = [ParamSpec("bars", "Quantidade de barras", "int", 96, 8, 256, 1, group="Tamanho"),
              ParamSpec("gap", "Espaço entre barras", "float", 0.4, 0.0, 0.9, 0.05, group="Tamanho"),
              ParamSpec("round", "Arredondamento", "float", 1.0, 0.0, 1.0, 0.1, group="Tamanho"),
              P_MINH,
              ParamSpec("center_out", "Graves no centro", "bool", False, group="Estilo",
                        tip="Espelha também na horizontal: graves no meio, agudos nas pontas")]
    default_box = (0.5, 0.5, 0.84, 0.4)

    def n_bars(self, prm):
        return prm["bars"] // 2 if prm["center_out"] else prm["bars"]

    def freq_range(self, prm):
        return (50.0, 14000.0) if prm["center_out"] else (30.0, 16000.0)

    def draw(self, p, box, d, prm, u):
        vals = center_out(d.bands) if prm["center_out"] else d.bands
        n = vals.size
        xs, bw = _slots(box, n, prm["gap"])
        h = np.maximum(vals * box.height() / 2, prm["min_height"] * u / 2)
        cy = box.center().y()
        brush = linear_brush(prm, 0, box.top(), 0, box.bottom(), symmetric=True)
        p.fillPath(_bars_path(xs, bw, cy - h, cy + h, prm["round"] * bw / 2), brush)


@register
class Analyzer(Style):
    id = "analyzer"
    name = "Espectro de frequência"
    description = "Analisador clássico em blocos de LED, com marcador de pico por banda."
    uses = frozenset({"bands", "peaks"})
    overrides = {"color1": "#ff1744", "color2": "#00e676"}
    params = [ParamSpec("bars", "Bandas", "int", 32, 8, 96, 1, group="Tamanho"),
              ParamSpec("segments", "Blocos por banda", "int", 20, 6, 48, 1, group="Tamanho"),
              ParamSpec("gap", "Espaço entre bandas", "float", 0.25, 0.0, 0.8, 0.05, group="Tamanho"),
              ParamSpec("seg_gap", "Espaço entre blocos", "float", 0.3, 0.0, 0.8, 0.05, group="Tamanho"),
              ParamSpec("peak_hold", "Marcador de pico", "bool", True, group="Estilo"),
              ParamSpec("peak_speed", "Queda do pico", "float", 0.35, 0.05, 1.0, 0.05, group="Estilo"),
              ParamSpec("show_off", "Mostrar blocos apagados", "bool", True, group="Estilo")]
    default_box = (0.5, 0.72, 0.7, 0.42)

    def peak_fall(self, prm):
        return 0.004 + 0.04 * prm["peak_speed"]

    def draw(self, p, box, d, prm, u):
        n, S = prm["bars"], prm["segments"]
        xs, bw = _slots(box, n, prm["gap"])
        seg_h = box.height() / S
        sh = max(seg_h * (1 - prm["seg_gap"]), 1.0)
        lit = np.floor(d.bands * S + 1e-6).astype(int)
        pk = np.minimum(np.floor(d.peaks * S + 1e-6).astype(int), S)
        grad = prm["gradient"]
        for s in range(S):
            y = box.bottom() - (s + 1) * seg_h + (seg_h - sh) / 2
            col = mix_color(prm["color2"], prm["color1"], s / max(S - 1, 1)) if grad else qcolor(prm["color1"])
            on = QPainterPath()
            off = QPainterPath()
            for i in range(n):
                r = QRectF(xs[i], y, bw, sh)
                if s < lit[i] or (prm["peak_hold"] and s == pk[i] - 1 and pk[i] > lit[i]):
                    on.addRect(r)
                elif prm["show_off"]:
                    off.addRect(r)
            if not on.isEmpty():
                p.fillPath(on, col)
            if not off.isEmpty():
                c2 = qcolor(col.name(), 0.10)
                p.fillPath(off, c2)


@register
class CircleSpectrum(Style):
    id = "circle_spectrum"
    name = "Espectro circular"
    description = "Barras organizadas radialmente em volta de um círculo que pulsa."
    uses = frozenset({"bands", "level"})
    overrides = {"color1": "#ffd54f", "color2": "#ff6f00"}
    params = [ParamSpec("bars", "Quantidade de barras", "int", 120, 24, 360, 2, group="Tamanho"),
              ParamSpec("radius", "Raio interno", "float", 0.42, 0.1, 0.9, 0.01, group="Tamanho",
                        tip="Fração da área do visualizador"),
              ParamSpec("length", "Comprimento das barras", "float", 0.8, 0.05, 1.0, 0.01, group="Tamanho"),
              ParamSpec("thickness", "Espessura das barras", "float", 0.55, 0.1, 1.0, 0.05, group="Tamanho"),
              ParamSpec("pulse", "Pulsação com o volume", "float", 0.25, 0.0, 1.0, 0.05, group="Comportamento"),
              ParamSpec("symmetric", "Simétrico (espelhado)", "bool", True, group="Estilo"),
              ParamSpec("ring", "Desenhar anel", "bool", True, group="Estilo"),
              ParamSpec("rotation", "Rotação", "float", 0.0, -180, 180, 1, group="Estilo", suffix="°")]
    default_box = (0.5, 0.5, 0.45, 0.8)

    def n_bars(self, prm):
        return prm["bars"] // 2 if prm["symmetric"] else prm["bars"]

    def draw(self, p, box, d, prm, u):
        vals = np.concatenate([d.bands, d.bands[::-1]]) if prm["symmetric"] else d.bands
        n = vals.size
        c = box.center()
        R = min(box.width(), box.height()) / 2
        r0 = R * prm["radius"] * (1 + prm["pulse"] * 0.25 * d.level)
        L = (R - R * prm["radius"]) * prm["length"] * 1.0
        ang = math.radians(prm["rotation"]) + np.pi / 2 + (np.arange(n) + 0.5) * 2 * np.pi / n
        ca, sa = np.cos(ang), np.sin(ang)
        r1 = r0 + np.maximum(vals * L, 2 * u)
        width = 2 * np.pi * r0 / n * prm["thickness"]
        lines = [QLineF(c.x() + r0 * ca[i], c.y() + r0 * sa[i], c.x() + r1[i] * ca[i], c.y() + r1[i] * sa[i])
                 for i in range(n)]
        p.setPen(pen(radial_brush(prm, c, r0, r0 + L), width, Qt.RoundCap))
        p.drawLines(lines)
        if prm["ring"]:
            p.setPen(pen(qcolor(prm["color1"] if not prm["gradient"] else prm["color2"]), max(2 * u, width * 0.4)))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, r0 - width, r0 - width)


@register
class Neon(Style):
    id = "neon"
    fmin, fmax = 50.0, 14000.0   # graves no centro: evita o vale do sub-grave
    name = "Visualizador neon"
    description = "Linhas ou barras finas com halo luminoso, estilo letreiro neon."
    uses = frozenset({"bands"})
    overrides = {"color1": "#ff00e5", "color2": "#00e5ff", "glow": 0.85}
    params = [ParamSpec("shape", "Forma", "choice", "lines",
                        choices=[("lines", "Linhas"), ("bars", "Barras")], group="Estilo"),
              ParamSpec("bars", "Detalhe (bandas)", "int", 48, 12, 128, 1, group="Tamanho"),
              ParamSpec("thickness", "Espessura", "float", 4, 1, 20, 0.5, group="Tamanho", suffix=" px"),
              ParamSpec("layers", "Camadas de linha", "int", 3, 1, 5, 1, group="Estilo"),
              ParamSpec("reflection", "Reflexo embaixo", "bool", True, group="Estilo")]
    default_box = (0.5, 0.55, 0.8, 0.36)

    def draw(self, p, box, d, prm, u):
        vals = center_out(spatial_smooth(d.bands, 3))
        n = vals.size
        cy = box.center().y() if prm["reflection"] else box.bottom()
        hmax = box.height() / 2 if prm["reflection"] else box.height()
        th = prm["thickness"] * u
        xs = box.left() + (np.arange(n) + 0.5) * box.width() / n
        if prm["shape"] == "bars":
            hh = np.maximum(vals * hmax, th / 2)
            col = linear_brush(prm, box.left(), 0, box.right(), 0, symmetric=True)
            p.setPen(pen(col, th))
            p.drawLines([QLineF(xs[i], cy, xs[i], cy - hh[i]) for i in range(n)])
            if prm["reflection"]:
                p.setOpacity(p.opacity() * 0.45)
                p.drawLines([QLineF(xs[i], cy, xs[i], cy + hh[i] * 0.6) for i in range(n)])
                p.setOpacity(p.opacity() / 0.45)
            return
        k = prm["layers"]
        p.setBrush(Qt.NoBrush)
        for j in range(k):
            scale = 1.0 - j * (0.7 / max(k, 1))
            col = mix_color(prm["color1"], prm["color2"], j / max(k - 1, 1)) if prm["gradient"] else qcolor(prm["color1"])
            v = vals ** (1 + 0.35 * j) * scale
            xx = np.concatenate([[box.left()], xs, [box.right()]])
            yy = np.concatenate([[cy], cy - v * hmax, [cy]])
            p.setPen(pen(col, th * (1 - 0.15 * j)))
            p.drawPath(smooth_path(xx, yy))
            if prm["reflection"]:
                p.setOpacity(p.opacity() * 0.4)
                p.drawPath(smooth_path(xx, np.concatenate([[cy], cy + v * hmax * 0.6, [cy]])))
                p.setOpacity(p.opacity() / 0.4)
