"""Janela "Lista de faixas para o YouTube": tempos de cada música no vídeo, títulos editáveis,
copiar para a área de transferência ou salvar em .txt."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog,
                               QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPlainTextEdit,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..tracklist import entries, format_ts, render_text, youtube_warnings


class TracklistDialog(QDialog):
    changed = Signal()          # títulos ou modo de marcação alterados (projeto modificado)

    def __init__(self, project, default_dir: str, parent=None):
        super().__init__(parent)
        self.project = project
        self.default_dir = default_dir
        self.setWindowTitle("Lista de faixas para a descrição do YouTube")
        self.resize(720, 640)
        lay = QVBoxLayout(self)

        info = QLabel("Tempo em que cada música começa no vídeo, na ordem final. Dê dois cliques num "
                      "título para editá-lo (vem das tags da música ou do nome do arquivo).")
        info.setWordWrap(True)
        lay.addWidget(info)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["#", "Início", "Título"])
        self.table.verticalHeader().hide()
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.itemChanged.connect(self._title_edited)
        lay.addWidget(self.table, 2)

        opts = QHBoxLayout()
        opts.addWidget(QLabel("Com crossfade, marcar a troca no:"))
        self.mark = QComboBox()
        self.mark.addItem("início da transição", "start")
        self.mark.addItem("meio da transição", "middle")
        self.mark.setCurrentIndex(max(0, self.mark.findData(project.export.tracklist_mark)))
        self.mark.currentIndexChanged.connect(self._mark_changed)
        opts.addWidget(self.mark)
        self.numbered = QCheckBox("Numerar (01., 02., …)")
        self.numbered.toggled.connect(self._refresh_text)
        opts.addWidget(self.numbered)
        opts.addStretch(1)
        lay.addLayout(opts)

        lay.addWidget(QLabel("Texto para colar na descrição:"))
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setStyleSheet("font-family:monospace")
        lay.addWidget(self.text, 2)
        self.warn = QLabel("")
        self.warn.setWordWrap(True)
        lay.addWidget(self.warn)

        btns = QHBoxLayout()
        self.btn_copy = QPushButton("Copiar")
        self.btn_copy.setDefault(True)
        self.btn_copy.clicked.connect(self.copy)
        btns.addWidget(self.btn_copy)
        b = QPushButton("Salvar .txt…")
        b.clicked.connect(self.save)
        btns.addWidget(b)
        btns.addStretch(1)
        self.copied = QLabel("")
        btns.addWidget(self.copied)
        b = QPushButton("Fechar")
        b.clicked.connect(self.accept)
        btns.addWidget(b)
        lay.addLayout(btns)
        self._fill()

    # ------------------------------------------------------------------
    def _entries(self):
        return entries(self.project.tracks, self.project.audio, self.project.export.tracklist_mark)

    def _fill(self):
        items = self._entries()
        hours = bool(items) and items[-1].end >= 3600
        self.mark.setEnabled(self.project.audio.crossfade > 0 and len(items) > 1)
        self.table.blockSignals(True)
        self.table.setRowCount(len(items))
        for r, e in enumerate(items):
            a = QTableWidgetItem(str(e.index))
            b = QTableWidgetItem(format_ts(e.start, hours))
            for it in (a, b):
                it.setFlags(it.flags() & ~Qt.ItemIsEditable)
            c = QTableWidgetItem(e.title)
            c.setData(Qt.UserRole, e.track)
            c.setToolTip(e.track.path)
            self.table.setItem(r, 0, a)
            self.table.setItem(r, 1, b)
            self.table.setItem(r, 2, c)
        self.table.blockSignals(False)
        self._refresh_text()

    def _refresh_text(self):
        items = self._entries()
        self.text.setPlainText(render_text(items, self.numbered.isChecked()))
        w = youtube_warnings(items)
        self.warn.setText("<span style='color:#ffd180'>" + "<br>".join(w) + "</span>" if w else
                          "<span style='color:#69f0ae'>✔ Formato aceito pelo YouTube para criar "
                          "capítulos automaticamente.</span>")
        self.copied.setText("")

    def _title_edited(self, item):
        if item.column() != 2:
            return
        track = item.data(Qt.UserRole)
        new = item.text().strip()
        # vazio volta ao nome automático
        track.title = new if new and new != Path(track.path).stem.replace("_", " ") else ""
        if not new:
            self.table.blockSignals(True)
            item.setText(track.display_title)
            self.table.blockSignals(False)
        self.changed.emit()
        self._refresh_text()

    def _mark_changed(self):
        self.project.export.tracklist_mark = self.mark.currentData()
        self.changed.emit()
        self._fill()

    def copy(self):
        QGuiApplication.clipboard().setText(self.text.toPlainText())
        self.copied.setText("✔ Copiado!")

    def save(self):
        stem = Path(self.project.tracks[0].path).stem if self.project.tracks else "video"
        f, _ = QFileDialog.getSaveFileName(self, "Salvar lista de faixas",
                                           str(Path(self.default_dir) / f"{stem} - lista de faixas.txt"),
                                           "Texto (*.txt)")
        if not f:
            return
        try:
            Path(f).write_text(self.text.toPlainText(), encoding="utf-8")
        except OSError as e:
            QMessageBox.warning(self, "Erro ao salvar", f"Não foi possível salvar:\n{e}")
            return
        self.copied.setText(f"✔ Salvo em {Path(f).name}")
