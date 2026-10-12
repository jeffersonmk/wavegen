"""Área de pré-visualização: mostra o quadro composto e permite mover/redimensionar o
visualizador com o mouse (arrastar = mover, cantos/bordas = redimensionar)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QImage, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..project import Box

HANDLE = 7  # px na tela


class PreviewView(QWidget):
    boxChanged = Signal(object)        # Box (durante o arraste)
    boxEdited = Signal()               # arraste terminou
    resized = Signal()                 # tamanho do painel mudou (re-render na nova resolução)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(QSize(320, 180))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.image: QImage | None = None
        self.box: Box | None = None
        self.frame_aspect = 16 / 9
        self.show_guides = True
        self.message = ""
        self._drag = None              # (modo, ponto inicial, box inicial)

    # ------------------------------------------------------------------ dados
    def set_frame(self, img: QImage, box: Box):
        self.image = img
        self.box = box
        self.frame_aspect = img.width() / max(img.height(), 1)
        self.update()

    def set_message(self, text: str):
        self.message = text
        self.update()

    # ------------------------------------------------------------------ geometria
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.resized.emit()

    def pixel_width(self) -> int:
        """Largura real, em pixels da tela, da área onde o quadro aparece."""
        return int(self.frame_rect().width() * self.devicePixelRatioF())

    def frame_rect(self) -> QRectF:
        W, H = self.width() - 16, self.height() - 16
        if W / max(H, 1) > self.frame_aspect:
            h = H
            w = h * self.frame_aspect
        else:
            w = W
            h = w / self.frame_aspect
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def box_rect(self) -> QRectF | None:
        if self.box is None:
            return None
        fr = self.frame_rect()
        b = self.box
        w, h = b.w * b.scale * fr.width(), b.h * b.scale * fr.height()
        return QRectF(fr.left() + b.x * fr.width() - w / 2, fr.top() + b.y * fr.height() - h / 2, w, h)

    def _hit(self, pos: QPointF) -> str | None:
        r = self.box_rect()
        if r is None:
            return None
        x, y = pos.x(), pos.y()
        near_l, near_r = abs(x - r.left()) <= HANDLE, abs(x - r.right()) <= HANDLE
        near_t, near_b = abs(y - r.top()) <= HANDLE, abs(y - r.bottom()) <= HANDLE
        inside_x = r.left() - HANDLE <= x <= r.right() + HANDLE
        inside_y = r.top() - HANDLE <= y <= r.bottom() + HANDLE
        if not (inside_x and inside_y):
            return None
        edge = ("t" if near_t else "b" if near_b else "") + ("l" if near_l else "r" if near_r else "")
        if edge:
            return edge
        return "move" if r.contains(pos) else None

    _CURSORS = {"move": Qt.SizeAllCursor, "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
                "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor, "tl": Qt.SizeFDiagCursor,
                "br": Qt.SizeFDiagCursor, "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor}

    # ------------------------------------------------------------------ mouse
    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton or self.box is None:
            return
        mode = self._hit(e.position())
        if mode:
            b = self.box
            self._drag = (mode, e.position(), Box(b.x, b.y, b.w, b.h, b.scale))

    def mouseMoveEvent(self, e):
        if self._drag is None:
            mode = self._hit(e.position())
            self.setCursor(QCursor(self._CURSORS.get(mode, Qt.ArrowCursor)))
            return
        mode, p0, b0 = self._drag
        fr = self.frame_rect()
        dx = (e.position().x() - p0.x()) / fr.width()
        dy = (e.position().y() - p0.y()) / fr.height()
        b = Box(b0.x, b0.y, b0.w, b0.h, b0.scale)
        fine = bool(e.modifiers() & Qt.ShiftModifier)
        if fine:
            dx, dy = dx * 0.25, dy * 0.25
        if mode == "move":
            b.x, b.y = b0.x + dx, b0.y + dy
            if not fine:                       # ímã no centro
                if abs(b.x - 0.5) < 0.012:
                    b.x = 0.5
                if abs(b.y - 0.5) < 0.012:
                    b.y = 0.5
        else:
            s = max(b0.scale, 1e-6)
            left, right = b0.x - b0.w * s / 2, b0.x + b0.w * s / 2
            top, bottom = b0.y - b0.h * s / 2, b0.y + b0.h * s / 2
            if "l" in mode:
                left = min(left + dx, right - 0.02)
            if "r" in mode:
                right = max(right + dx, left + 0.02)
            if "t" in mode:
                top = min(top + dy, bottom - 0.02)
            if "b" in mode:
                bottom = max(bottom + dy, top + 0.02)
            b.x, b.y = (left + right) / 2, (top + bottom) / 2
            b.w, b.h = (right - left) / s, (bottom - top) / s
        b.x = min(max(b.x, -0.5), 1.5)
        b.y = min(max(b.y, -0.5), 1.5)
        b.w = min(max(b.w, 0.02), 2.0)
        b.h = min(max(b.h, 0.02), 2.0)
        self.box = b
        self.boxChanged.emit(b)
        self.update()

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            self.boxEdited.emit()

    # ------------------------------------------------------------------ desenho
    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0d0d10"))
        fr = self.frame_rect()
        if self.image is not None:
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            # quadriculado para mostrar transparência
            p.save()
            p.setClipRect(fr)
            sz = 12
            for yy in range(int(fr.top()), int(fr.bottom()) + sz, sz):
                for xx in range(int(fr.left()), int(fr.right()) + sz, sz):
                    c = QColor("#2a2a2e") if ((xx // sz + yy // sz) % 2) else QColor("#202024")
                    p.fillRect(xx, yy, sz, sz, c)
            p.restore()
            p.drawImage(fr, self.image)
        r = self.box_rect()
        if r is not None and self.show_guides:
            active = self._drag is not None or self.underMouse()
            pen = QPen(QColor(255, 255, 255, 170 if active else 70), 1, Qt.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRect(r)
            if active:
                p.setPen(QPen(QColor("#ffffff"), 1))
                p.setBrush(QColor("#3d8bfd"))
                for hx in (r.left(), r.center().x(), r.right()):
                    for hy in (r.top(), r.center().y(), r.bottom()):
                        if hx == r.center().x() and hy == r.center().y():
                            continue
                        p.drawRect(QRectF(hx - 4, hy - 4, 8, 8))
            if self._drag is not None:
                p.setPen(QPen(QColor(61, 139, 253, 120), 1))
                if self.box and abs(self.box.x - 0.5) < 1e-9:
                    p.drawLine(QPointF(fr.center().x(), fr.top()), QPointF(fr.center().x(), fr.bottom()))
                if self.box and abs(self.box.y - 0.5) < 1e-9:
                    p.drawLine(QPointF(fr.left(), fr.center().y()), QPointF(fr.right(), fr.center().y()))
        if self.message:
            p.setPen(QColor("#ffffff"))
            m = QRectF(fr.left() + 10, fr.bottom() - 34, fr.width() - 20, 26)
            p.fillRect(m, QColor(0, 0, 0, 150))
            p.drawText(m, Qt.AlignCenter, self.message)
        p.end()
