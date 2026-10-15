"""Animação de "Inscreva-se" sobreposta ao vídeo.

Sequência (duração padrão 6 s, tudo escala com a duração escolhida):
  1. o botão entra crescendo (efeito "pop") e subindo um pouco;
  2. o cursor entra e clica no botão → ondinha, o botão fica cinza com ✓ e o texto vira
     "Inscrito" (largura animada);
  3. o cursor vai até o sino e clica → o sino balança e fica preenchido, com brilhos;
  4. o cursor sai e o conjunto desaparece.

É desenhada pelo mesmo código no preview e na exportação (Compositor), independente do estilo.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen

# (texto do botão, texto depois do clique)
LANGUAGES: dict[str, tuple[str, str, str]] = {
    # código: (nome no menu, botão, inscrito)
    "pt": ("Português", "INSCREVER-SE", "INSCRITO"),
    "en": ("English", "SUBSCRIBE", "SUBSCRIBED"),
    "es": ("Español", "SUSCRIBIRSE", "SUSCRITO"),
    "fr": ("Français", "S'ABONNER", "ABONNÉ"),
    "de": ("Deutsch", "ABONNIEREN", "ABONNIERT"),
    "it": ("Italiano", "ISCRIVITI", "ISCRITTO"),
    "ru": ("Русский", "ПОДПИСАТЬСЯ", "ВЫ ПОДПИСАНЫ"),
    "ja": ("日本語", "チャンネル登録", "登録済み"),
    "ko": ("한국어", "구독", "구독중"),
    "custom": ("Personalizado", "", ""),
}

POSITIONS = [
    ("bottom_left", "Embaixo, à esquerda"),
    ("bottom_center", "Embaixo, no centro"),
    ("bottom_right", "Embaixo, à direita"),
    ("top_left", "Em cima, à esquerda"),
    ("top_right", "Em cima, à direita"),
    ("center", "No centro"),
]


@dataclass
class SubscribeSettings:
    enabled: bool = False
    lang: str = "pt"
    text: str = ""               # idioma "custom": texto do botão
    text_done: str = ""          # idioma "custom": texto depois do clique
    first: float = 30.0          # primeira aparição (s desde o início)
    interval: float = 600.0      # repetir a cada N s (0 = só uma vez)
    duration: float = 6.0        # duração de cada aparição (s)
    at_end: bool = False         # aparecer também nos últimos segundos do vídeo
    position: str = "bottom_left"
    size: float = 1.0
    color: str = "#e62117"
    bell: bool = True
    cursor: bool = True

    @classmethod
    def from_dict(cls, d: dict) -> "SubscribeSettings":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})

    def texts(self) -> tuple[str, str]:
        if self.lang == "custom":
            a = self.text.strip() or LANGUAGES["pt"][1]
            return a, self.text_done.strip() or a
        return LANGUAGES.get(self.lang, LANGUAGES["pt"])[1:]


def schedule(s: SubscribeSettings, total: float) -> list[float]:
    """Instantes (s) em que a animação começa. Só entram aparições que cabem inteiras no vídeo."""
    if not s.enabled or total <= 0:
        return []
    dur = max(1.0, s.duration)
    out: list[float] = []
    t = max(0.0, s.first)
    while t + dur <= total + 1e-6:
        out.append(round(t, 3))
        if s.interval <= 0:
            break
        t += max(dur + 1.0, s.interval)
    if s.at_end:
        end = max(0.0, total - dur - 1.0)
        if all(abs(end - a) >= dur + 1.0 for a in out) and end + dur <= total:
            out.append(round(end, 3))
    return sorted(out)


def active_start(starts: list[float], t: float, dur: float) -> float | None:
    for a in starts:
        if a <= t < a + dur:
            return a
    return None


# ----------------------------------------------------------------------------- curvas
def _clamp(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def _seg(t: float, a: float, b: float) -> float:
    return _clamp((t - a) / (b - a)) if b > a else float(t >= b)


def _ease_out(x: float) -> float:
    return 1 - (1 - x) ** 3


def _ease_io(x: float) -> float:
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def _back(x: float) -> float:                     # passa um pouco do ponto e volta ("pop")
    c1, c3 = 1.7, 2.7
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def _mix(a: QColor, b: QColor, k: float) -> QColor:
    return QColor(int(a.red() + (b.red() - a.red()) * k), int(a.green() + (b.green() - a.green()) * k),
                  int(a.blue() + (b.blue() - a.blue()) * k), int(a.alpha() + (b.alpha() - a.alpha()) * k))


# ----------------------------------------------------------------------------- desenho
def _bell_path(s: float) -> QPainterPath:
    p = QPainterPath()
    p.moveTo(-0.40 * s, 0.24 * s)
    p.cubicTo(-0.30 * s, 0.12 * s, -0.31 * s, -0.02 * s, -0.30 * s, -0.10 * s)
    p.cubicTo(-0.27 * s, -0.36 * s, 0.27 * s, -0.36 * s, 0.30 * s, -0.10 * s)
    p.cubicTo(0.31 * s, -0.02 * s, 0.30 * s, 0.12 * s, 0.40 * s, 0.24 * s)
    p.closeSubpath()
    return p


def _cursor_path(s: float) -> QPainterPath:
    pts = [(0, 0), (0, 17.5), (4.3, 13.4), (7.3, 20.3), (10.2, 19.0), (7.3, 12.3), (13, 12.3)]
    p = QPainterPath()
    p.moveTo(pts[0][0] * s, pts[0][1] * s)
    for x, y in pts[1:]:
        p.lineTo(x * s, y * s)
    p.closeSubpath()
    return p


def draw(p: QPainter, W: int, H: int, t: float, start: float, s: SubscribeSettings) -> None:
    """Desenha a animação no instante ``t`` (aparição que começou em ``start``)."""
    D = max(1.0, s.duration)
    k = D / 6.0
    ph = t - start
    if ph < 0 or ph >= D:
        return
    u = min(W, H) / 1080.0 * max(0.3, s.size)

    # linha do tempo (s, escalada pela duração)
    T_IN = 0.55 * k
    T_CUR_IN = (0.55 * k, 1.35 * k)
    T_CLICK = 1.45 * k
    T_BELL_MOVE = (2.0 * k, 2.6 * k)
    T_BELL = 2.7 * k
    T_CUR_OUT = (3.7 * k, 4.4 * k)
    T_OUT = (D - 0.6 * k, D)
    if not s.bell:
        T_CUR_OUT = (2.3 * k, 3.0 * k)

    # ---------- entrada / saída
    a_in = _seg(ph, 0, T_IN)
    a_out = _seg(ph, *T_OUT)
    alpha = _ease_out(a_in) * (1 - _ease_io(a_out))
    if alpha <= 0.002:
        return
    scale = (0.6 + 0.4 * _back(a_in)) * (1 - 0.08 * a_out)
    dy = (1 - _ease_out(a_in)) * 26 * u + _ease_io(a_out) * 18 * u

    txt, txt_done = s.texts()
    f = QFont(QFont().defaultFamily())
    f.setPixelSize(max(6, int(27 * u)))
    f.setWeight(QFont.Bold)
    f.setLetterSpacing(QFont.PercentageSpacing, 104)
    fm = QFontMetricsF(f)
    bh = 66 * u
    padx = 30 * u
    check_w = 30 * u
    w0 = fm.horizontalAdvance(txt) + 2 * padx
    w1 = fm.horizontalAdvance(txt_done) + 2 * padx + check_w
    done = _ease_io(_seg(ph, T_CLICK + 0.05 * k, T_CLICK + 0.35 * k))
    bw = w0 + (w1 - w0) * done
    bell_d = bh
    gap = 14 * u
    total_w = max(w0, w1) + (gap + bell_d if s.bell else 0)

    # ---------- posição do conjunto (âncora estável: largura máxima, não "pula")
    m = 64 * u
    pos = s.position
    if pos.endswith("left"):
        x0 = m
    elif pos.endswith("right"):
        x0 = W - m - total_w
    else:
        x0 = (W - total_w) / 2
    if pos.startswith("top"):
        y0 = m
    elif pos.startswith("bottom"):
        y0 = H - m - bh
    else:
        y0 = (H - bh) / 2
    y0 += dy

    btn = QRectF(x0, y0, bw, bh)
    bell_c = QPointF(x0 + bw + gap + bell_d / 2, y0 + bh / 2)
    origin = QPointF(x0 + total_w / 2, y0 + bh / 2)

    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    p.setOpacity(alpha)
    p.translate(origin)
    p.scale(scale, scale)
    p.translate(-origin)

    # sombra
    p.setPen(Qt.NoPen)
    for i in range(5, 0, -1):
        c = QColor(0, 0, 0, int(16 * (6 - i) / 5))
        p.setBrush(c)
        r = btn.adjusted(-i * 1.6 * u, -i * 1.2 * u + 4 * u, i * 1.6 * u, i * 1.6 * u + 4 * u)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)

    # botão (vermelho → cinza ao inscrever); "afunda" um pouco no clique
    press = math.sin(math.pi * _seg(ph, T_CLICK - 0.06 * k, T_CLICK + 0.14 * k))
    red = QColor(s.color)
    gray = QColor(48, 48, 52)
    p.save()
    bc = btn.center()
    p.translate(bc)
    p.scale(1 - 0.06 * press, 1 - 0.06 * press)
    p.translate(-bc)
    p.setBrush(_mix(red, gray, done))
    p.drawRoundedRect(btn, bh / 2, bh / 2)
    # brilho sutil no topo
    hl = QColor(255, 255, 255, int(34 * (1 - done) + 10))
    p.setBrush(hl)
    p.drawRoundedRect(QRectF(btn.left() + bh * 0.25, btn.top() + 3 * u, btn.width() - bh * 0.5,
                             bh * 0.38), bh * 0.19, bh * 0.19)
    # ondinha do clique
    rip = _seg(ph, T_CLICK, T_CLICK + 0.6 * k)
    if 0 < rip < 1:
        p.save()
        clip = QPainterPath()
        clip.addRoundedRect(btn, bh / 2, bh / 2)
        p.setClipPath(clip)
        p.setBrush(QColor(255, 255, 255, int(110 * (1 - rip))))
        rr = bw * 0.9 * _ease_out(rip)
        p.drawEllipse(_cursor_target(btn), rr, rr)
        p.restore()
    # texto (troca com fade) + ✓
    p.setFont(f)
    base_y = btn.center().y() + (fm.ascent() - fm.descent()) / 2
    # troca do texto sem sobrepor: o antigo sai na 1ª metade, o novo entra na 2ª
    a_old = 1 - _seg(done, 0.0, 0.45)
    a_new = _seg(done, 0.55, 1.0)
    if a_old > 0:
        p.setPen(QColor(255, 255, 255, int(255 * a_old)))
        p.drawText(QPointF(btn.left() + padx, base_y), txt)
    if a_new > 0:
        done = a_new
        col = QColor(230, 230, 235, int(255 * done))
        p.setPen(QPen(col, 3.6 * u, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        cx, cy = btn.left() + padx + 9 * u, btn.center().y()
        chk = QPainterPath(QPointF(cx - 9 * u, cy))
        chk.lineTo(cx - 3 * u, cy + 6.5 * u)
        chk.lineTo(cx + 9 * u, cy - 7 * u)
        L = done
        p.drawPath(chk if L >= 1 else _partial(chk, L))
        p.setPen(col)
        p.drawText(QPointF(btn.left() + padx + check_w, base_y), txt_done)
    p.restore()

    # sino
    if s.bell:
        rung = _seg(ph, T_BELL, T_BELL + 0.1 * k)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(28, 28, 32, 225))
        p.drawEllipse(bell_c, bell_d / 2, bell_d / 2)
        p.save()
        p.translate(bell_c)
        sw = _seg(ph, T_BELL, T_BELL + 1.0 * k)
        ang = 24 * math.sin(sw * math.pi * 6) * (1 - sw) if 0 < sw < 1 else 0.0
        p.translate(0, -bell_d * 0.18)
        p.rotate(ang)
        p.translate(0, bell_d * 0.18)
        bs = bell_d * 0.74
        body = _bell_path(bs)
        white = QColor(255, 255, 255)
        p.setPen(QPen(white, 0.07 * bs, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(QColor(255, 255, 255, int(255 * rung)))
        p.drawPath(body)
        p.setPen(Qt.NoPen)
        p.setBrush(white)
        p.drawEllipse(QPointF(0, 0.33 * bs), 0.09 * bs, 0.09 * bs)
        p.drawEllipse(QPointF(0, -0.33 * bs), 0.05 * bs, 0.05 * bs)
        p.restore()
        # brilhos ao tocar
        sp = _seg(ph, T_BELL, T_BELL + 0.55 * k)
        if 0 < sp < 1:
            p.setPen(QPen(QColor(255, 255, 255, int(230 * (1 - sp))), 3.2 * u, Qt.SolidLine, Qt.RoundCap))
            for a_deg in (-60, -25, 25, 60, -155, 155):
                a = math.radians(a_deg - 90)
                r0 = bell_d * (0.56 + 0.18 * sp)
                r1 = r0 + bell_d * 0.16 * (1 - sp * 0.5)
                p.drawLine(QPointF(bell_c.x() + r0 * math.cos(a), bell_c.y() + r0 * math.sin(a)),
                           QPointF(bell_c.x() + r1 * math.cos(a), bell_c.y() + r1 * math.sin(a)))

    # cursor
    if s.cursor:
        tgt_btn = _cursor_target(QRectF(x0, y0, w0, bh))
        start_pt = QPointF(tgt_btn.x() + 220 * u, tgt_btn.y() + 170 * u)
        tgt_bell = QPointF(bell_c.x() + 13 * u, bell_c.y() + 15 * u)
        end_pt = QPointF((tgt_bell if s.bell else tgt_btn).x() + 200 * u,
                         (tgt_bell if s.bell else tgt_btn).y() + 190 * u)
        if ph < T_CUR_IN[1]:
            q = _ease_io(_seg(ph, *T_CUR_IN))
            pt = start_pt + (tgt_btn - start_pt) * q
            ca = _seg(ph, T_CUR_IN[0], T_CUR_IN[0] + 0.2 * k)
        elif s.bell and ph < T_BELL_MOVE[1]:
            q = _ease_io(_seg(ph, *T_BELL_MOVE))
            pt = tgt_btn + (tgt_bell - tgt_btn) * q
            ca = 1.0
        else:
            q = _ease_io(_seg(ph, *T_CUR_OUT))
            last = tgt_bell if s.bell else tgt_btn
            pt = last + (end_pt - last) * q
            ca = 1 - q
        clicks = [T_CLICK] + ([T_BELL] if s.bell else [])
        pr = max(math.sin(math.pi * _seg(ph, c - 0.06 * k, c + 0.14 * k)) for c in clicks)
        if ca > 0.01:
            cs = 2.3 * u * (1 - 0.15 * pr)
            p.save()
            p.setOpacity(alpha * ca)
            p.translate(pt)
            path = _cursor_path(cs)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 70))
            p.drawPath(path.translated(2.5 * u, 3 * u))
            p.setPen(QPen(QColor(15, 15, 18), 1.6 * u, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(QColor(255, 255, 255))
            p.drawPath(path)
            p.restore()
    p.restore()


def _cursor_target(btn: QRectF) -> QPointF:
    return QPointF(btn.left() + btn.width() * 0.62, btn.top() + btn.height() * 0.58)


def _partial(path: QPainterPath, frac: float) -> QPainterPath:
    """Trecho inicial de um caminho (para o ✓ "se desenhar")."""
    out = QPainterPath(path.pointAtPercent(0))
    n = 24
    for i in range(1, n + 1):
        out.lineTo(path.pointAtPercent(frac * i / n))
    return out
