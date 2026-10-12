"""Widgets reutilizáveis: controles numéricos, cor, painel de parâmetros, galeria."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget)

from ..styles.base import ParamSpec


class ColorButton(QPushButton):
    colorChanged = Signal(str)

    def __init__(self, color: str = "#ffffff", parent=None):
        super().__init__(parent)
        self.setFixedSize(64, 26)
        self.setCursor(Qt.PointingHandCursor)
        self._color = color
        self.clicked.connect(self._pick)
        self._refresh()

    def color(self) -> str:
        return self._color

    def set_color(self, c: str):
        self._color = c
        self._refresh()

    def _refresh(self):
        c = QColor(self._color)
        txt = "#000" if c.lightnessF() > 0.6 else "#fff"
        self.setText(c.name().upper())
        self.setStyleSheet(f"QPushButton{{background:{c.name()};color:{txt};border:1px solid #555;"
                           f"border-radius:4px;font-size:10px}}")

    def _pick(self):
        c = QColorDialog.getColor(QColor(self._color), self, "Escolher cor",
                                  QColorDialog.ShowAlphaChannel)
        if c.isValid():
            self._color = c.name(QColor.HexArgb) if c.alpha() < 255 else c.name()
            self._refresh()
            self.colorChanged.emit(self._color)


class SliderSpin(QWidget):
    """Controle deslizante + campo numérico preciso, sincronizados."""

    valueChanged = Signal(float)

    def __init__(self, lo: float, hi: float, step: float, value: float, decimals: int | None = None,
                 suffix: str = "", integer: bool = False, parent=None):
        super().__init__(parent)
        self.lo, self.hi, self.step = lo, hi, step
        self.integer = integer
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, max(1, round((hi - lo) / step)))
        if integer:
            self.spin = QSpinBox()
            self.spin.setRange(int(lo), int(hi))
            self.spin.setSingleStep(max(1, int(step)))
        else:
            self.spin = QDoubleSpinBox()
            self.spin.setRange(lo, hi)
            self.spin.setSingleStep(step)
            if decimals is None:
                decimals = 0 if step >= 1 else (1 if step >= 0.1 else 2 if step >= 0.01 else 3)
            self.spin.setDecimals(decimals)
        self.spin.setSuffix(suffix)
        self.spin.setFixedWidth(84)
        self.spin.setKeyboardTracking(False)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.spin)
        self._block = False
        self.slider.valueChanged.connect(self._from_slider)
        self.spin.valueChanged.connect(self._from_spin)
        self.set_value(value)

    def value(self) -> float:
        return self.spin.value()

    def set_value(self, v: float):
        self._block = True
        self.spin.setValue(int(round(v)) if self.integer else v)
        self.slider.setValue(round((v - self.lo) / self.step))
        self._block = False

    def _from_slider(self, i: int):
        if self._block:
            return
        v = self.lo + i * self.step
        self._block = True
        self.spin.setValue(int(round(v)) if self.integer else v)
        self._block = False
        self.valueChanged.emit(self.spin.value())

    def _from_spin(self, v: float):
        if self._block:
            return
        self._block = True
        self.slider.setValue(round((v - self.lo) / self.step))
        self._block = False
        self.valueChanged.emit(float(v))


class ImagePicker(QWidget):
    """Escolher uma imagem (ou limpar, voltando ao padrão)."""

    pathChanged = Signal(str)

    def __init__(self, path: str = "", empty_text: str = "", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.thumb = QLabel()
        self.thumb.setFixedSize(40, 40)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet("QLabel{border:1px solid #555;border-radius:3px;color:#888}")
        lay.addWidget(self.thumb)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setStyleSheet("font-size:11px")
        lay.addWidget(self.label, 1)
        b = QPushButton("Escolher…")
        b.clicked.connect(self._pick)
        lay.addWidget(b)
        self.clear_btn = QPushButton("✕")
        self.clear_btn.setFixedWidth(28)
        self.clear_btn.setToolTip("Remover (volta ao padrão)")
        self.clear_btn.clicked.connect(lambda: self._set("", emit=True))
        lay.addWidget(self.clear_btn)
        self.empty_text = empty_text or "Nenhuma"
        self._set(path)

    def _set(self, path: str, emit: bool = False):
        from pathlib import Path
        self.path = path
        self.clear_btn.setEnabled(bool(path))
        pm = QPixmap(path) if path else QPixmap()
        if path and not pm.isNull():
            self.thumb.setPixmap(pm.scaled(38, 38, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
            self.label.setText(Path(path).name)
            self.label.setToolTip(path)
        else:
            self.thumb.setPixmap(QPixmap())
            self.thumb.setText("—" if not path else "?")
            self.label.setText(self.empty_text if not path else f"Não encontrada: {Path(path).name}")
        if emit:
            self.pathChanged.emit(path)

    def _pick(self):
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog
        start = str(Path(self.path).parent) if self.path else str(Path.home() / "Pictures")
        f, _ = QFileDialog.getOpenFileName(self, "Escolher imagem", start,
                                           "Imagens (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff "
                                           "*.PNG *.JPG *.JPEG *.WEBP)")
        if f:
            self._set(f, emit=True)


def make_editor(spec: ParamSpec, value, on_change: Callable[[str, object], None]) -> QWidget:
    if spec.kind in ("float", "int"):
        w = SliderSpin(spec.min, spec.max, spec.step, float(value), suffix=spec.suffix,
                       integer=spec.kind == "int")
        w.valueChanged.connect(lambda v: on_change(spec.key, int(v) if spec.kind == "int" else float(v)))
    elif spec.kind == "bool":
        w = QCheckBox()
        w.setChecked(bool(value))
        w.toggled.connect(lambda v: on_change(spec.key, bool(v)))
    elif spec.kind == "color":
        w = ColorButton(str(value))
        w.colorChanged.connect(lambda c: on_change(spec.key, c))
    elif spec.kind == "image":
        w = ImagePicker(str(value or ""), spec.tip)
        w.pathChanged.connect(lambda v: on_change(spec.key, v))
    elif spec.kind == "choice":
        w = QComboBox()
        for val, label in spec.choices:
            w.addItem(label, val)
        idx = max(0, [c[0] for c in spec.choices].index(value) if value in [c[0] for c in spec.choices] else 0)
        w.setCurrentIndex(idx)
        w.currentIndexChanged.connect(lambda i: on_change(spec.key, w.itemData(i)))
    else:
        w = QLabel("?")
    if spec.tip:
        w.setToolTip(spec.tip)
    return w


GROUP_ORDER = ["Capa", "Disco", "Nome e contador", "Cores", "Tamanho", "Estilo", "Comportamento"]


class ParamPanel(QWidget):
    """Gera os controles a partir dos ParamSpec do estilo, agrupados."""

    changed = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.groups: dict[str, QFormLayout] = {}
        self.extra: dict[str, list[tuple[str, QWidget]]] = {}

    def build(self, specs: list[ParamSpec], values: dict,
              extra_rows: dict[str, list[tuple[str, QWidget]]] | None = None):
        while self.lay.count():
            item = self.lay.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
        self.groups = {}
        by_group: dict[str, list[ParamSpec]] = {}
        for s in specs:
            by_group.setdefault(s.group, []).append(s)
        extra_rows = extra_rows or {}
        names = [g for g in GROUP_ORDER if g in by_group or g in extra_rows]
        names += [g for g in list(by_group) + list(extra_rows) if g not in names]
        for g in names:
            box = QGroupBox(g)
            form = QFormLayout(box)
            form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            for s in by_group.get(g, []):
                form.addRow(s.label, make_editor(s, values.get(s.key, s.default),
                                                 lambda k, v: self.changed.emit(k, v)))
            for label, w in extra_rows.get(g, []):
                form.addRow(label, w)
            self.lay.addWidget(box)
            self.groups[g] = form
        self.lay.addStretch(1)


class StyleGallery(QListWidget):
    styleSelected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListWidget.IconMode)
        self.setIconSize(QSize(168, 95))
        self.setGridSize(QSize(184, 124))
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setWordWrap(True)
        self.setSpacing(4)
        self.setUniformItemSizes(True)
        self.setMinimumHeight(290)
        self.currentItemChanged.connect(self._sel)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.doItemsLayout()         # reorganiza as colunas quando o painel muda de largura

    def populate(self, styles: list, thumbs: dict[str, QPixmap]):
        self.blockSignals(True)
        self.clear()
        for cls in styles:
            it = QListWidgetItem(QIcon(thumbs.get(cls.id, QPixmap())), cls.name)
            it.setData(Qt.UserRole, cls.id)
            it.setToolTip(f"<b>{cls.name}</b><br>{cls.description}")
            self.addItem(it)
        self.blockSignals(False)

    def select(self, style_id: str):
        for i in range(self.count()):
            if self.item(i).data(Qt.UserRole) == style_id:
                self.blockSignals(True)
                self.setCurrentRow(i)
                self.blockSignals(False)
                return

    def _sel(self, cur, _prev):
        if cur is not None:
            self.styleSelected.emit(cur.data(Qt.UserRole))
