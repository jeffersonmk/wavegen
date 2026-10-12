"""Estilos baseados na forma de onda / envoltória (amplitude no tempo) e curvas suaves."""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QPainterPath

from .base import (FrameData, ParamSpec, Style, center_out, conical_brush, linear_brush,
                   mix_color, pen, qcolor, register, smooth_path, spatial_smooth)


@register
class WaveLine(Style):
    id = "wave_line"
    name = "Linha de onda"
    description = "Osciloscópio: a forma de onda real do áudio desenhada como uma linha contínua."
    uses = frozenset({"scope"})
    overrides = {"color1": "#69f0ae", "color2": "#00b0ff"}
    params = [ParamSpec("thickness", "Espessura da linha", "float", 4, 1, 24, 0.5, group="Tamanho", suffix=" px"),
              ParamSpec("window_ms", "Janela de tempo", "float", 40, 5, 250, 1, group="Comportamento",
                        suffix=" ms", tip="Quanto do áudio aparece na tela de cada vez"),
              ParamSpec("detail", "Detalhe (pontos)", "int", 240, 32, 1024, 8, group="Comportamento"),
              ParamSpec("taper", "Afinar nas pontas", "bool", True, group="Estilo")]
    default_box = (0.5, 0.5, 0.84, 0.36)

    def draw(self, p, box, d, prm, u):
        n = prm["detail"]
        s = np.clip(d.scope(prm["window_ms"] / 1000, n) * d.gain * 1.6, -1, 1)
        if prm["taper"]:
            s = s * np.sin(np.linspace(0, np.pi, n)) ** 0.7
        xs = np.linspace(box.left(), box.right(), n)
        ys = box.center().y() - s * box.height() / 2
        p.setBrush(Qt.NoBrush)
        p.setPen(pen(linear_brush(prm, box.left(), 0, box.right(), 0), prm["thickness"] * u))
        p.drawPath(smooth_path(xs, ys))


@register
class CircleWave(Style):
    id = "circle_wave"
    name = "Onda circular"
    description = "Anel deformado pela forma de onda, que pulsa com o volume da música."
    uses = frozenset({"scope", "level"})
    overrides = {"color1": "#e040fb", "color2": "#40c4ff"}
    params = [ParamSpec("radius", "Raio", "float", 0.62, 0.15, 0.95, 0.01, group="Tamanho",
                        tip="Fração da área do visualizador"),
              ParamSpec("amplitude", "Amplitude da onda", "float", 0.6, 0.0, 1.0, 0.01, group="Tamanho"),
              ParamSpec("thickness", "Espessura da linha", "float", 4, 1, 24, 0.5, group="Tamanho", suffix=" px"),
              ParamSpec("pulse", "Pulsação com o volume", "float", 0.35, 0.0, 1.0, 0.05, group="Comportamento"),
              ParamSpec("window_ms", "Janela de tempo", "float", 25, 5, 250, 1, group="Comportamento", suffix=" ms"),
              ParamSpec("fill", "Preencher", "bool", False, group="Estilo"),
              ParamSpec("rings", "Anéis", "int", 2, 1, 4, 1, group="Estilo")]
    default_box = (0.5, 0.5, 0.45, 0.8)

    def draw(self, p, box, d, prm, u):
        n = 256
        c = box.center()
        R = min(box.width(), box.height()) / 2
        r0 = R * prm["radius"] * (1 + prm["pulse"] * 0.3 * d.level) / (1 + prm["pulse"] * 0.3)
        amp = R * (1 - prm["radius"]) * prm["amplitude"]
        n_win = prm["window_ms"] / 1000
        s = np.clip(d.scope(n_win, n) * d.gain * 1.6, -1, 1)
        s = np.convolve(np.concatenate([s[-4:], s, s[:4]]), np.hanning(9) / np.hanning(9).sum(), "valid")
        k = n // 8                         # junta o fim com o começo (sem costura)
        ramp = np.linspace(0, 1, k)
        s[-k:] = s[-k:] * (1 - ramp) + s[:k] * ramp
        ang = -np.pi / 2 + np.arange(n) * 2 * np.pi / n
        p.setPen(pen(conical_brush(prm, c), prm["thickness"] * u))
        for j in range(prm["rings"]):
            f = 1.0 - j * 0.35
            rr = r0 * (1 - j * 0.08) + s * amp * f
            if j:
                p.setOpacity(p.opacity() * 0.55)
            path = smooth_path(c.x() + rr * np.cos(ang), c.y() + rr * np.sin(ang), closed=True)
            if prm["fill"] and j == 0:
                p.setBrush(qcolor(prm["color2"], 0.35))
            else:
                p.setBrush(Qt.NoBrush)
            p.drawPath(path)
            if j:
                p.setOpacity(p.opacity() / 0.55)


@register
class Minimal(Style):
    id = "minimal"
    fmin, fmax = 50.0, 14000.0   # graves no centro: evita o vale do sub-grave
    name = "Linha minimalista"
    description = "Traço fino e discreto que ondula suavemente com o espectro."
    uses = frozenset({"bands"})
    common = ("color1", "opacity", "glow")
    overrides = {"color1": "#ffffff", "opacity": 0.9}
    params = [ParamSpec("thickness", "Espessura da linha", "float", 2, 0.5, 10, 0.5, group="Tamanho", suffix=" px"),
              ParamSpec("bars", "Detalhe", "int", 32, 8, 96, 1, group="Comportamento"),
              ParamSpec("softness", "Suavidade da curva", "int", 3, 1, 9, 1, group="Comportamento"),
              ParamSpec("dots", "Pontos nas extremidades", "bool", True, group="Estilo")]
    default_box = (0.5, 0.85, 0.6, 0.12)

    def draw(self, p, box, d, prm, u):
        v = spatial_smooth(center_out(d.bands), prm["softness"])
        n = v.size
        taper = np.sin(np.linspace(0, np.pi, n)) ** 0.5
        xs = np.linspace(box.left(), box.right(), n)
        ys = box.bottom() - v * taper * box.height()
        th = prm["thickness"] * u
        p.setBrush(Qt.NoBrush)
        p.setPen(pen(qcolor(prm["color1"]), th))
        p.drawPath(smooth_path(xs, ys))
        if prm["dots"]:
            p.setPen(Qt.NoPen)
            p.setBrush(qcolor(prm["color1"]))
            for x in (box.left(), box.right()):
                p.drawEllipse(QPointF(x, box.bottom()), th * 1.8, th * 1.8)


@register
class SymWave(Style):
    id = "sym_wave"
    fmin, fmax = 50.0, 14000.0   # graves no centro: evita o vale do sub-grave
    name = "Onda simétrica"
    description = "Forma orgânica espelhada nos dois eixos, em camadas, que respira com a música."
    uses = frozenset({"bands"})
    overrides = {"color1": "#ff4081", "color2": "#7c4dff"}
    params = [ParamSpec("bars", "Detalhe", "int", 40, 8, 128, 1, group="Comportamento"),
              ParamSpec("layers", "Camadas", "int", 3, 1, 5, 1, group="Estilo"),
              ParamSpec("softness", "Suavidade da forma", "int", 3, 1, 9, 1, group="Comportamento"),
              ParamSpec("outline", "Contorno", "float", 0, 0, 10, 0.5, group="Tamanho", suffix=" px")]
    default_box = (0.5, 0.5, 0.84, 0.4)

    def draw(self, p, box, d, prm, u):
        v0 = spatial_smooth(center_out(d.bands), prm["softness"])
        n = v0.size
        taper = np.sin(np.linspace(0, np.pi, n)) ** 0.6
        xs = np.concatenate([[box.left()], np.linspace(box.left(), box.right(), n), [box.right()]])
        cy, hh = box.center().y(), box.height() / 2
        k = prm["layers"]
        for j in range(k - 1, -1, -1):           # de trás (maior, mais transparente) para frente
            f = 0.82 * (1.0 + j * 0.16)
            v = np.concatenate([[0], np.clip((v0 ** (1 + 0.25 * j)) * taper * f, 0, 1.0), [0]])
            path = smooth_path(xs, cy - v * hh)
            smooth_path(xs[::-1], (cy + v * hh)[::-1], path=path, move=False)
            path.closeSubpath()
            col = mix_color(prm["color1"], prm["color2"], j / max(k - 1, 1)) if prm["gradient"] else qcolor(prm["color1"])
            col.setAlphaF(col.alphaF() * (1.0 if j == 0 else 0.75 / (1 + j)))
            p.fillPath(path, col)
            if prm["outline"] > 0 and j == 0:
                p.setBrush(Qt.NoBrush)
                p.setPen(pen(qcolor(prm["color2"]), prm["outline"] * u))
                p.drawPath(path)


@register
class FilledWave(Style):
    id = "filled_wave"
    name = "Waveform preenchido"
    description = "Forma de onda da música rolando na tela (como num editor de áudio), com borda."
    uses = frozenset({"env"})
    overrides = {"color1": "#ffab40", "color2": "#ff3d00"}
    params = [ParamSpec("window_s", "Tempo visível", "float", 8, 1, 60, 0.5, group="Comportamento", suffix=" s"),
              ParamSpec("detail", "Detalhe (colunas)", "int", 220, 40, 800, 10, group="Comportamento"),
              ParamSpec("border", "Espessura da borda", "float", 2, 0, 12, 0.5, group="Tamanho", suffix=" px"),
              ParamSpec("border_color", "Cor da borda", "color", "#ffffff", group="Cores"),
              ParamSpec("future_opacity", "Opacidade do trecho futuro", "float", 0.35, 0.0, 1.0, 0.05, group="Cores"),
              ParamSpec("playhead", "Linha de reprodução", "bool", True, group="Estilo")]
    default_box = (0.5, 0.8, 0.84, 0.22)

    def draw(self, p, box, d, prm, u):
        n = prm["detail"]
        e = np.clip(d.envelope(prm["window_s"], n) * d.gain, 0, 1)
        e = np.maximum(e, 0.01)
        xs = np.linspace(box.left(), box.right(), n)
        cy, hh = box.center().y(), box.height() / 2
        path = QPainterPath()
        path.moveTo(xs[0], cy)
        for x, v in zip(xs, e):
            path.lineTo(x, cy - v * hh)
        path.lineTo(xs[-1], cy)
        for x, v in zip(xs[::-1], e[::-1]):
            path.lineTo(x, cy + v * hh)
        path.closeSubpath()
        mid = box.center().x()
        fill_past = linear_brush(prm, 0, box.top(), 0, box.bottom(), symmetric=True)
        p.save()
        p.setClipRect(QRectF(box.left() - 1, box.top() - 1, mid - box.left() + 1, box.height() + 2))
        p.fillPath(path, fill_past)
        p.setClipRect(QRectF(mid, box.top() - 1, box.right() - mid + 1, box.height() + 2))
        p.setOpacity(p.opacity() * prm["future_opacity"])
        p.fillPath(path, fill_past)
        p.restore()
        if prm["border"] > 0:
            p.setBrush(Qt.NoBrush)
            p.setPen(pen(qcolor(prm["border_color"]), prm["border"] * u))
            p.drawPath(path)
        if prm["playhead"]:
            p.setPen(pen(qcolor(prm["border_color"]), max(2 * u, prm["border"] * u), Qt.FlatCap))
            p.drawLine(QLineF(mid, box.top(), mid, box.bottom()))
