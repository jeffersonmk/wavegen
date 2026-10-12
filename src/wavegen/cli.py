"""Linha de comando.

  wavegen                      abre a interface gráfica
  wavegen projeto.wavegen.json abre a interface com o projeto
  wavegen tracklist projeto.wavegen.json [--numerar]
                               mostra a lista de faixas com os tempos (para o YouTube)
  wavegen render --project projeto.wavegen.json --out video.mp4 [--sample INICIO DURACAO]
                               renderiza sem interface (mostra o progresso no terminal)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys


def render_cli(argv: list[str]) -> int:
    """Executa o job e mostra um progresso legível."""
    cmd = [sys.executable, "-m", "wavegen.job"] + argv
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
    code = 1
    for line in proc.stdout:
        try:
            ev = json.loads(line)
        except ValueError:
            print(line, end="")
            continue
        t = ev.get("type")
        if t == "stage":
            print("\n" + ev["message"], flush=True)
        elif t == "progress" and ev.get("stage") == "render":
            eta = ev.get("eta")
            sys.stderr.write(f"\r  {ev['fraction'] * 100:5.1f}%  quadro {ev['done']}/{ev['total']}  "
                             f"{ev['fps']} q/s  restam ~{eta or 0:.0f}s   ")
            sys.stderr.flush()
        elif t == "done":
            print(f"\nPronto: {ev['path']} ({ev['size'] / 1e6:.1f} MB, {ev['duration']:.1f}s, "
                  f"{ev['elapsed']:.0f}s de renderização, {ev['encoder']})")
            code = 0
        elif t == "error":
            print(f"\nErro: {ev['message']}", file=sys.stderr)
            if ev.get("details") and os.environ.get("WAVEGEN_DEBUG"):
                print(ev["details"], file=sys.stderr)
    proc.wait()
    return code if proc.returncode == 0 else (proc.returncode or 1)


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "tracklist":
        from .project import Project
        from .tracklist import entries, project_text, youtube_warnings
        pr = Project.load(sys.argv[2])
        print(project_text(pr, numbered="--numerar" in sys.argv), end="")
        for w in youtube_warnings(entries(pr.tracks, pr.audio, pr.export.tracklist_mark)):
            print("Aviso:", w, file=sys.stderr)
        return 0
    if len(sys.argv) > 1 and sys.argv[1] == "render":
        return render_cli(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    from .gui.app import main as gui_main
    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
