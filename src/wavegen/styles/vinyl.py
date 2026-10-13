"""Estilo "Vinil com capa": capa quadrada (imagem personalizada), disco de vinil girando
saindo de trás dela e um waveform de barras arredondadas que nasce no disco e corre para o lado.
"""

from __future__ import annotations

import math
import os

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QFont, QFontMetricsF, QImage,
                           QLinearGradient, QPainterPath, QPen, QRadialGradient)

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


_SCALED: dict = {}


def scaled_square(img: QImage, px: int) -> QImage:
    """Recorte quadrado central da imagem já reduzido para px×px com filtro de alta
    qualidade (média de área do Qt). Desenhar esta versão 1:1 evita o serrilhado de reduzir
    a imagem original grande a cada quadro. Fica em cache por imagem e tamanho."""
    px = max(1, int(px))
    key = (img.cacheKey(), px)
    out = _SCALED.get(key)
    if out is None:
        r = _square_crop(img).toRect()
        sq = img.copy(r)
        # reduções grandes em duas etapas (mais nítido e sem moiré)
        while sq.width() > px * 3:
            sq = sq.scaled(sq.width() // 2, sq.height() // 2, Qt.IgnoreAspectRatio,
                           Qt.SmoothTransformation)
        out = sq.scaled(px, px, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        out = out.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        if len(_SCALED) > 16:
            _SCALED.clear()
        _SCALED[key] = out
    return out


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
                  tip="Vazio = usa a imagem de fundo (com fundo em vídeo, uma capa padrão)"),
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
        ParamSpec("show_title", "Mostrar nome da faixa", "bool", True, group="Nome e contador"),
        ParamSpec("show_counter", "Mostrar contador", "bool", True, group="Nome e contador"),
        ParamSpec("counter_mode", "Contador", "choice", "elapsed_total", group="Nome e contador",
                  choices=[("elapsed_total", "Tempo / duração"), ("remaining", "Tempo restante"),
                           ("elapsed", "Tempo decorrido")]),
        ParamSpec("show_progress", "Barra de progresso", "bool", True, group="Nome e contador"),
        ParamSpec("show_number", "Número da faixa (03 / 12)", "bool", True, group="Nome e contador"),
        ParamSpec("text_layout", "Posição do texto", "choice", "split", group="Nome e contador",
                  choices=[("split", "Nome em cima, contador embaixo"), ("top", "Tudo em cima"),
                           ("bottom", "Tudo embaixo")]),
        ParamSpec("text_size", "Tamanho do texto", "float", 1.0, 0.5, 2.0, 0.05, group="Nome e contador"),
        ParamSpec("text_color", "Cor do texto", "color", "#ffffff", group="Nome e contador"),
        ParamSpec("accent", "Cor de destaque", "color", "#f7a21b", group="Nome e contador",
                  tip="Barra de progresso e número da faixa"),
        ParamSpec("text_shadow", "Sombra no texto", "bool", True, group="Nome e contador"),
        ParamSpec("title_anim", "Animar troca de faixa", "bool", True, group="Nome e contador",
                  tip="O nome entra deslizando quando a faixa muda"),
        ParamSpec("text_bg", "Fundo atrás do texto", "choice", "soft", group="Nome e contador",
                  choices=[("soft", "Sombra suave (esfumada)"), ("glass", "Painel de vidro"),
                           ("off", "Nenhum")],
                  tip="Escurece só a área do nome e do contador para o texto se destacar"),
        ParamSpec("text_bg_opacity", "Opacidade do fundo do texto", "float", 0.45, 0.0, 0.9, 0.05,
                  group="Nome e contador", suffix=""),
        ParamSpec("text_bg_color", "Cor do fundo do texto", "color", "#000000",
                  group="Nome e contador"),
    ]
    default_box = (0.5, 0.5, 0.86, 0.65)

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
        area = QRectF(x0, cover.top(), max(0.0, box.right() - x0), S)
        self._draw_wave(p, area, d, prm, u, S)
        self._draw_disc(p, c, R, d.t, prm, img)
        self._draw_cover(p, cover, prm, u, img)
        if prm["show_title"] or prm["show_counter"]:
            self._draw_text(p, area.adjusted(R * 0.12, 0, 0, 0), d, prm, S)

    # ------------------------------------------------------------------ nome + contador
    @staticmethod
    def _draw_text_bg(p, rects: list[QRectF], prm, S: float, area: QRectF):
        """Escurece só a área do texto. Os pedaços próximos (número + nome, contador + barra)
        viram um bloco; cada bloco ganha uma sombra esfumada ou um painel de vidro."""
        rects = sorted(rects, key=lambda r: r.top())
        blocks: list[QRectF] = []
        for r in rects:
            if blocks and r.top() <= blocks[-1].bottom() + S * 0.04:
                blocks[-1] = blocks[-1].united(r)
            else:
                blocks.append(QRectF(r))
        # mesma largura para todos os blocos: forma uma coluna alinhada, mais harmoniosa
        x0 = min(b.left() for b in blocks)
        x1 = max(b.right() for b in blocks)
        blocks = [QRectF(x0, b.top(), x1 - x0, b.height()) for b in blocks]
        op = float(prm["text_bg_opacity"])
        base = QColor(prm["text_bg_color"])
        padx, pady = S * 0.035, S * 0.018
        p.save()
        p.setRenderHint(p.RenderHint.Antialiasing)
        p.setPen(Qt.NoPen)
        for b in blocks:
            r = b.adjusted(-padx, -pady, padx, pady)
            if prm["text_bg"] == "glass":
                rad = min(r.height() / 2, S * 0.035)
                c = QColor(base)
                c.setAlphaF(op)
                p.setBrush(c)
                p.drawRoundedRect(r, rad, rad)
                hl = QLinearGradient(r.topLeft(), r.bottomLeft())     # brilho sutil no topo
                hl.setColorAt(0.0, QColor(255, 255, 255, int(28 * min(1.0, op * 2))))
                hl.setColorAt(0.5, QColor(255, 255, 255, 0))
                p.setBrush(hl)
                p.drawRoundedRect(r, rad, rad)
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor(255, 255, 255, int(40 * min(1.0, op * 2))), max(1.0, S * 0.0025)))
                p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), rad, rad)
                p.setPen(Qt.NoPen)
            else:
                # sombra esfumada: camadas sobrepostas, mais forte no centro e sumindo nas
                # bordas, sem contorno visível — escurece sem "caixa"
                n = 16
                feather = S * 0.07
                a1 = 1 - (1 - min(op, 0.95)) ** (1 / n)
                c = QColor(base)
                c.setAlphaF(a1)
                p.setBrush(c)
                for i in range(n):
                    e = feather * (1 - i / (n - 1))
                    rr = r.adjusted(-e, -e * 0.5, e, e * 0.5)
                    rad = min(rr.height() / 2, S * 0.06)
                    p.drawRoundedRect(rr, rad, rad)
        p.restore()

    @staticmethod
    def _fmt(sec: float, hours: bool) -> str:
        sec = max(0, int(sec))
        h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
        return f"{h}:{m:02d}:{s:02d}" if hours else f"{m:02d}:{s:02d}"

    def _draw_text(self, p, area: QRectF, d, prm, S):
        if area.width() < 40:
            return
        k = prm["text_size"]
        hmax = S * prm["wave_height"] / 2
        top_h = max(0.0, area.height() / 2 - hmax)          # espaço livre acima/abaixo da onda
        col = QColor(prm["text_color"])
        acc = QColor(prm["accent"])
        fam = QFont().defaultFamily()

        def font(px: float, weight, tabular=False) -> QFont:
            f = QFont(fam)
            f.setPixelSize(max(6, int(px)))
            f.setWeight(weight)
            if tabular:
                try:
                    f.setFeature(QFont.Tag("tnum"), 1)      # algarismos de largura fixa
                except (AttributeError, TypeError):
                    pass
            return f

        rects: list[QRectF] = []
        measuring = [False]

        def text(x, y_base, s, f, c, alpha=1.0):
            if measuring[0]:
                fm = QFontMetricsF(f)
                rects.append(QRectF(x, y_base - fm.ascent(), fm.horizontalAdvance(s), fm.height()))
                return
            p.setFont(f)
            if prm["text_shadow"]:
                sh = QColor(0, 0, 0, int(150 * alpha))
                p.setPen(sh)
                o = max(1.0, f.pixelSize() * 0.06)
                p.drawText(QPointF(x + o, y_base + o), s)
            cc = QColor(c)
            cc.setAlphaF(cc.alphaF() * alpha)
            p.setPen(cc)
            p.drawText(QPointF(x, y_base), s)

        # ---------- linha do título (com animação de entrada na troca de faixa)
        f_title = font(S * 0.095 * k, QFont.Bold)
        f_small = font(S * 0.06 * k, QFont.DemiBold, tabular=True)
        fm_t, fm_s = QFontMetricsF(f_title), QFontMetricsF(f_small)
        title_h = fm_t.height() if prm["show_title"] else 0.0
        num_h = fm_s.height() * 1.15 if prm["show_number"] and d.track_count > 0 else 0.0
        cnt_h = fm_s.height() * (1.9 if prm["show_progress"] else 1.0) if prm["show_counter"] else 0.0

        layout = prm["text_layout"]
        pad = S * 0.02
        if layout == "top":
            y_title = area.top() + pad
            y_cnt = y_title + num_h + title_h + pad * 0.5
        elif layout == "bottom":
            blk = num_h + title_h + pad * 0.5 + cnt_h
            y_title = area.bottom() - pad - blk
            y_cnt = y_title + num_h + title_h + pad * 0.5
        else:
            y_title = area.top() + max(pad, top_h - num_h - title_h - pad)
            y_cnt = area.bottom() - top_h + pad
        x = area.left()
        wmax = area.width()

        p.save()
        p.setRenderHint(p.RenderHint.TextAntialiasing)
        use_bg = prm["text_bg"] != "off" and prm["text_bg_opacity"] > 0
        for measuring[0] in ((True, False) if use_bg else (False,)):
            if not measuring[0] and rects:
                self._draw_text_bg(p, rects, prm, S, area)
            if prm["show_title"]:
                a, dx = 1.0, 0.0
                if prm["title_anim"] and d.track_start > 0:          # não anima a 1ª faixa
                    ph = min(1.0, d.track_pos / 0.7)
                    e = 1 - (1 - ph) ** 3                         # ease-out
                    a, dx = e, (1 - e) * S * 0.12
                if num_h:
                    num = f"{d.track_index:02d} / {d.track_count:02d}"
                    text(x + dx, y_title + fm_s.ascent(), num, f_small, acc, a)
                # nome longo: diminui a fonte (até 60%) antes de cortar com "…"
                ft, fmt = f_title, fm_t
                tw = fm_t.horizontalAdvance(d.track_title)
                if tw > wmax:
                    ft = font(f_title.pixelSize() * max(0.6, wmax / tw), QFont.Bold)
                    fmt = QFontMetricsF(ft)
                title = fmt.elidedText(d.track_title, Qt.ElideRight, wmax)
                base_y = y_title + num_h + fm_t.ascent() - (fm_t.ascent() - fmt.ascent()) * 0.5
                text(x + dx, base_y, title, ft, col, a)

            if prm["show_counter"]:
                hours = d.track_len >= 3600
                el, tot = d.track_pos, d.track_len
                mode = prm["counter_mode"]
                if mode == "remaining":
                    left_s, right_s = "-" + self._fmt(tot - el, hours), ""
                elif mode == "elapsed":
                    left_s, right_s = self._fmt(el, hours), ""
                else:
                    left_s, right_s = self._fmt(el, hours), self._fmt(tot, hours)
                if not prm["show_title"] and num_h:
                    left_s = f"{d.track_index:02d}/{d.track_count:02d}   " + left_s
                yb = y_cnt + fm_s.ascent()
                if prm["show_progress"]:
                    bar_y = y_cnt + fm_s.height() * 0.5
                    lw = fm_s.horizontalAdvance(left_s) + S * 0.03
                    rw = (fm_s.horizontalAdvance(right_s) + S * 0.03) if right_s else 0.0
                    bx0, bx1 = x + lw, x + wmax - rw
                    if bx1 - bx0 < S * 0.15:                     # pouco espaço: barra embaixo
                        bx0, bx1 = x, x + wmax
                        bar_y = y_cnt + fm_s.height() * 1.45
                        rw = 0.0
                    text(x, yb, left_s, f_small, col)
                    if right_s:
                        rx = x + wmax - fm_s.horizontalAdvance(right_s)
                        text(rx, yb, right_s, f_small, col, 0.75)
                    th = max(2.0, S * 0.012 * k)
                    if measuring[0]:
                        rects.append(QRectF(bx0, bar_y - th * 1.6, bx1 - bx0, th * 3.2))
                        continue
                    frac = 0.0 if tot <= 0 else min(1.0, el / tot)
                    p.setPen(Qt.NoPen)
                    bg = QColor(col)
                    bg.setAlphaF(0.25)
                    p.setBrush(bg)
                    p.drawRoundedRect(QRectF(bx0, bar_y - th / 2, bx1 - bx0, th), th / 2, th / 2)
                    p.setBrush(acc)
                    fx = bx0 + (bx1 - bx0) * frac
                    p.drawRoundedRect(QRectF(bx0, bar_y - th / 2, max(th, fx - bx0), th), th / 2, th / 2)
                    p.setBrush(col)
                    p.drawEllipse(QPointF(fx, bar_y), th * 1.6, th * 1.6)
                else:
                    s = left_s + (f"  /  {right_s}" if right_s else "")
                    text(x, yb, s, f_small, col)
        p.restore()

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
        # Barras alinhadas à grade de pixels: largura inteira e bordas em pixels inteiros,
        # então todas as barras têm a mesma espessura e ficam nítidas (sem "tremer" ao rolar).
        wp = max(3, int(round(w)))          # ≥3 px: pontos e pontas ficam redondos
        rad = wp / 2
        path = QPainterPath()
        for i, v in enumerate(vals):
            x = area.left() + (i + frac) * step + step / 2
            xl = round(x - wp / 2)
            if xl > area.right():
                break
            h = max(wp, int(round(2 * v * hmax)))
            top = round(cy - h / 2)
            path.addRoundedRect(QRectF(xl, top, wp, h), rad, rad)
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
            p.setRenderHint(p.RenderHint.SmoothPixmapTransform)
            p.setRenderHint(p.RenderHint.Antialiasing)
            p.setClipPath(clip)
            p.translate(c)
            p.rotate(-ang)
            lab = scaled_square(img, math.ceil(2 * rl) + 2)
            p.drawImage(QRectF(-rl, -rl, 2 * rl, 2 * rl), lab)
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
        r = QRectF(round(r.left()), round(r.top()), round(r.width()), round(r.width()))
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
            # posição e tamanho em pixels inteiros + imagem já reduzida = desenho 1:1, nítido
            p.drawImage(r.topLeft(), scaled_square(img, int(r.width())))
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
