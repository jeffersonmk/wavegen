"""Exercita a interface sem tela (QT_QPA_PLATFORM=offscreen) e salva capturas.

Uso: QT_QPA_PLATFORM=offscreen uv run python tests/gui_smoke.py OUT_DIR IMAGEM AUDIO...
"""

import os as _os
import tempfile as _tf
# preferências e cache isolados: o teste nunca altera ~/.config/wavegen nem ~/.cache/wavegen
_iso = _tf.mkdtemp(prefix="wavegen-iso-")
_os.environ["XDG_CONFIG_HOME"] = _os.path.join(_iso, "config")
_os.environ["XDG_CACHE_HOME"] = _os.path.join(_iso, "cache")


import sys
import functools
print = functools.partial(print, flush=True)  # noqa: A001
import time
from pathlib import Path

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

out_dir, image, *audios = sys.argv[1:]
Path(out_dir).mkdir(parents=True, exist_ok=True)

app = QApplication(["wavegen"])
from wavegen.gui import app as gapp  # noqa: E402

gapp.setup_theme(app)
w = gapp.MainWindow()
w.resize(1600, 950)
w.show()


def wait(ms=300, until=None, timeout=60.0):
    t0 = time.time()
    while True:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()
        if until is None or until() or time.time() - t0 > timeout:
            return


def shot(name):
    wait(200)
    w.grab().save(str(Path(out_dir) / f"{name}.png"))
    print("captura:", name)


wait(800)
shot("01_inicial")
w.set_image(image)
w.add_files(audios)
wait(300, until=lambda: len(w.project.tracks) == len(audios))
wait(300, until=lambda: not w.using_demo and w.mix_task is None, timeout=120)
print("trilha pronta:", not w.using_demo, "faixas:", len(w.project.tracks))
w.player.seek(int(75 * w.sr))
w._render_current(force=True)
shot("02_com_arquivos")
w.tabs.setCurrentIndex(0)
w.gallery.select("neon")
shot("02b_galeria")
for sid in ("circle_spectrum", "filled_wave", "neon"):
    w.gallery.select(sid)
    w.set_style(sid)
    wait(200)
    w._render_current(force=True)
w.tabs.setCurrentIndex(1)
shot("03_personalizar_neon")
w.tabs.setCurrentIndex(4)
shot("04_exportar")
w.gallery.select("vinyl"); w.set_style("vinyl")
w.set_param("cover", sys.argv[2]); w.set_param("label_mode", "cover")
wait(300); w._render_current(force=True)
w.tabs.setCurrentIndex(1)
shot("07_vinil")
# ---- renderização pela interface (processo separado) + cancelamento
gapp.QMessageBox.exec = lambda self: 0            # sem diálogos modais no teste
w.project.export.out_dir = out_dir
seen = []
orig = w._render_stdout
def spy():
    orig()
    seen.append(w.progress.value())
w.render_proc = None
w.start_render(sample=True)
w.render_proc.readyReadStandardOutput.disconnect()
w.render_proc.readyReadStandardOutput.connect(spy)
t0 = time.time(); ui_ticks = 0
while w.render_proc is not None and time.time() - t0 < 120:
    wait(50); ui_ticks += 1                       # a interface continua processando eventos
print("teste 15s:", w.status.text())
print("  ticks da interface durante o render:", ui_ticks, "progresso visto:", sorted(set(seen))[-3:])
shot("05_render_concluido")
w.start_render(sample=True)
wait(300, until=lambda: w.progress.value() > 80, timeout=60)
w.cancel_render()
wait(200, until=lambda: w.render_proc is None, timeout=30)
print("cancelar:", w.status.text())
print("  parciais restantes:", list(Path(out_dir).glob(".*part*")))
# arraste do visualizador via API do preview
b = w.project.box_for()
print("box:", b)
w.dirty = False
w.close()
app.quit()
