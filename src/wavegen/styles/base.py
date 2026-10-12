"""Base da biblioteca de estilos.

Para criar um estilo novo: subclasse de ``Style``, declare ``params`` e implemente ``draw``;
decore com ``@register``. A interface gera os controles automaticamente a partir dos
``ParamSpec`` e mostra apenas os grupos/controles que o estilo usa.

Unidades: medidas em "px" são relativas a um quadro de 1080 px no menor lado
(``u = min(W, H) / 1080``), então preview e exportação ficam proporcionais.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QLinearGradient, QPainter,
                           QPainterPath, QPen, QRadialGradient)


@dataclass
class ParamSpec:
    key: str
    label: str
    kind: str                     # float | int | bool | color | choice | image
    default: object
    min: float = 0.0
    max: float = 1.0
    step: float = 0.01
    group: str = "Estilo"         # Cores | Tamanho | Comportamento | Estilo
    choices: list[tuple[str, str]] = field(default_factory=list)  # (valor, rótulo)
    suffix: str = ""
    tip: str = ""


# parâmetros comuns — cada estilo escolhe quais usa (``common``)
COMMON: dict[str, ParamSpec] = {p.key: p for p in [
    ParamSpec("color1", "Cor principal", "color", "#ff3d7f", group="Cores"),
    ParamSpec("color2", "Cor secundária", "color", "#7b2ff7", group="Cores"),
    ParamSpec("gradient", "Usar gradiente", "bool", True, group="Cores",
              tip="Desligado: usa só a cor principal"),
    ParamSpec("opacity", "Opacidade", "float", 1.0, 0.05, 1.0, 0.05, group="Cores"),
    ParamSpec("glow", "Brilho (neon)", "float", 0.0, 0.0, 1.0, 0.05, group="Cores",
              tip="Halo luminoso em volta do desenho"),
]}


@dataclass
class FrameData:
    t: float                                  # segundos
    n: int                                    # índice do quadro
    bands: np.ndarray | None = None           # 0..1 por barra (suavizado)
    level: float = 0.0                        # 0..1 volume geral (suavizado)
    peaks: np.ndarray | None = None           # picos retidos (para o analisador)
    gain: float = 1.0                         # ganho linear (sensibilidade) p/ osciloscópio
    scope: Callable[[float, int], np.ndarray] | None = None     # (janela_s, n) -> amostras
    envelope: Callable[[float, int], np.ndarray] | None = None  # (janela_s, n) -> picos
    envelope_from: Callable[[float, float, int], np.ndarray] | None = None  # (t, janela_s, n)
    # faixa tocando neste instante (para estilos que mostram nome/contador)
    track_title: str = ""
    track_index: int = 0           # 1, 2, … (0 = sem faixas)
    track_count: int = 0
    track_pos: float = 0.0         # s desde o início da faixa
    track_len: float = 0.0         # duração da faixa no vídeo (s)
    track_start: float = 0.0       # s (no vídeo) em que a faixa começa


class Style:
    id = "base"
    name = "Base"
    description = ""
    uses: frozenset = frozenset({"bands"})    # bands | level | scope | env | peaks
    common = ("color1", "color2", "gradient", "opacity", "glow")
    overrides: dict = {}                      # padrões dos parâmetros comuns
    params: list[ParamSpec] = []
    default_box = (0.5, 0.5, 0.84, 0.32)      # centro x, centro y, largura, altura (frações)

    # --- metadados -------------------------------------------------------
    @classmethod
    def all_params(cls) -> list[ParamSpec]:
        out = []
        for k in cls.common:
            spec = COMMON[k]
            if k in cls.overrides:
                spec = ParamSpec(**{**spec.__dict__, "default": cls.overrides[k]})
            out.append(spec)
        return out + list(cls.params)

    @classmethod
    def defaults(cls) -> dict:
        return {p.key: p.default for p in cls.all_params()}

    @classmethod
    def resolve(cls, prm: dict | None) -> dict:
        """Parâmetros completos e validados (valores fora da faixa são limitados)."""
        out = cls.defaults()
        for p in cls.all_params():
            if prm and p.key in prm:
                v = prm[p.key]
                try:
                    if p.kind == "float":
                        v = float(min(max(float(v), p.min), p.max))
                    elif p.kind == "int":
                        v = int(min(max(int(v), p.min), p.max))
                    elif p.kind == "bool":
                        v = bool(v)
                    elif p.kind == "color":
                        v = QColor(str(v)).name(QColor.HexArgb) if QColor(str(v)).isValid() else p.default
                    elif p.kind == "choice":
                        v = v if v in [c[0] for c in p.choices] else p.default
                    elif p.kind == "image":
                        v = str(v or "")
                except (TypeError, ValueError):
                    v = p.default
                out[p.key] = v
        for k, v in (prm or {}).items():
            if k.startswith("_"):
                out[k] = v           # dados internos (ex.: imagem de fundo do projeto)
        return out

    # --- dados de áudio necessários -------------------------------------
    def n_bars(self, prm: dict) -> int | None:
        return int(prm.get("bars", 64)) if "bands" in self.uses else None

    fmin, fmax = 30.0, 16000.0                # faixa de frequências exibida

    def freq_range(self, prm: dict) -> tuple[float, float]:
        return self.fmin, self.fmax

    def peak_fall(self, prm: dict) -> float:
        return 0.02

    # --- desenho ---------------------------------------------------------
    def draw(self, p: QPainter, box: QRectF, d: FrameData, prm: dict, u: float) -> None:
        raise NotImplementedError


REGISTRY: dict[str, type[Style]] = {}


def register(cls: type[Style]) -> type[Style]:
    REGISTRY[cls.id] = cls
    return cls


# ----------------------------------------------------------------------------- utilitários

def qcolor(s: str, alpha: float = 1.0) -> QColor:
    c = QColor(s)
    if alpha != 1.0:
        c.setAlphaF(max(0.0, min(1.0, c.alphaF() * alpha)))
    return c


def mix_color(a: str, b: str, t: float) -> QColor:
    ca, cb = QColor(a), QColor(b)
    return QColor.fromRgbF(ca.redF() + (cb.redF() - ca.redF()) * t,
                           ca.greenF() + (cb.greenF() - ca.greenF()) * t,
                           ca.blueF() + (cb.blueF() - ca.blueF()) * t,
                           ca.alphaF() + (cb.alphaF() - ca.alphaF()) * t)


def linear_brush(prm: dict, x0: float, y0: float, x1: float, y1: float,
                 symmetric: bool = False) -> QBrush:
    """Gradiente de color2 (início) para color1 (fim); simétrico = color1 nas pontas."""
    if not prm.get("gradient", True):
        return QBrush(qcolor(prm["color1"]))
    g = QLinearGradient(x0, y0, x1, y1)
    if symmetric:
        g.setColorAt(0.0, qcolor(prm["color1"]))
        g.setColorAt(0.5, qcolor(prm["color2"]))
        g.setColorAt(1.0, qcolor(prm["color1"]))
    else:
        g.setColorAt(0.0, qcolor(prm["color2"]))
        g.setColorAt(1.0, qcolor(prm["color1"]))
    return QBrush(g)


def radial_brush(prm: dict, c: QPointF, r0: float, r1: float) -> QBrush:
    if not prm.get("gradient", True):
        return QBrush(qcolor(prm["color1"]))
    g = QRadialGradient(c, max(r1, 1.0))
    k = max(0.0, min(0.999, r0 / max(r1, 1.0)))
    g.setColorAt(0.0, qcolor(prm["color2"]))
    g.setColorAt(k, qcolor(prm["color2"]))
    g.setColorAt(1.0, qcolor(prm["color1"]))
    return QBrush(g)


def conical_brush(prm: dict, c: QPointF, angle: float = 90.0) -> QBrush:
    if not prm.get("gradient", True):
        return QBrush(qcolor(prm["color1"]))
    g = QConicalGradient(c, angle)
    g.setColorAt(0.0, qcolor(prm["color1"]))
    g.setColorAt(0.5, qcolor(prm["color2"]))
    g.setColorAt(1.0, qcolor(prm["color1"]))
    return QBrush(g)


def smooth_path(xs, ys, closed: bool = False, path: QPainterPath | None = None,
                move: bool = True) -> QPainterPath:
    """Curva suave (Catmull-Rom → Bézier) passando por todos os pontos."""
    path = path if path is not None else QPainterPath()
    n = len(xs)
    if n == 0:
        return path
    pts = [QPointF(float(x), float(y)) for x, y in zip(xs, ys)]
    if move:
        path.moveTo(pts[0])
    else:
        path.lineTo(pts[0])
    if n < 3:
        for q in pts[1:]:
            path.lineTo(q)
        return path
    rng = range(n) if closed else range(n - 1)
    for i in rng:
        p0 = pts[(i - 1) % n] if closed or i > 0 else pts[i]
        p1 = pts[i]
        p2 = pts[(i + 1) % n]
        p3 = pts[(i + 2) % n] if closed or i + 2 < n else pts[min(i + 2, n - 1)]
        c1 = QPointF(p1.x() + (p2.x() - p0.x()) / 6, p1.y() + (p2.y() - p0.y()) / 6)
        c2 = QPointF(p2.x() - (p3.x() - p1.x()) / 6, p2.y() - (p3.y() - p1.y()) / 6)
        path.cubicTo(c1, c2, p2)
    if closed:
        path.closeSubpath()
    return path


def center_out(values: np.ndarray) -> np.ndarray:
    """Espelha o espectro: graves no centro, agudos nas duas pontas."""
    return np.concatenate([values[::-1], values])


def spatial_smooth(v: np.ndarray, k: int) -> np.ndarray:
    if k <= 1:
        return v
    ker = np.hanning(k + 2)[1:-1]
    ker /= ker.sum()
    pad = np.pad(v, (k // 2, k - 1 - k // 2), mode="edge")
    return np.convolve(pad, ker, mode="valid")


def pen(brush_or_color, width: float, cap=Qt.RoundCap) -> QPen:
    pn = QPen(brush_or_color, max(width, 0.5))
    pn.setCapStyle(cap)
    pn.setJoinStyle(Qt.RoundJoin)
    return pn
