"""Tarefas em segundo plano (threads) com sinais Qt — a interface nunca bloqueia."""

from __future__ import annotations

import threading
import traceback
from typing import Callable

from PySide6.QtCore import QObject, Signal


class Task(QObject):
    """Executa ``fn(progress, cancel)`` numa thread; sinais chegam na thread da interface."""

    progress = Signal(float)
    finished = Signal(object)
    failed = Signal(object)     # WavegenError ou Exception

    def __init__(self, fn: Callable, parent=None):
        super().__init__(parent)
        self.fn = fn
        self.cancel = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "Task":
        self._thread.start()
        return self

    def stop(self):
        self.cancel.set()

    def _run(self):
        try:
            res = self.fn(self.progress.emit, self.cancel)
        except Exception as e:  # noqa: BLE001
            if not hasattr(e, "message"):
                e.details = traceback.format_exc()  # type: ignore[attr-defined]
            if not self.cancel.is_set():
                self.failed.emit(e)
            return
        if not self.cancel.is_set():
            self.finished.emit(res)
