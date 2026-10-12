"""Janela principal do Wavegen."""

from __future__ import annotations

import json
import os
import sys
import time
from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import QProcess, QSettings, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QIcon, QImage, QKeySequence,
                           QPainter, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox,
                               QComboBox, QDockWidget, QFileDialog, QFormLayout, QFrame,
                               QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QRadioButton, QScrollArea, QSlider, QSplitter,
                               QStyle, QTabWidget, QToolButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from .. import __version__
from ..analysis import ensure_features
from ..compositor import IMAGE_EXTS, Compositor, compose_background, load_image
from ..video import VIDEO_EXTS, VideoDecoder, is_video, probe_video
from ..export import (FORMATS, FPS_CHOICES, PRESETS, QUALITIES, default_filename,
                      estimate_size, extension, fps_of, is_transparent, resolution)
from ..ffmpeg import WavegenError, check_vaapi, encoders, ffmpeg_path, ffmpeg_version
from ..playlist import AUDIO_EXTS, CACHE_DIR, Track, ensure_mix, open_pcm, total_duration, ordered
from ..project import Box, Project, load_prefs, save_prefs
from ..styles import get_style, style_list
from .demo import SR as DEMO_SR
from .demo import demo_features, demo_pcm
from .player import AudioPlayer
from .preview import PreviewView
from .tasks import Task
from .widgets import ColorButton, ParamPanel, SliderSpin, StyleGallery

PREVIEW_LONG_SIDE = 960          # mínimo; o preview acompanha o tamanho real do painel
PREVIEW_MAX_SIDE = 1920


def fmt_time(s: float) -> str:
    s = max(0, int(round(s)))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def fmt_size(b: float) -> str:
    if b >= 1e9:
        return f"{b / 1e9:.2f} GB"
    return f"{b / 1e6:.0f} MB" if b >= 1e6 else f"{b / 1e3:.0f} KB"


class PlaylistWidget(QTreeWidget):
    filesDropped = Signal(list)
    reordered = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(3)
        self.setHeaderLabels(["#", "Música", "Duração"])
        self.setRootIsDecorated(False)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setAcceptDrops(True)
        self.setAlternatingRowColors(True)
        h = self.header()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeToContents)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e):
        if e.mimeData().hasUrls():
            self.filesDropped.emit([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])
            e.acceptProposedAction()
            return
        super().dropEvent(e)
        self.reordered.emit()


class MainWindow(QMainWindow):
    def __init__(self, project_path: str | None = None):
        super().__init__()
        self.setWindowTitle("Wavegen — vídeos musicais com waveform")
        self.resize(1500, 900)
        self.setAcceptDrops(True)
        self.prefs = load_prefs()
        self.project = Project()
        self.project_path: str | None = None
        self.dirty = False

        # estado de áudio/preview
        self.feats = None
        self.pcm = None
        self.sr = DEMO_SR
        self.using_demo = True
        self.mix_task: Task | None = None
        self.mix_status = ""
        self.player = AudioPlayer()
        self.comp: Compositor | None = None
        self._src_cache: dict = {}
        self._bg_cache: dict = {}
        self._video: VideoDecoder | None = None
        self.cur_frame = 0
        self.render_proc: QProcess | None = None
        self.render_t0 = 0.0
        self.last_out = ""
        self.vaapi_ok: bool | None = None
        self._loading = False

        self._apply_export_prefs()
        self._build_ui()
        self._build_menu()

        self.rebuild_timer = QTimer(self, singleShot=True, interval=40, timeout=self.rebuild_preview)
        self.mix_timer = QTimer(self, singleShot=True, interval=350, timeout=self.start_mix)
        self.play_timer = QTimer(self, interval=15, timeout=self._tick)
        self.elapsed_timer = QTimer(self, interval=500, timeout=self._update_elapsed)

        self._make_thumbnails()
        self._load_project_into_ui()
        self._check_environment()
        if project_path:
            QTimer.singleShot(0, lambda: self.open_project(project_path))
        geo = self.prefs.get("geometry")
        if geo:
            from PySide6.QtCore import QByteArray
            self.restoreGeometry(QByteArray.fromHex(geo.encode()))

    # ================================================================== UI
    def _build_ui(self):
        # ---------------- esquerda: arquivos
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(6, 6, 6, 6)

        gi = QGroupBox("1 · Fundo (imagem ou vídeo)")
        gl = QVBoxLayout(gi)
        self.img_thumb = QLabel("Nenhum fundo\n(arraste uma imagem ou vídeo para a janela)")
        self.img_thumb.setAlignment(Qt.AlignCenter)
        self.img_thumb.setMinimumHeight(130)
        self.img_thumb.setStyleSheet("QLabel{border:1px dashed #555;border-radius:6px;color:#999}")
        gl.addWidget(self.img_thumb)
        row = QHBoxLayout()
        b = QPushButton("Escolher imagem ou vídeo…")
        b.clicked.connect(self.choose_image)
        row.addWidget(b)
        self.btn_img_clear = QPushButton("Remover")
        self.btn_img_clear.clicked.connect(lambda: self.set_image(""))
        row.addWidget(self.btn_img_clear)
        gl.addLayout(row)
        self.img_info = QLabel("")
        self.img_info.setWordWrap(True)
        self.img_info.setStyleSheet("color:#bbb;font-size:11px")
        gl.addWidget(self.img_info)
        ll.addWidget(gi)

        ga = QGroupBox("2 · Músicas (ordem de reprodução)")
        al = QVBoxLayout(ga)
        self.playlist = PlaylistWidget()
        self.playlist.filesDropped.connect(self.add_files)
        self.playlist.reordered.connect(self._sync_order_from_widget)
        al.addWidget(self.playlist, 1)
        row = QHBoxLayout()
        for text, tip, slot in (("＋ Adicionar", "Adicionar músicas", self.choose_audio),
                                ("－ Remover", "Remover selecionadas", self.remove_tracks),
                                ("↑", "Mover para cima", lambda: self.move_tracks(-1)),
                                ("↓", "Mover para baixo", lambda: self.move_tracks(1))):
            bt = QPushButton(text)
            bt.setToolTip(tip)
            bt.clicked.connect(slot)
            if len(text) == 1:
                bt.setFixedWidth(34)
            row.addWidget(bt)
        al.addLayout(row)
        self.total_label = QLabel("Duração total: —")
        self.total_label.setStyleSheet("font-weight:bold")
        al.addWidget(self.total_label)
        self.btn_tracklist = QPushButton("Lista de faixas para o YouTube…")
        self.btn_tracklist.setToolTip("Tempos de início de cada música no vídeo, para colar na descrição")
        self.btn_tracklist.clicked.connect(self.show_tracklist)
        al.addWidget(self.btn_tracklist)
        ll.addWidget(ga, 1)

        # ---------------- centro: preview
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 6, 0, 6)
        self.preview = PreviewView()
        self.preview.boxChanged.connect(self._box_dragged)
        self.preview.boxEdited.connect(self._box_drag_done)
        self.resize_timer = QTimer(self, singleShot=True, interval=200, timeout=self._preview_resized)
        self.preview.resized.connect(self.resize_timer.start)
        cl.addWidget(self.preview, 1)
        tr = QHBoxLayout()
        self.btn_play = QToolButton()
        self.btn_play.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.btn_play.setIconSize(QSize(22, 22))
        self.btn_play.setToolTip("Reproduzir / pausar (Espaço)")
        self.btn_play.clicked.connect(self.toggle_play)
        tr.addWidget(self.btn_play)
        self.time_slider = QSlider(Qt.Horizontal)
        self.time_slider.setRange(0, 0)
        self.time_slider.sliderMoved.connect(self._seek_slider)
        self.time_slider.sliderPressed.connect(lambda: self._seek_slider(self.time_slider.value()))
        tr.addWidget(self.time_slider, 1)
        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setMinimumWidth(110)
        tr.addWidget(self.time_label)
        tr.addWidget(QLabel("🔊"))
        self.preview_vol = QSlider(Qt.Horizontal)
        self.preview_vol.setRange(0, 100)
        self.preview_vol.setValue(int(self.prefs.get("preview_volume", 80)))
        self.preview_vol.setFixedWidth(90)
        self.preview_vol.setToolTip("Volume da pré-visualização (não afeta o vídeo)")
        self.preview_vol.valueChanged.connect(self._preview_volume)
        self._preview_volume(self.preview_vol.value())
        tr.addWidget(self.preview_vol)
        self.chk_guides = QCheckBox("Guias")
        self.chk_guides.setChecked(True)
        self.chk_guides.setToolTip("Mostrar a moldura do visualizador (arraste para mover, cantos para redimensionar)")
        self.chk_guides.toggled.connect(self._toggle_guides)
        tr.addWidget(self.chk_guides)
        cl.addLayout(tr)
        self.preview_note = QLabel("")
        self.preview_note.setStyleSheet("color:#999;font-size:11px")
        cl.addWidget(self.preview_note)

        # ---------------- direita: abas
        self.tabs = QTabWidget()
        self.tabs.setMinimumWidth(430)
        # estilos
        ts = QWidget()
        tsl = QVBoxLayout(ts)
        hint = QLabel("Escolha o estilo do visualizador (as miniaturas usam áudio de demonstração):")
        hint.setWordWrap(True)
        tsl.addWidget(hint)
        self.gallery = StyleGallery()
        self.gallery.styleSelected.connect(self.set_style)
        tsl.addWidget(self.gallery, 1)
        self.style_desc = QLabel("")
        self.style_desc.setWordWrap(True)
        self.style_desc.setStyleSheet("color:#bbb")
        tsl.addWidget(self.style_desc)
        self.tabs.addTab(ts, "3 · Estilo")
        # personalizar
        self.param_panel = ParamPanel()
        self.param_panel.changed.connect(self.set_param)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.addWidget(self.param_panel)
        btn_reset = QPushButton("Restaurar padrões deste estilo")
        btn_reset.clicked.connect(self.reset_style)
        hl.addWidget(btn_reset)
        sc.setWidget(holder)
        self.tabs.addTab(sc, "4 · Personalizar")
        self._build_position_controls()
        self._build_behavior_controls()
        self.tabs.addTab(self._build_background_tab(), "Fundo")
        self.tabs.addTab(self._build_audio_tab(), "Áudio")
        self.tabs.addTab(self._build_export_tab(), "5 · Exportar")

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left)
        split.addWidget(center)
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        split.setSizes([330, 760, 450])
        self.splitter = split

        # ---------------- rodapé: renderização
        bottom = QFrame()
        bottom.setFrameShape(QFrame.StyledPanel)
        bl = QHBoxLayout(bottom)
        self.btn_render = QPushButton("  Renderizar vídeo")
        self.btn_render.setIcon(self.style().standardIcon(QStyle.SP_DialogSaveButton))
        self.btn_render.setMinimumHeight(38)
        self.btn_render.setStyleSheet("QPushButton{font-weight:bold;font-size:14px;padding:0 18px}")
        self.btn_render.clicked.connect(self.start_render)
        bl.addWidget(self.btn_render)
        self.btn_sample = QPushButton("Testar 15 s")
        self.btn_sample.setToolTip("Renderiza só 15 segundos a partir da posição atual, para conferir o resultado final")
        self.btn_sample.clicked.connect(lambda: self.start_render(sample=True))
        bl.addWidget(self.btn_sample)
        self.btn_cancel = QPushButton("Cancelar")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_render)
        bl.addWidget(self.btn_cancel)
        vb = QVBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFormat("")
        vb.addWidget(self.progress)
        self.status = QLabel("Pronto.")
        vb.addWidget(self.status)
        bl.addLayout(vb, 1)
        self.elapsed_label = QLabel("")
        self.elapsed_label.setMinimumWidth(220)
        self.elapsed_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bl.addWidget(self.elapsed_label)
        self.btn_open_out = QPushButton("Abrir pasta")
        self.btn_open_out.setEnabled(False)
        self.btn_open_out.clicked.connect(self.open_output_folder)
        bl.addWidget(self.btn_open_out)
        self.btn_log = QPushButton("Diagnóstico")
        self.btn_log.setCheckable(True)
        bl.addWidget(self.btn_log)

        root = QWidget()
        rl = QVBoxLayout(root)
        rl.setContentsMargins(4, 0, 4, 4)
        rl.addWidget(split, 1)
        rl.addWidget(bottom)
        self.setCentralWidget(root)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setStyleSheet("font-family:monospace;font-size:11px")
        self.log_dock = QDockWidget("Diagnóstico / log técnico", self)
        self.log_dock.setWidget(self.log)
        self.log_dock.setObjectName("log")
        self.addDockWidget(Qt.BottomDockWidgetArea, self.log_dock)
        self.log_dock.hide()
        self.btn_log.toggled.connect(self.log_dock.setVisible)
        self.log_dock.visibilityChanged.connect(self.btn_log.setChecked)

    # ------------------------------------------------------------------ posição
    def _build_position_controls(self):
        self.pos_widgets = {}
        rows = []
        for key, label, lo, hi in (("x", "Horizontal (centro)", -20, 120), ("y", "Vertical (centro)", -20, 120),
                                   ("w", "Largura", 2, 200), ("h", "Altura", 2, 200),
                                   ("scale", "Escala geral", 10, 300)):
            w = SliderSpin(lo, hi, 0.5, 50, decimals=1, suffix=" %")
            w.valueChanged.connect(lambda v, k=key: self.set_box_value(k, v / 100))
            self.pos_widgets[key] = w
            rows.append((label, w))
        self.margin_spin = SliderSpin(0, 30, 0.5, 5, decimals=1, suffix=" %")
        self.margin_spin.setToolTip("Distância da borda usada pelos botões de alinhamento")
        rows.append(("Margem das bordas", self.margin_spin))
        ah = QHBoxLayout()
        for txt, mode in (("⇤ Esquerda", "left"), ("↔ Centro", "hcenter"), ("Direita ⇥", "right")):
            b = QPushButton(txt)
            b.clicked.connect(lambda _=False, m=mode: self.align_box(m))
            ah.addWidget(b)
        wh = QWidget()
        wh.setLayout(ah)
        av = QHBoxLayout()
        for txt, mode in (("⤒ Topo", "top"), ("↕ Meio", "vcenter"), ("Base ⤓", "bottom")):
            b = QPushButton(txt)
            b.clicked.connect(lambda _=False, m=mode: self.align_box(m))
            av.addWidget(b)
        wv = QWidget()
        wv.setLayout(av)
        rows += [("Alinhar", wh), ("", wv)]
        hint = QLabel("Dica: arraste o visualizador na pré-visualização; use os cantos para "
                      "redimensionar (Shift = ajuste fino).")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#999;font-size:11px")
        rows.append(("", hint))
        self.position_rows = rows

    def _build_behavior_controls(self):
        r = self.project.response
        self.beh = {}
        rows = []
        specs = (("sensitivity_db", "Sensibilidade", -12, 24, 0.5, " dB",
                  "Aumenta ou diminui o quanto o visual reage ao volume"),
                 ("attack", "Velocidade de resposta", 0.05, 1.0, 0.05, "",
                  "Quão rápido o visual sobe quando o som aumenta"),
                 ("smooth", "Suavidade", 0.0, 0.98, 0.02, "",
                  "Quanto maior, mais devagar o visual desce (movimento mais suave)"),
                 ("range_db", "Faixa dinâmica", 18, 72, 1, " dB",
                  "Menor = mais contraste entre partes altas e baixas"),
                 ("tilt_db", "Reforço dos agudos", 0, 9, 0.5, " dB/oit",
                  "Compensa a energia menor das frequências altas"))
        for key, label, lo, hi, step, suf, tip in specs:
            val = (1 - r.release) if key == "smooth" else getattr(r, key)
            w = SliderSpin(lo, hi, step, val, suffix=suf)
            w.setToolTip(tip)
            w.valueChanged.connect(lambda v, k=key: self.set_response(k, v))
            self.beh[key] = w
            rows.append((label, w))
        self.behavior_rows = rows

    # ------------------------------------------------------------------ fundo
    def _build_background_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.bg_fit = QComboBox()
        self.bg_fit.addItem("Preencher a tela (recorta as sobras)", "cover")
        self.bg_fit.addItem("Imagem inteira (sem cortes)", "contain")
        self.bg_fit.currentIndexChanged.connect(lambda _: self.set_bg("fit", self.bg_fit.currentData()))
        f.addRow("Enquadramento", self.bg_fit)
        self.bg_fill = QComboBox()
        self.bg_fill.addItem("Imagem desfocada", "blur")
        self.bg_fill.addItem("Cor sólida", "color")
        self.bg_fill.currentIndexChanged.connect(lambda _: self.set_bg("fill", self.bg_fill.currentData()))
        f.addRow("Preencher as sobras com", self.bg_fill)
        self.bg_color = ColorButton("#101014")
        self.bg_color.colorChanged.connect(lambda c: self.set_bg("color", c))
        f.addRow("Cor de fundo", self.bg_color)
        self.bg_fx = SliderSpin(0, 100, 1, 50, suffix=" %")
        self.bg_fx.valueChanged.connect(lambda v: self.set_bg("focus_x", v / 100))
        f.addRow("Posição horizontal", self.bg_fx)
        self.bg_fy = SliderSpin(0, 100, 1, 50, suffix=" %")
        self.bg_fy.valueChanged.connect(lambda v: self.set_bg("focus_y", v / 100))
        f.addRow("Posição vertical", self.bg_fy)
        self.bg_zoom = SliderSpin(100, 300, 1, 100, suffix=" %")
        self.bg_zoom.valueChanged.connect(lambda v: self.set_bg("zoom", v / 100))
        f.addRow("Zoom", self.bg_zoom)
        self.bg_dim = SliderSpin(0, 80, 1, 0, suffix=" %")
        self.bg_dim.setToolTip("Escurece a imagem para destacar o visualizador")
        self.bg_dim.valueChanged.connect(lambda v: self.set_bg("dim", v / 100))
        f.addRow("Escurecer imagem", self.bg_dim)
        self.bg_loop = QCheckBox("Repetir em loop até a última faixa")
        self.bg_loop.setToolTip("Desligado: o vídeo toca uma vez e o último quadro fica parado até o fim")
        self.bg_loop.setChecked(True)
        self.bg_loop.setEnabled(False)
        self.bg_loop.toggled.connect(lambda v: self.set_bg("loop_video", v))
        f.addRow("Vídeo de fundo", self.bg_loop)
        note = QLabel("A imagem nunca é deformada: em “Preencher”, as sobras são cortadas (ajuste a "
                      "posição para escolher o recorte); em “Imagem inteira”, as faixas vazias são "
                      "preenchidas. A imagem fica estática durante todo o vídeo; um vídeo de fundo toca sem som "
                      "(o áudio é só o das músicas).")
        note.setWordWrap(True)
        note.setStyleSheet("color:#999;font-size:11px")
        f.addRow(note)
        return w

    # ------------------------------------------------------------------ áudio
    def _build_audio_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.a_seq = QCheckBox("Tocar na ordem da lista (desmarcado = ordem embaralhada)")
        self.a_seq.setChecked(True)
        self.a_seq.toggled.connect(lambda v: self.set_audio("sequential", bool(v)))
        f.addRow(self.a_seq)
        self.a_shuffle = QPushButton("Embaralhar de novo")
        self.a_shuffle.clicked.connect(self.reshuffle)
        f.addRow("", self.a_shuffle)
        self.a_vol = SliderSpin(-20, 10, 0.5, 0, suffix=" dB")
        self.a_vol.setToolTip("0 dB = volume original, sem alteração")
        self.a_vol.valueChanged.connect(lambda v: self.set_audio("volume_db", v))
        f.addRow("Volume geral", self.a_vol)
        self.a_xf = SliderSpin(0, 12, 0.5, 0, suffix=" s")
        self.a_xf.setToolTip("Sobreposição entre o fim de uma música e o começo da próxima")
        self.a_xf.valueChanged.connect(lambda v: self.set_audio("crossfade", v))
        f.addRow("Crossfade entre faixas", self.a_xf)
        self.a_gap = SliderSpin(0, 10, 0.5, 0, suffix=" s")
        self.a_gap.setToolTip("Silêncio entre as faixas (não usado com crossfade)")
        self.a_gap.valueChanged.connect(lambda v: self.set_audio("gap", v))
        f.addRow("Intervalo entre faixas", self.a_gap)
        self.a_fin = SliderSpin(0, 15, 0.5, 0, suffix=" s")
        self.a_fin.valueChanged.connect(lambda v: self.set_audio("fade_in", v))
        f.addRow("Fade-in no início", self.a_fin)
        self.a_fout = SliderSpin(0, 15, 0.5, 0, suffix=" s")
        self.a_fout.valueChanged.connect(lambda v: self.set_audio("fade_out", v))
        f.addRow("Fade-out no final", self.a_fout)
        note = QLabel("Nenhum efeito é aplicado automaticamente: sem normalização ou compressão. "
                      "Com uma única música AAC/M4A e sem ajustes, o áudio original é copiado para o "
                      "vídeo sem recodificar. Nos demais casos, a trilha é montada em 32 bits e "
                      "codificada uma única vez (AAC).")
        note.setWordWrap(True)
        note.setStyleSheet("color:#999;font-size:11px")
        f.addRow(note)
        return w

    # ------------------------------------------------------------------ exportar
    def _build_export_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        self.e_preset = QComboBox()
        for k, (_, _, label) in PRESETS.items():
            self.e_preset.addItem(label, k)
        self.e_preset.currentIndexChanged.connect(lambda _: self.set_export("preset", self.e_preset.currentData()))
        f.addRow("Resolução", self.e_preset)
        self.e_fps = QComboBox()
        for k, (_, label) in FPS_CHOICES.items():
            self.e_fps.addItem(label, k)
        self.e_fps.currentIndexChanged.connect(lambda _: self.set_export("fps", self.e_fps.currentData()))
        f.addRow("Taxa de quadros", self.e_fps)
        self.e_format = QComboBox()
        for k, (label, _) in FORMATS.items():
            self.e_format.addItem(label, k)
        self.e_format.currentIndexChanged.connect(lambda _: self.set_export("format", self.e_format.currentData()))
        f.addRow("Formato", self.e_format)
        self.e_quality = QComboBox()
        for k, label in QUALITIES.items():
            self.e_quality.addItem(label, k)
        self.e_quality.currentIndexChanged.connect(lambda _: self.set_export("quality", self.e_quality.currentData()))
        f.addRow("Qualidade", self.e_quality)

        enc = QWidget()
        el = QVBoxLayout(enc)
        el.setContentsMargins(0, 0, 0, 0)
        self.e_sw = QRadioButton("CPU (software · libx264) — máxima compatibilidade")
        self.e_hw = QRadioButton("GPU (VAAPI · h264_vaapi) — codificação mais rápida")
        grp = QButtonGroup(self)
        grp.addButton(self.e_sw)
        grp.addButton(self.e_hw)
        self.e_sw.toggled.connect(lambda v: v and self.set_export("encoder", "software"))
        self.e_hw.toggled.connect(lambda v: v and self.set_export("encoder", "vaapi"))
        el.addWidget(self.e_sw)
        el.addWidget(self.e_hw)
        self.vaapi_label = QLabel("Verificando a GPU…")
        self.vaapi_label.setWordWrap(True)
        self.vaapi_label.setStyleSheet("font-size:11px")
        el.addWidget(self.vaapi_label)
        f.addRow("Codificação", enc)

        drow = QHBoxLayout()
        self.e_dir = QLineEdit()
        self.e_dir.editingFinished.connect(lambda: self.set_export("out_dir", self.e_dir.text().strip()))
        drow.addWidget(self.e_dir, 1)
        b = QPushButton("…")
        b.setFixedWidth(34)
        b.clicked.connect(self.choose_out_dir)
        drow.addWidget(b)
        dw = QWidget()
        dw.setLayout(drow)
        drow.setContentsMargins(0, 0, 0, 0)
        f.addRow("Pasta de destino", dw)
        self.e_name = QLineEdit()
        self.e_name.setPlaceholderText("nome automático (primeira música)")
        self.e_name.editingFinished.connect(lambda: self.set_export("filename", self.e_name.text().strip()))
        f.addRow("Nome do arquivo", self.e_name)
        self.e_tracklist = QCheckBox("Salvar também a lista de faixas (.txt) para a descrição do YouTube")
        self.e_tracklist.setToolTip("Cria “<nome do vídeo> - lista de faixas.txt” na mesma pasta")
        self.e_tracklist.toggled.connect(lambda v: self.set_export("save_tracklist", bool(v)))
        f.addRow("", self.e_tracklist)
        self.export_info = QLabel("")
        self.export_info.setWordWrap(True)
        self.export_info.setTextFormat(Qt.RichText)
        f.addRow(self.export_info)
        return w

    # ------------------------------------------------------------------ menu
    def _build_menu(self):
        m = self.menuBar().addMenu("&Arquivo")
        for text, key, slot in (("Novo projeto", QKeySequence.New, self.new_project),
                                ("Abrir projeto…", QKeySequence.Open, self.open_project_dialog),
                                ("Salvar projeto", QKeySequence.Save, self.save_project),
                                ("Salvar projeto como…", QKeySequence.SaveAs, self.save_project_as)):
            a = QAction(text, self)
            a.setShortcut(key)
            a.triggered.connect(slot)
            m.addAction(a)
        self.recent_menu = m.addMenu("Projetos recentes")
        self._refresh_recent()
        m.addSeparator()
        for text, slot in (("Escolher imagem ou vídeo…", self.choose_image), ("Adicionar músicas…", self.choose_audio),
                           ("Lista de faixas para o YouTube…", self.show_tracklist)):
            a = QAction(text, self)
            a.triggered.connect(slot)
            m.addAction(a)
        m.addSeparator()
        a = QAction("Sair", self)
        a.setShortcut(QKeySequence.Quit)
        a.triggered.connect(self.close)
        m.addAction(a)
        h = self.menuBar().addMenu("A&juda")
        a = QAction("Diagnóstico do sistema (FFmpeg / GPU)", self)
        a.triggered.connect(self.show_diagnostics)
        h.addAction(a)
        a = QAction("Limpar cache de áudio", self)
        a.triggered.connect(self.clear_cache)
        h.addAction(a)
        a = QAction("Sobre", self)
        a.triggered.connect(lambda: QMessageBox.about(
            self, "Wavegen", f"<b>Wavegen {__version__}</b><br>Vídeos musicais com waveform animado.<br>"
                             f"{ffmpeg_version_safe()}"))
        h.addAction(a)
        sp = QAction(self)
        sp.setShortcut(Qt.Key_Space)
        sp.setShortcutContext(Qt.WindowShortcut)
        sp.triggered.connect(self._space)
        self.addAction(sp)

    def _space(self):
        fw = QApplication.focusWidget()
        if isinstance(fw, (QLineEdit, QPlainTextEdit)):
            return
        self.toggle_play()

    # ================================================================== miniaturas
    def _make_thumbnails(self):
        fps = Fraction(30)
        feats = demo_features(fps)
        pcm = demo_pcm()
        thumbs = {}
        bg = QImage(384, 216, QImage.Format_ARGB32_Premultiplied)
        p = QPainter(bg)
        from PySide6.QtGui import QLinearGradient
        g = QLinearGradient(0, 0, 384, 216)
        g.setColorAt(0, QColor("#1d1b2e"))
        g.setColorAt(1, QColor("#0e1420"))
        p.fillRect(bg.rect(), g)
        p.end()
        n = int(3.15 * 30)
        for cls in style_list():
            pr = Project()
            pr.style = cls.id
            c = Compositor(pr, 384, 216, feats, pcm, background=bg)
            thumbs[cls.id] = QPixmap.fromImage(c.render(n))
        self.gallery.populate(style_list(), thumbs)

    # ================================================================== projeto -> UI
    def _apply_export_prefs(self):
        ex = self.project.export
        for k, v in (self.prefs.get("export") or {}).items():
            if hasattr(ex, k) and k != "filename":
                setattr(ex, k, v)
        if not Path(ex.out_dir).is_dir():
            ex.out_dir = str(Path.home() / "Videos") if (Path.home() / "Videos").is_dir() else str(Path.home())

    def _load_project_into_ui(self):
        self._loading = True
        p = self.project
        bg = p.background
        self.bg_fit.setCurrentIndex(max(0, self.bg_fit.findData(bg.fit)))
        self.bg_fill.setCurrentIndex(max(0, self.bg_fill.findData(bg.fill)))
        self.bg_color.set_color(bg.color)
        self.bg_fx.set_value(bg.focus_x * 100)
        self.bg_fy.set_value(bg.focus_y * 100)
        self.bg_zoom.set_value(bg.zoom * 100)
        self.bg_dim.set_value(bg.dim * 100)
        self.bg_loop.setChecked(bg.loop_video)
        a = p.audio
        self.a_seq.setChecked(a.sequential)
        self.a_vol.set_value(a.volume_db)
        self.a_xf.set_value(a.crossfade)
        self.a_gap.set_value(a.gap)
        self.a_fin.set_value(a.fade_in)
        self.a_fout.set_value(a.fade_out)
        r = p.response
        for k, w in self.beh.items():
            w.set_value((1 - r.release) if k == "smooth" else getattr(r, k))
        ex = p.export
        self.e_preset.setCurrentIndex(max(0, self.e_preset.findData(ex.preset)))
        self.e_fps.setCurrentIndex(max(0, self.e_fps.findData(ex.fps)))
        self.e_format.setCurrentIndex(max(0, self.e_format.findData(ex.format)))
        self.e_quality.setCurrentIndex(max(0, self.e_quality.findData(ex.quality)))
        (self.e_hw if ex.encoder == "vaapi" else self.e_sw).setChecked(True)
        self.e_dir.setText(ex.out_dir)
        self.e_name.setText(ex.filename)
        self.e_tracklist.setChecked(ex.save_tracklist)
        self.gallery.select(p.style)
        self._loading = False
        self._refresh_image_info()
        self._refresh_playlist()
        self._build_param_panel()
        self._refresh_export_info()
        self.schedule_mix(immediate=True)
        self.schedule_rebuild()

    def _build_param_panel(self):
        st = get_style(self.project.style)
        self.style_desc.setText(f"<b>{st.name}</b> — {st.description}")
        extra = {"Posição": self.position_rows, "Comportamento": self.behavior_rows}
        # widgets reaproveitados: tirar do layout anterior antes de reconstruir
        for _, w in self.position_rows + self.behavior_rows:
            w.setParent(None)
        self.param_panel.build(st.all_params(), self.project.params_for(), extra)
        self._refresh_box_controls()

    def _refresh_box_controls(self):
        b = self.project.box_for()
        for k, w in self.pos_widgets.items():
            w.blockSignals(True)
            w.set_value(getattr(b, k) * 100)
            w.blockSignals(False)

    # ================================================================== edição
    def mark_dirty(self):
        if self._loading:
            return
        self.dirty = True
        name = Path(self.project_path).name if self.project_path else "sem título"
        self.setWindowTitle(f"Wavegen — {name} *")

    def set_style(self, sid: str):
        if sid == self.project.style:
            return
        self.project.style = sid
        self._build_param_panel()
        self.mark_dirty()
        self.schedule_rebuild()

    def set_param(self, key: str, value):
        self.project.style_params.setdefault(self.project.style, {})[key] = value
        self.mark_dirty()
        self.schedule_rebuild()

    def reset_style(self):
        sid = self.project.style
        self.project.style_params.pop(sid, None)
        self.project.boxes.pop(sid, None)
        self._build_param_panel()
        self.mark_dirty()
        self.schedule_rebuild()

    def set_box_value(self, key: str, value: float):
        if self._loading:
            return
        setattr(self.project.box_for(), key, value)
        self.mark_dirty()
        self.schedule_rebuild()

    def align_box(self, mode: str):
        b = self.project.box_for()
        m = self.margin_spin.value() / 100
        w, h = b.w * b.scale, b.h * b.scale
        if mode == "left":
            b.x = m + w / 2
        elif mode == "right":
            b.x = 1 - m - w / 2
        elif mode == "hcenter":
            b.x = 0.5
        elif mode == "top":
            b.y = m + h / 2
        elif mode == "bottom":
            b.y = 1 - m - h / 2
        elif mode == "vcenter":
            b.y = 0.5
        self._refresh_box_controls()
        self.mark_dirty()
        self.schedule_rebuild()

    def _box_dragged(self, box: Box):
        self.project.boxes[self.project.style] = box
        if self.comp is not None:
            self.comp.box = box
            self._render_current(force=True, keep_box=True)

    def _box_drag_done(self):
        self._refresh_box_controls()
        self.mark_dirty()

    def set_response(self, key: str, v: float):
        r = self.project.response
        if key == "smooth":
            r.release = max(0.02, 1 - v)
        else:
            setattr(r, key, v)
        self.mark_dirty()
        self.schedule_rebuild()

    def set_bg(self, key: str, value):
        if self._loading:
            return
        setattr(self.project.background, key, value)
        self.mark_dirty()
        self._refresh_image_info()
        self.schedule_rebuild()

    def set_audio(self, key: str, value):
        if self._loading:
            return
        setattr(self.project.audio, key, value)
        self.a_shuffle.setEnabled(not self.project.audio.sequential)
        self.mark_dirty()
        self._refresh_totals()
        self.schedule_mix()

    def reshuffle(self):
        self.project.audio.shuffle_seed += 1
        self.mark_dirty()
        self._refresh_playlist()
        self.schedule_mix()

    def set_export(self, key: str, value):
        if self._loading:
            return
        old_fps = self.project.export.fps
        setattr(self.project.export, key, value)
        self.mark_dirty()
        self._refresh_export_info()
        if key == "fps" and value != old_fps:
            self.schedule_mix(immediate=True)
        if key in ("preset", "format"):
            self._refresh_image_info()
            self.schedule_rebuild()
        if key == "preset":
            self._check_vaapi_async()

    # ================================================================== imagem
    def choose_image(self):
        d = self.prefs.get("image_dir", str(Path.home() / "Pictures"))
        img = "*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff"
        vid = " ".join(f"*{e}" for e in sorted(VIDEO_EXTS))
        up = lambda s: s + " " + s.upper()  # noqa: E731
        f, _ = QFileDialog.getOpenFileName(
            self, "Escolher imagem ou vídeo de fundo", d,
            f"Imagens e vídeos ({up(img)} {up(vid)});;Imagens ({up(img)});;Vídeos ({up(vid)})")
        if f:
            self.prefs["image_dir"] = str(Path(f).parent)
            self.set_image(f)

    def set_image(self, path: str):
        if path:
            try:
                img = self._source_image(path, reload=True)
            except WavegenError as e:
                self.show_error(e)
                return
            self.log_line(f"{'Vídeo' if is_video(path) else 'Imagem'}: {path} "
                          f"({img.width()}×{img.height()})")
        self.project.background.image = path
        self.mark_dirty()
        self._refresh_image_info()
        self.schedule_rebuild()

    def _source_image(self, path: str, reload: bool = False) -> QImage:
        st = Path(path).stat() if Path(path).exists() else None
        key = (path, st.st_mtime_ns if st else 0)
        if reload or key not in self._src_cache:
            self._src_cache = {key: load_image(path)}
        return self._src_cache[key]

    def _refresh_image_info(self):
        path = self.project.background.image
        self.btn_img_clear.setEnabled(bool(path))
        if not path:
            self.img_thumb.setPixmap(QPixmap())
            self.img_thumb.setText("Nenhum fundo\n(escolha ou arraste uma imagem ou vídeo para a janela)")
            self.img_info.setText("Sem imagem: o fundo usa a cor escolhida na aba “Fundo”.")
            return
        try:
            img = self._source_image(path)
        except WavegenError as e:
            self.img_thumb.setText("Imagem indisponível")
            self.img_info.setText(f"<span style='color:#ff8a80'>{e.message}</span>")
            return
        pm = QPixmap.fromImage(img).scaled(300, 130, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.img_thumb.setPixmap(pm)
        W, H = resolution(self.project.export.preset)
        msgs = [f"<b>{Path(path).name}</b> — {img.width()}×{img.height()}"]
        self.bg_loop.setEnabled(is_video(path))
        if is_video(path):
            try:
                vi = probe_video(path)
                total = self.feats.duration if self.feats is not None and not self.using_demo else 0
                msgs[0] += f" · vídeo de {fmt_time(vi.duration)}"
                if total and vi.duration + 0.05 < total:
                    msgs.append("O vídeo é mais curto que as músicas: "
                                + ("vai repetir em loop até a última faixa terminar."
                                   if self.project.background.loop_video else
                                   "o último quadro fica parado até o fim (ative o loop na aba Fundo)."))
                elif total and vi.duration > total + 0.05:
                    msgs.append("O vídeo é mais longo que as músicas: será cortado no fim da última faixa.")
                if vi.has_audio:
                    msgs.append("O som do vídeo não é usado (só as músicas da playlist).")
            except WavegenError as e:
                msgs.append(f"<span style='color:#ff8a80'>{e.message}</span>")
        iar, var = img.width() / img.height(), W / H
        if is_transparent(self.project.export.format):
            msgs.append("<span style='color:#ffd180'>O formato transparente ignora a imagem de fundo.</span>")
        else:
            if self.project.background.fit == "cover" and abs(iar - var) / var > 0.02:
                cut = (1 - min(iar, var) / max(iar, var)) * 100
                side = "nas laterais" if iar > var else "em cima/embaixo"
                msgs.append(f"Proporção diferente do vídeo: ~{cut:.0f}% da imagem será cortada {side} "
                            f"(ajuste a posição na aba Fundo).")
            need = max(W / img.width(), H / img.height()) if self.project.background.fit == "cover" \
                else min(W / img.width(), H / img.height())
            need *= self.project.background.zoom
            if need > 1.05:
                msgs.append(f"<span style='color:#ffd180'>A imagem é menor que o vídeo ({W}×{H}) e será "
                            f"ampliada {need:.1f}× — pode perder nitidez.</span>")
        self.img_info.setText("<br>".join(msgs))

    # ================================================================== músicas
    def choose_audio(self):
        d = self.prefs.get("audio_dir", str(Path.home() / "Music"))
        exts = " ".join(f"*{e} *{e.upper()}" for e in sorted(AUDIO_EXTS))
        files, _ = QFileDialog.getOpenFileNames(self, "Adicionar músicas", d, f"Áudio ({exts});;Todos (*)")
        if files:
            self.prefs["audio_dir"] = str(Path(files[0]).parent)
            self.add_files(files)

    def add_files(self, files: list[str]):
        visual = IMAGE_EXTS | VIDEO_EXTS
        images = [f for f in files if Path(f).suffix.lower() in visual]
        audios = [f for f in files if Path(f).suffix.lower() not in visual]
        if images:
            self.set_image(images[0])
        if not audios:
            return
        self.status.setText(f"Lendo {len(audios)} arquivo(s) de áudio…")

        def work(progress, cancel):
            ok, bad = [], []
            for i, f in enumerate(audios):
                if Path(f).is_dir():
                    continue
                try:
                    ok.append(Track.load(f))
                except WavegenError as e:
                    bad.append((f, e.message))
                progress((i + 1) / len(audios))
            return ok, bad

        t = Task(work, self)
        t.finished.connect(self._files_loaded)
        t.failed.connect(self.show_error)
        t.start()
        self._add_task = t

    def _files_loaded(self, res):
        ok, bad = res
        self.project.tracks.extend(ok)
        if ok:
            self.mark_dirty()
            self._refresh_playlist()
            self.schedule_mix(immediate=True)
            self.status.setText(f"{len(ok)} música(s) adicionada(s).")
            for t in ok:
                self.log_line(f"Música: {t.path} ({t.codec}, {fmt_time(t.duration)})")
        if bad:
            QMessageBox.warning(self, "Alguns arquivos não foram adicionados",
                                "Estes arquivos não puderam ser lidos como áudio:\n\n" +
                                "\n".join(f"• {Path(f).name}: {m}" for f, m in bad))

    def _selected_rows(self) -> list[int]:
        return sorted({self.playlist.indexOfTopLevelItem(i) for i in self.playlist.selectedItems()})

    def remove_tracks(self):
        rows = self._selected_rows()
        if not rows:
            return
        for r in reversed(rows):
            del self.project.tracks[r]
        self.mark_dirty()
        self._refresh_playlist()
        self.schedule_mix(immediate=True)

    def move_tracks(self, delta: int):
        rows = self._selected_rows()
        if not rows:
            return
        tr = self.project.tracks
        if delta < 0 and rows[0] == 0 or delta > 0 and rows[-1] == len(tr) - 1:
            return
        for r in (rows if delta < 0 else reversed(rows)):
            tr[r], tr[r + delta] = tr[r + delta], tr[r]
        self.mark_dirty()
        self._refresh_playlist([r + delta for r in rows])
        self.schedule_mix()

    def _sync_order_from_widget(self):
        new = []
        for i in range(self.playlist.topLevelItemCount()):
            new.append(self.playlist.topLevelItem(i).data(0, Qt.UserRole))
        if [t.path for t in new] != [t.path for t in self.project.tracks]:
            self.project.tracks = new
            self.mark_dirty()
            self.schedule_mix()
        self._refresh_playlist([self.playlist.indexOfTopLevelItem(i) for i in self.playlist.selectedItems()])

    def _refresh_playlist(self, select: list[int] | None = None):
        self.playlist.clear()
        for i, t in enumerate(self.project.tracks):
            it = QTreeWidgetItem([str(i + 1), t.name, fmt_time(t.duration)])
            it.setData(0, Qt.UserRole, t)
            it.setToolTip(1, t.path)
            it.setFlags(it.flags() & ~Qt.ItemIsDropEnabled)
            if not t.exists():
                it.setForeground(1, QColor("#ff8a80"))
                it.setToolTip(1, f"Arquivo não encontrado: {t.path}")
            self.playlist.addTopLevelItem(it)
        for r in select or []:
            if 0 <= r < self.playlist.topLevelItemCount():
                self.playlist.topLevelItem(r).setSelected(True)
        self.a_shuffle.setEnabled(not self.project.audio.sequential)
        self._refresh_totals()

    def _refresh_totals(self):
        tr = self.project.tracks
        if not tr:
            self.total_label.setText("Duração total: — (adicione músicas)")
            self.btn_tracklist.setEnabled(False)
        else:
            d = total_duration([t.duration for t in ordered(tr, self.project.audio)], self.project.audio)
            self.total_label.setText(f"{len(tr)} música(s) · duração total: {fmt_time(d)}")
            self.btn_tracklist.setEnabled(True)
        self._refresh_export_info()

    # ================================================================== trilha + análise
    def schedule_mix(self, immediate: bool = False):
        self.mix_timer.start(0 if immediate else 350)

    def start_mix(self):
        if self.mix_task is not None:
            self.mix_task.stop()
            self.mix_task = None
        fps = fps_of(self.project.export.fps)
        tracks = [Track(t.path, t.duration, t.codec, t.title) for t in self.project.tracks]
        if not tracks or any(not t.exists() for t in tracks):
            self._use_demo(fps, "Pré-visualização com áudio de demonstração — adicione músicas." if not tracks
                           else "Há músicas não encontradas — usando áudio de demonstração.")
            return
        from dataclasses import replace
        audio = replace(self.project.audio)
        self.mix_status = "Preparando a trilha…"
        self.preview.set_message(self.mix_status)

        def work(progress, cancel):
            mix = ensure_mix(tracks, audio, lambda f: progress(f * 0.5), cancel)
            feats = ensure_features(mix, fps, lambda f: progress(0.5 + f * 0.5), cancel)
            pcm, sr = open_pcm(mix)
            return feats, pcm, sr, mix

        t = Task(work, self)
        t.progress.connect(lambda f: self.preview.set_message(f"Preparando a trilha e analisando o áudio… {f * 100:.0f}%"))
        t.finished.connect(self._mix_ready)
        t.failed.connect(self._mix_failed)
        self.mix_task = t.start()

    def _use_demo(self, fps, msg):
        self._set_audio_source(demo_features(fps), demo_pcm(), DEMO_SR, demo=True)
        self.preview.set_message(msg)

    def _mix_ready(self, res):
        feats, pcm, sr, mix = res
        self.mix_task = None
        self.log_line(f"Trilha pronta: {mix} ({fmt_time(feats.duration)}, {feats.n_frames} quadros)")
        self._set_audio_source(feats, pcm, sr, demo=False)
        self.preview.set_message("")

    def _mix_failed(self, e):
        self.mix_task = None
        if getattr(e, "code", "") == "cancelled":
            return
        self.show_error(e)
        self._use_demo(fps_of(self.project.export.fps), "Erro ao preparar a trilha — veja os detalhes.")

    def _set_audio_source(self, feats, pcm, sr, demo: bool):
        t_sec = self.player.position() / max(self.sr, 1)
        was_playing = self.player.playing
        self.player.pause()
        self.feats, self.pcm, self.sr, self.using_demo = feats, pcm, sr, demo
        self.player.set_source(pcm, sr)
        self.player.seek(int(min(t_sec, feats.duration) * sr))
        self.time_slider.setRange(0, max(0, feats.n_frames - 1))
        self.rebuild_preview()
        if was_playing:
            self.toggle_play()

    # ================================================================== preview
    def schedule_rebuild(self):
        self.rebuild_timer.start()

    def preview_size(self) -> tuple[int, int]:
        W, H = resolution(self.project.export.preset)
        # desenha o preview na resolução real do painel (sem esticar a imagem), limitado ao
        # tamanho do vídeo final e a 1920 px para manter a reprodução leve
        long_px = PREVIEW_LONG_SIDE
        pw = self.preview.pixel_width() if hasattr(self, "preview") else 0
        if pw > 0:
            fw = pw if W >= H else pw * H / W
            long_px = max(long_px, min(int(fw), PREVIEW_MAX_SIDE))
        long_px = min(long_px, max(W, H))
        s = long_px / max(W, H)
        return max(2, int(round(W * s / 2)) * 2), max(2, int(round(H * s / 2)) * 2)

    def _background(self, W, H) -> QImage:
        bg = self.project.background
        transparent = is_transparent(self.project.export.format)
        key = (bg.image, bg.fit, bg.focus_x, bg.focus_y, bg.zoom, bg.fill, bg.color, bg.dim, W, H, transparent)
        if key not in self._bg_cache:
            src = None
            if bg.image and not transparent:
                try:
                    src = self._source_image(bg.image)
                except WavegenError:
                    src = None
            self._bg_cache = {key: compose_background(src, W, H, bg, transparent)}
        return self._bg_cache[key]

    def _preview_resized(self):
        if self.comp is not None and (self.comp.W, self.comp.H) != self.preview_size():
            self.schedule_rebuild()

    def rebuild_preview(self):
        if self.feats is None:
            return
        W, H = self.preview_size()
        transparent = is_transparent(self.project.export.format)
        provider = None
        bg = self.project.background
        if self._video is not None:
            self._video.close()
            self._video = None
        if bg.image and is_video(bg.image) and not transparent:
            try:
                self._video = VideoDecoder(bg, W, H, self.feats.fps)
                dec = self._video
                provider = lambda n: dec.frame(n, 0.05 if self.player.playing else 3.0)  # noqa: E731
            except WavegenError as e:
                self.log_line(f"Vídeo de fundo: {e.message}")
        try:
            self.comp = Compositor(self.project, W, H, self.feats, self.pcm,
                                   transparent=transparent,
                                   background=self._background(W, H), bg_provider=provider)
        except Exception as e:  # noqa: BLE001
            self.log_line(f"Erro no preview: {e}")
            return
        fw, fh = resolution(self.project.export.preset)
        self.preview_note.setText(
            f"Pré-visualização em {W}×{H} (o vídeo final será {fw}×{fh} com o mesmo enquadramento)"
            + (" · áudio de demonstração" if self.using_demo else ""))
        self._render_current(force=True)

    def _render_current(self, force=False, keep_box=False):
        if self.comp is None or self.feats is None:
            return
        n = int(self.player.position() / self.sr * float(self.feats.fps))
        n = min(max(n, 0), self.feats.n_frames - 1)
        if n == self.cur_frame and not force:
            return
        self.cur_frame = n
        img = self.comp.render(n)
        self.preview.set_frame(img, self.project.box_for() if not keep_box else self.comp.box)
        if not self.time_slider.isSliderDown():
            self.time_slider.blockSignals(True)
            self.time_slider.setValue(n)
            self.time_slider.blockSignals(False)
        self.time_label.setText(f"{fmt_time(n / float(self.feats.fps))} / {fmt_time(self.feats.duration)}")

    def toggle_play(self):
        if self.player.playing:
            self.player.pause()
            self.play_timer.stop()
            self.btn_play.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        else:
            self.player.play()
            if self.player.error:
                self.log_line(self.player.error)
            self.play_timer.start()
            self.btn_play.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))

    def _tick(self):
        self._render_current()
        if not self.player.playing:
            self.player.pause()              # fecha a saída de áudio que chegou ao fim
            self.play_timer.stop()
            self.btn_play.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))

    def _seek_slider(self, n: int):
        if self.feats is None:
            return
        self.player.seek(int(n / float(self.feats.fps) * self.sr))
        self._render_current(force=True)

    def _preview_volume(self, v: int):
        self.player.volume = (v / 100) ** 2
        self.prefs["preview_volume"] = v

    def _toggle_guides(self, on: bool):
        self.preview.show_guides = on
        self.preview.update()

    # ================================================================== exportação
    def _out_path(self) -> Path:
        ex = self.project.export
        name = ex.filename.strip() or default_filename(self.project.tracks, ex.format)
        name = name.replace("/", "-").replace("\0", "")
        if not name.lower().endswith(extension(ex.format)):
            stem = name.rsplit(".", 1)[0] if name.lower().endswith((".mp4", ".mov", ".mkv")) else name
            name = stem + extension(ex.format)
        return Path(os.path.expanduser(ex.out_dir or str(Path.home()))) / name

    def _refresh_export_info(self):
        if not hasattr(self, "export_info"):
            return
        ex = self.project.export
        W, H = resolution(ex.preset)
        fps = fps_of(ex.fps)
        tr = self.project.tracks
        d = total_duration([t.duration for t in ordered(tr, self.project.audio)], self.project.audio) if tr else 0
        mp4 = ex.format == "mp4"
        self.e_sw.setEnabled(mp4)
        self.e_hw.setEnabled(mp4 and bool(self.vaapi_ok))
        lines = [f"Arquivo final: <b>{self._out_path().name}</b>",
                 f"{W}×{H} · {float(fps):.3g} fps · duração {fmt_time(d) if d else '—'}"]
        if d:
            lo, hi = estimate_size(ex.format, ex.quality, ex.encoder, W, H, fps, d)
            lines.append(f"Tamanho estimado: <b>{fmt_size(lo)} – {fmt_size(hi)}</b> "
                         f"<i>(estimativa: varia com a imagem e o estilo)</i>")
            fr = d * float(fps)
            lines.append(f"Quadros a renderizar: {int(fr):,}".replace(",", "."))
        if not mp4:
            lines.append("<span style='color:#ffd180'>Formato transparente: sem imagem de fundo, para "
                         "sobrepor no DaVinci Resolve. Arquivos grandes; áudio em PCM.</span>")
        if ex.preset == "4k":
            lines.append("4K: a renderização leva cerca de 3–4× mais tempo que em 1080p.")
        if float(fps) > 30:
            lines.append("60 fps: movimento mais fluido, mas o dobro de quadros para renderizar.")
        self.export_info.setText("<br>".join(lines))

    def choose_out_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Pasta de destino", self.project.export.out_dir)
        if d:
            self.e_dir.setText(d)
            self.set_export("out_dir", d)

    # ================================================================== ambiente
    def _check_environment(self):
        try:
            ffmpeg_path()
            self.log_line(ffmpeg_version())
            missing = [e for e in ("libx264", "aac") if e not in encoders()]
            if missing:
                self.log_line(f"Aviso: encoders ausentes no FFmpeg: {', '.join(missing)}")
        except WavegenError as e:
            self.btn_render.setEnabled(False)
            self.btn_sample.setEnabled(False)
            QTimer.singleShot(200, lambda: QMessageBox.critical(self, "FFmpeg não encontrado", e.message))
            return
        self._check_vaapi_async()

    def _check_vaapi_async(self):
        W, H = resolution(self.project.export.preset)
        self.vaapi_label.setText("Verificando a GPU (VAAPI)…")

        def work(progress, cancel):
            return check_vaapi("h264_vaapi", W, H)

        t = Task(work, self)
        t.finished.connect(self._vaapi_result)
        t.start()
        self._vaapi_task = t

    def _vaapi_result(self, st):
        self.vaapi_ok = st.available
        if st.available:
            self.vaapi_label.setText(
                "<span style='color:#69f0ae'>✔ GPU disponível.</span> A GPU acelera só a "
                "<b>codificação</b> do vídeo; o desenho do waveform continua na CPU.")
        else:
            self.vaapi_label.setText(f"<span style='color:#ffab91'>✖ GPU indisponível:</span> {st.message}")
            if self.project.export.encoder == "vaapi":
                self.e_sw.setChecked(True)
        if st.details:
            self.log_line("VAAPI: " + st.details[-800:])
        self.log_line("VAAPI: " + st.message)
        self._refresh_export_info()

    def show_diagnostics(self):
        lines = [f"Wavegen {__version__}", f"Python {sys.version.split()[0]}"]
        try:
            lines.append(ffmpeg_version())
            enc = encoders()
            lines.append("Encoders: " + ", ".join(e for e in ("libx264", "aac", "h264_vaapi", "hevc_vaapi",
                                                                "prores_ks", "png") if e in enc))
        except WavegenError as e:
            lines.append(e.message)
        for W, H in ((1920, 1080), (3840, 2160)):
            st = check_vaapi("h264_vaapi", W, H)
            lines.append(f"VAAPI {W}×{H}: {'OK' if st.available else 'indisponível'} — {st.message}")
        lines.append(f"Áudio do preview: {self.player.error or 'OK'}")
        lines.append(f"Processadores: {os.cpu_count()}")
        lines.append(f"Cache: {CACHE_DIR}")
        txt = "\n".join(lines)
        self.log_line(txt)
        box = QMessageBox(self)
        box.setWindowTitle("Diagnóstico do sistema")
        box.setText(txt)
        box.exec()

    def clear_cache(self):
        import shutil
        mixdir = CACHE_DIR / "mix"
        size = sum(f.stat().st_size for f in mixdir.glob("*") if f.is_file()) if mixdir.exists() else 0
        if QMessageBox.question(self, "Limpar cache", f"Apagar {fmt_size(size)} de trilhas e análises em cache?\n"
                                "(Elas serão recriadas quando necessário.)") == QMessageBox.Yes:
            shutil.rmtree(mixdir, ignore_errors=True)
            self.schedule_mix(immediate=True)

    # ================================================================== renderização
    def start_render(self, sample: bool = False):
        if self.render_proc is not None:
            return
        if not self.project.tracks:
            QMessageBox.information(self, "Sem músicas", "Adicione pelo menos uma música antes de renderizar.")
            return
        if not self._check_missing():
            return
        ex = self.project.export
        out = self._out_path()
        if sample:
            out = out.with_name(out.stem + " (teste 15s)" + out.suffix)
        if not out.parent.is_dir():
            QMessageBox.warning(self, "Pasta inválida", f"A pasta de destino não existe:\n{out.parent}")
            return
        if out.exists() and not sample:
            if QMessageBox.question(self, "Substituir arquivo?",
                                    f"“{out.name}” já existe nesta pasta. Substituir?") != QMessageBox.Yes:
                return
        if ex.encoder == "vaapi" and ex.format == "mp4" and not self.vaapi_ok:
            if QMessageBox.question(self, "GPU indisponível",
                                    "A codificação pela GPU não está disponível. Renderizar pela CPU?") != QMessageBox.Yes:
                return
            self.e_sw.setChecked(True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        pj = CACHE_DIR / "render-project.json"
        self.project.save(pj)
        args = ["-m", "wavegen.job", "--project", str(pj), "--out", str(out)]
        if sample:
            t0 = self.cur_frame / float(self.feats.fps) if self.feats is not None and not self.using_demo else 0.0
            args += ["--sample", f"{t0:.3f}", "15"]
        self.render_out = out
        self.render_sample = sample
        self.render_buf = b""
        self.render_error = None
        self.render_result = None
        p = QProcess(self)
        p.setProgram(sys.executable)
        p.setArguments(args)
        p.setProcessChannelMode(QProcess.SeparateChannels)
        p.readyReadStandardOutput.connect(self._render_stdout)
        p.readyReadStandardError.connect(lambda: self.log_line(
            bytes(p.readAllStandardError()).decode("utf-8", "replace").rstrip()))
        p.finished.connect(self._render_finished)
        p.errorOccurred.connect(lambda err: self.log_line(f"Processo de renderização: erro {err}"))
        self.render_proc = p
        self.log_line(f"Renderização iniciada: {out}")
        self.btn_render.setEnabled(False)
        self.btn_sample.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.btn_open_out.setEnabled(False)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        self.status.setText("Iniciando…")
        self.render_t0 = time.monotonic()
        self.render_eta = None
        self.elapsed_timer.start()
        p.start()

    def _render_stdout(self):
        self.render_buf += bytes(self.render_proc.readAllStandardOutput())
        *lines, self.render_buf = self.render_buf.split(b"\n")
        for raw in lines:
            if not raw.strip():
                continue
            try:
                ev = json.loads(raw)
            except ValueError:
                self.log_line(raw.decode("utf-8", "replace"))
                continue
            t = ev.get("type")
            if t == "stage":
                self.status.setText(ev.get("message", ""))
                self.log_line(ev.get("message", ""))
            elif t == "progress":
                stage = ev.get("stage")
                f = float(ev.get("fraction") or 0)
                if stage == "render":
                    self.progress.setValue(int(50 + f * 950))
                    self.render_eta = ev.get("eta")
                    self.status.setText(f"Renderizando… quadro {ev.get('done')} de {ev.get('total')} "
                                        f"({ev.get('fps')} quadros/s, {ev.get('workers')} processos)")
                elif stage == "mix":
                    self.progress.setValue(int(f * 25))
                elif stage == "analysis":
                    self.progress.setValue(int(25 + f * 25))
            elif t == "done":
                self.render_result = ev
            elif t == "error":
                self.render_error = ev

    def _update_elapsed(self):
        el = time.monotonic() - self.render_t0
        txt = f"Decorrido {fmt_time(el)}"
        if self.render_eta is not None:
            txt += f" · restam ~{fmt_time(self.render_eta)}"
        self.elapsed_label.setText(txt)

    def cancel_render(self):
        if self.render_proc is None:
            return
        self.status.setText("Cancelando…")
        self.btn_cancel.setEnabled(False)
        self.render_proc.terminate()
        QTimer.singleShot(6000, lambda: self.render_proc is not None and self.render_proc.kill())

    def _render_finished(self, code, _status):
        self._render_stdout()
        self.elapsed_timer.stop()
        self._update_elapsed()
        self.render_proc = None
        self.btn_render.setEnabled(True)
        self.btn_sample.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        res, err = self.render_result, self.render_error
        if res and code == 0 and Path(res["path"]).exists():
            self.progress.setValue(1000)
            self.last_out = res["path"]
            self.btn_open_out.setEnabled(True)
            msg = (f"Concluído: {Path(res['path']).name} — {fmt_size(res['size'])}, "
                   f"{fmt_time(res['duration'])}, em {fmt_time(res['elapsed'])} ({res['encoder']})")
            self.status.setText(msg)
            self.log_line(msg + f" · áudio {res.get('audio')}")
            box = QMessageBox(self)
            box.setWindowTitle("Vídeo pronto")
            box.setIcon(QMessageBox.Information)
            box.setText(f"<b>{Path(res['path']).name}</b><br>{fmt_size(res['size'])} · "
                        f"{fmt_time(res['duration'])} · renderizado em {fmt_time(res['elapsed'])}<br>"
                        f"Codificação: {res['encoder']} · áudio {res.get('audio')}<br><br>"
                        "O arquivo foi verificado (vídeo + áudio, duração correta)."
                        + (f"<br><br>Lista de faixas salva em:<br><i>{Path(res['tracklist']).name}</i>"
                           if res.get("tracklist") else ""))
            b_play = box.addButton("Reproduzir", QMessageBox.ActionRole)
            b_dir = box.addButton("Abrir pasta", QMessageBox.ActionRole)
            box.addButton("Fechar", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is b_play:
                QDesktopServices.openUrl(QUrl.fromLocalFile(res["path"]))
            elif box.clickedButton() is b_dir:
                self.open_output_folder()
            return
        self.progress.setValue(0)
        self.progress.setFormat("")
        if err and err.get("code") == "cancelled":
            self.status.setText("Renderização cancelada. Nenhum arquivo parcial foi mantido.")
            return
        if err is None:
            err = {"code": "crash", "message": f"O processo de renderização terminou inesperadamente (código {code}).",
                   "details": ""}
        self.status.setText("Falha na renderização: " + err.get("message", ""))
        if err.get("details"):
            self.log_line(err["details"])
        if err.get("code") in ("vaapi_failed", "vaapi_unavailable"):
            if QMessageBox.question(self, "Falha na GPU",
                                    err["message"] + "\n\nRenderizar novamente usando a CPU (software)?"
                                    ) == QMessageBox.Yes:
                self.e_sw.setChecked(True)
                self.start_render(self.render_sample)
            return
        self.show_error(WavegenError(err.get("message", "Erro"), err.get("details", ""), err.get("code", "")))

    def show_tracklist(self):
        if not self.project.tracks:
            QMessageBox.information(self, "Sem músicas", "Adicione músicas para gerar a lista de faixas.")
            return
        from .tracklist_dialog import TracklistDialog
        dlg = TracklistDialog(self.project, self.project.export.out_dir, self)
        dlg.changed.connect(self.mark_dirty)
        dlg.exec()

    def open_output_folder(self):
        target = Path(self.last_out).parent if self.last_out else Path(self.project.export.out_dir)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    # ================================================================== projetos
    def _check_missing(self) -> bool:
        missing = self.project.missing_files()
        if not missing:
            return True
        return self.relocate_missing(missing)

    def relocate_missing(self, missing: list[str]) -> bool:
        txt = "Estes arquivos não foram encontrados (foram movidos ou apagados?):\n\n" + \
              "\n".join(f"• {m}" for m in missing) + "\n\nDeseja localizá-los agora?"
        if QMessageBox.question(self, "Arquivos não encontrados", txt) != QMessageBox.Yes:
            return False
        for old in missing:
            if Path(old).exists():
                continue
            is_img = old == self.project.background.image
            f, _ = QFileDialog.getOpenFileName(self, f"Localizar “{Path(old).name}”",
                                               self.prefs.get("audio_dir", str(Path.home())))
            if not f:
                return False
            newdir = Path(f).parent
            if is_img:
                self.project.background.image = f
            for sid, key, val in self.project.image_params():
                if val == old:
                    self.project.style_params[sid][key] = f
            for t in self.project.tracks:
                if t.path == old:
                    t.path = f
            # tenta achar os outros faltantes na mesma pasta nova
            for t in self.project.tracks:
                cand = newdir / Path(t.path).name
                if not Path(t.path).exists() and cand.exists():
                    t.path = str(cand)
            cand = newdir / Path(self.project.background.image).name
            if self.project.background.image and not Path(self.project.background.image).exists() and cand.exists():
                self.project.background.image = str(cand)
        self.mark_dirty()
        self._load_project_into_ui()
        return not self.project.missing_files()

    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        r = QMessageBox.question(self, "Alterações não salvas", "Salvar as alterações do projeto atual?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save_project()
        return r == QMessageBox.Discard

    def new_project(self):
        if not self._confirm_discard():
            return
        self.project = Project()
        self._apply_export_prefs()
        self.project_path = None
        self.dirty = False
        self.setWindowTitle("Wavegen — sem título")
        self._load_project_into_ui()

    def open_project_dialog(self):
        if not self._confirm_discard():
            return
        f, _ = QFileDialog.getOpenFileName(self, "Abrir projeto", self.prefs.get("project_dir", str(Path.home())),
                                           "Projeto Wavegen (*.wavegen.json *.json)")
        if f:
            self.open_project(f)

    def open_project(self, path: str):
        try:
            pr = Project.load(path)
        except (OSError, ValueError, KeyError, TypeError) as e:
            QMessageBox.critical(self, "Não foi possível abrir", f"O projeto não pôde ser lido:\n{e}")
            return
        self.project = pr
        self.project_path = path
        self.prefs["project_dir"] = str(Path(path).parent)
        self._add_recent(path)
        self.dirty = False
        self.setWindowTitle(f"Wavegen — {Path(path).name}")
        self._load_project_into_ui()
        missing = pr.missing_files()
        if missing:
            QTimer.singleShot(100, lambda: self.relocate_missing(missing))

    def save_project(self) -> bool:
        if not self.project_path:
            return self.save_project_as()
        try:
            self.project.save(self.project_path)
        except OSError as e:
            QMessageBox.critical(self, "Erro ao salvar", f"Não foi possível salvar o projeto:\n{e}")
            return False
        self.dirty = False
        self.setWindowTitle(f"Wavegen — {Path(self.project_path).name}")
        self.status.setText(f"Projeto salvo: {self.project_path}")
        self._add_recent(self.project_path)
        return True

    def save_project_as(self) -> bool:
        d = self.prefs.get("project_dir", str(Path.home()))
        name = (Path(self.project.tracks[0].path).stem if self.project.tracks else "projeto") + ".wavegen.json"
        f, _ = QFileDialog.getSaveFileName(self, "Salvar projeto", str(Path(d) / name),
                                           "Projeto Wavegen (*.wavegen.json)")
        if not f:
            return False
        if not f.endswith(".json"):
            f += ".wavegen.json"
        self.project_path = f
        self.prefs["project_dir"] = str(Path(f).parent)
        return self.save_project()

    def _add_recent(self, path: str):
        rec = [p for p in self.prefs.get("recent", []) if p != path]
        self.prefs["recent"] = ([path] + rec)[:8]
        self._refresh_recent()

    def _refresh_recent(self):
        self.recent_menu.clear()
        rec = self.prefs.get("recent", [])
        for p in rec:
            a = QAction(p, self)
            a.triggered.connect(lambda _=False, x=p: self._confirm_discard() and self.open_project(x))
            self.recent_menu.addAction(a)
        self.recent_menu.setEnabled(bool(rec))

    # ================================================================== utilidades
    def log_line(self, text: str):
        if text:
            self.log.appendPlainText(time.strftime("[%H:%M:%S] ") + text)

    def show_error(self, e):
        msg = getattr(e, "message", None) or str(e) or e.__class__.__name__
        details = getattr(e, "details", "")
        if details:
            self.log_line(details)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Wavegen")
        box.setText(msg)
        if details:
            box.setDetailedText(details)
        box.exec()

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        files = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        projects = [f for f in files if f.endswith(".wavegen.json")]
        if projects:
            if self._confirm_discard():
                self.open_project(projects[0])
            return
        self.add_files(files)

    def closeEvent(self, e):
        if self.render_proc is not None:
            if QMessageBox.question(self, "Renderização em andamento",
                                    "Uma renderização está em andamento. Cancelar e sair?") != QMessageBox.Yes:
                e.ignore()
                return
            self.render_proc.terminate()
            self.render_proc.waitForFinished(6000)
        if not self._confirm_discard():
            e.ignore()
            return
        self.player.close()
        if self._video is not None:
            self._video.close()
        if self.mix_task is not None:
            self.mix_task.stop()
        ex = self.project.export
        self.prefs["export"] = {k: getattr(ex, k) for k in ("preset", "fps", "quality", "encoder",
                                                            "format", "out_dir")}
        self.prefs["geometry"] = bytes(self.saveGeometry().toHex()).decode()
        try:
            save_prefs(self.prefs)
        except OSError:
            pass
        e.accept()


def ffmpeg_version_safe() -> str:
    try:
        return ffmpeg_version()
    except WavegenError:
        return "FFmpeg não encontrado"


def setup_theme(app):
    app.setStyle("Fusion")
    from PySide6.QtGui import QPalette
    pal = QPalette()
    for role, c in ((QPalette.Window, "#1e1f24"), (QPalette.WindowText, "#e8e8ec"), (QPalette.Base, "#16171b"),
                    (QPalette.AlternateBase, "#1c1d22"), (QPalette.ToolTipBase, "#2a2b31"),
                    (QPalette.ToolTipText, "#f0f0f0"), (QPalette.Text, "#e8e8ec"), (QPalette.Button, "#2a2b31"),
                    (QPalette.ButtonText, "#e8e8ec"), (QPalette.Highlight, "#3d8bfd"),
                    (QPalette.HighlightedText, "#ffffff"), (QPalette.PlaceholderText, "#8a8a92")):
        pal.setColor(role, QColor(c))
    pal.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#6a6a72"))
    pal.setColor(QPalette.Disabled, QPalette.Text, QColor("#6a6a72"))
    pal.setColor(QPalette.Disabled, QPalette.WindowText, QColor("#6a6a72"))
    app.setPalette(pal)


def main(argv=None):
    app = QApplication.instance() or QApplication(sys.argv if argv is None else argv)
    app.setApplicationName("Wavegen")
    app.setOrganizationName("wavegen")
    app.setDesktopFileName("wavegen")
    app.setWindowIcon(QIcon(str(Path(__file__).with_name("wavegen.svg"))))
    setup_theme(app)
    args = [a for a in (sys.argv[1:] if argv is None else argv[1:]) if not a.startswith("-")]
    w = MainWindow(args[0] if args else None)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
