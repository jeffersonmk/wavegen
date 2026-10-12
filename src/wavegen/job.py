"""Renderização completa (roda em um processo separado do da interface).

Protocolo: uma linha JSON por evento no stdout:
  {"type": "stage", "stage": "mix|analysis|check|render|verify", "message": "..."}
  {"type": "progress", "stage": "...", "fraction": 0.42, "done": 123, "total": 456, "fps": 61.2, "eta": 33.1}
  {"type": "done", "path": "...", "size": 123456, "duration": 684.6, "elapsed": 99.1, "encoder": "..."}
  {"type": "error", "code": "...", "message": "...", "details": "..."}

Uso: python -m wavegen.job --project projeto.json --out video.mp4 [--sample INICIO DURACAO]
O processo termina com SIGTERM (cancelar) removendo o arquivo parcial.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

Emit = Callable[[dict], None]


def _ensure_qt():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication
    return QGuiApplication.instance() or QGuiApplication([])


def estimate_bytes_upper(fmt: str, w: int, h: int, fps: float, duration: float, akbps: int) -> float:
    """Limite superior grosseiro (para checar espaço em disco antes de começar)."""
    px = w * h * fps
    if fmt == "mp4":
        vbps = px * 0.25          # bem acima do que imagem estática + waveform costuma gerar
    elif fmt == "mov_prores":
        vbps = px * 4.0
    else:
        vbps = px * 1.2
    abps = akbps * 1000 if fmt == "mp4" else 48000 * 2 * 24
    return (vbps + abps) / 8 * duration


def run_render(project, out_path: Path, emit: Emit, cancel: threading.Event,
               sample: tuple[float, float] | None = None, workers: int = 0) -> dict:
    from PySide6.QtGui import QImage

    from .analysis import ensure_features
    from .compositor import Compositor
    from .export import (EncodeJob, audio_kbps, build_encode_command, fps_of, is_transparent,
                         resolution)
    from .ffmpeg import WavegenError, check_vaapi, probe_streams, translate_ffmpeg_error
    from .parallel import ParallelFrames, default_workers
    from .playlist import ensure_mix, open_pcm

    t_start = time.monotonic()
    ex = project.export
    W, H = resolution(ex.preset)
    fps = fps_of(ex.fps)
    transparent = is_transparent(ex.format)
    out_path = Path(out_path).absolute()

    # ---------------------------------------------------------------- validações
    if not project.tracks:
        raise WavegenError("Adicione pelo menos uma música antes de renderizar.", code="no_audio")
    missing = project.missing_files()
    if missing:
        raise WavegenError("Arquivos de origem não encontrados:\n" + "\n".join(missing), code="missing_file")
    out_dir = out_path.parent
    if not out_dir.is_dir():
        raise WavegenError(f"A pasta de destino não existe: {out_dir}", code="permission")
    if not os.access(out_dir, os.W_OK):
        raise WavegenError(f"Sem permissão para gravar em {out_dir}", code="permission")

    # ---------------------------------------------------------------- trilha + análise
    emit({"type": "stage", "stage": "mix", "message": "Montando a trilha sonora…"})
    mix = ensure_mix(project.tracks, project.audio,
                     lambda f: emit({"type": "progress", "stage": "mix", "fraction": f}), cancel)
    emit({"type": "stage", "stage": "analysis", "message": "Analisando o áudio…"})
    feats = ensure_features(mix, fps, lambda f: emit({"type": "progress", "stage": "analysis",
                                                      "fraction": f}), cancel)
    pcm, sr = open_pcm(mix)

    n0, n1 = 0, feats.n_frames
    audio_start, audio_dur = 0.0, None
    if sample is not None:
        s_start, s_dur = sample
        n0 = max(0, min(int(s_start * fps), feats.n_frames - 1))
        n1 = min(feats.n_frames, n0 + max(1, int(s_dur * fps)))
        audio_start = float(n0 / fps)
        audio_dur = float((n1 - n0) / fps)
    n_total = n1 - n0
    duration = n_total / float(fps)

    # ---------------------------------------------------------------- disco
    need = estimate_bytes_upper(ex.format, W, H, float(fps), duration, audio_kbps(ex.quality))
    free = shutil.disk_usage(out_dir).free
    if sample is None and free < need * 0.5:
        raise WavegenError(
            f"Pouco espaço livre em {out_dir}: {free / 1e9:.1f} GB livres, o vídeo pode precisar de "
            f"até ~{need / 1e9:.1f} GB. Libere espaço ou escolha outra pasta.", code="disk_full")

    # ---------------------------------------------------------------- encoder
    encoder = ex.encoder if ex.format == "mp4" else "software"
    vaapi_dev = None
    if encoder == "vaapi":
        emit({"type": "stage", "stage": "check", "message": "Verificando a GPU (VAAPI)…"})
        st = check_vaapi("h264_vaapi", W, H)
        if not st.available:
            raise WavegenError(st.message + " Você pode renderizar usando a CPU (software).",
                               details=st.details, code="vaapi_unavailable")
        vaapi_dev = st.device

    copy_audio = None
    a = project.audio
    if (sample is None and ex.format == "mp4" and len(project.tracks) == 1
            and project.tracks[0].codec == "aac" and abs(a.volume_db) < 1e-6
            and a.fade_in <= 0 and a.fade_out <= 0):
        copy_audio = Path(project.tracks[0].path)   # áudio original sem recodificar

    job = EncodeJob(W, H, fps, ex.format, ex.quality, encoder, vaapi_dev,
                    audio_path=mix, audio_copy_from=copy_audio,
                    audio_start=audio_start, duration=audio_dur)
    tmp = out_path.with_name(f".{out_path.stem}.part{out_path.suffix or '.mp4'}")
    cmd = build_encode_command(job, tmp)

    # ---------------------------------------------------------------- render
    emit({"type": "stage", "stage": "render",
          "message": "Renderizando o vídeo…" + (" (codificação na GPU)" if encoder == "vaapi" else "")})
    workers = workers or default_workers(W, H, encoder)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    err_buf: list[bytes] = []
    t_err = threading.Thread(target=lambda: err_buf.append(proc.stderr.read()), daemon=True)
    t_err.start()

    if workers > 1:
        frames = ParallelFrames(project, mix, fps, W, H, transparent, n0, n1, workers)
        frame_iter = iter(frames)
    else:
        frames = None
        comp = Compositor(project, W, H, feats, pcm, transparent=transparent)

        def _seq():
            for n in range(n0, n1):
                img = comp.render(n)
                if transparent:
                    img = img.convertToFormat(QImage.Format_ARGB32)
                yield img.constBits()
        frame_iter = _seq()

    def cleanup():
        try:
            proc.kill()
        except OSError:
            pass
        proc.wait()
        for pipe in (proc.stdin, proc.stderr):
            try:
                pipe.close()
            except (OSError, ValueError):
                pass
        tmp.unlink(missing_ok=True)

    last_emit = 0.0
    t_render = time.monotonic()
    write_error = None
    try:
        for i, buf in enumerate(frame_iter):
            if cancel.is_set():
                cleanup()
                raise WavegenError("Renderização cancelada.", code="cancelled")
            try:
                proc.stdin.write(buf)
            except (BrokenPipeError, OSError) as e:
                write_error = e
                break
            now = time.monotonic()
            if now - last_emit > 0.25 or i == n_total - 1:
                last_emit = now
                el = now - t_render
                rate = (i + 1) / el if el > 0 else 0.0
                emit({"type": "progress", "stage": "render", "fraction": (i + 1) / n_total,
                      "done": i + 1, "total": n_total, "fps": round(rate, 1),
                      "eta": round((n_total - i - 1) / rate, 1) if rate > 0 else None,
                      "workers": workers})
        try:
            proc.stdin.close()
        except OSError:
            pass
        proc.wait()
        t_err.join(timeout=5)
    except WavegenError:
        raise
    except BaseException as e:
        cleanup()
        if isinstance(e, RuntimeError):
            raise WavegenError("Falha ao desenhar os quadros do vídeo.", details=str(e), code="internal")
        raise
    finally:
        if frames is not None:
            frame_iter.close()
            frames.close()

    for pipe in (proc.stdin, proc.stderr):
        try:
            pipe.close()
        except (OSError, ValueError):
            pass
    stderr = b"".join(err_buf).decode("utf-8", "replace")
    if proc.returncode != 0 or write_error is not None:
        tmp.unlink(missing_ok=True)
        err = translate_ffmpeg_error(stderr, "O FFmpeg falhou ao gerar o vídeo.")
        if encoder == "vaapi" and err.code in ("ffmpeg", "encoder"):
            err.code = "vaapi_failed"
            err.message = ("A codificação pela GPU (VAAPI) falhou durante a renderização. "
                           "Tente novamente usando a CPU (software).")
        raise err

    # ---------------------------------------------------------------- verificação
    emit({"type": "stage", "stage": "verify", "message": "Verificando o arquivo final…"})
    info = probe_streams(tmp)
    kinds = [s.get("codec_type") for s in info.get("streams", [])]
    got = float(info.get("format", {}).get("duration") or 0)
    tol = max(0.5, 3 / float(fps))
    if "video" not in kinds or "audio" not in kinds or abs(got - duration) > tol:
        tmp.unlink(missing_ok=True)
        raise WavegenError("O arquivo gerado não passou na verificação (faltam faixas ou a duração "
                           "não confere).", details=json.dumps(info, indent=1), code="verify")
    os.replace(tmp, out_path)
    tracklist_path = None
    if sample is None and getattr(ex, "save_tracklist", False):
        from .tracklist import project_text
        tracklist_path = out_path.with_name(f"{out_path.stem} - lista de faixas.txt")
        try:
            tracklist_path.write_text(project_text(project), encoding="utf-8")
        except OSError:
            tracklist_path = None        # o vídeo está pronto; a lista é só um extra
    return {"type": "done", "path": str(out_path), "size": out_path.stat().st_size,
            "tracklist": str(tracklist_path) if tracklist_path else None,
            "duration": round(got, 3), "elapsed": round(time.monotonic() - t_start, 1),
            "encoder": "GPU (VAAPI h264)" if encoder == "vaapi" else
            ("CPU (libx264)" if ex.format == "mp4" else "CPU"),
            "audio": "copiado do original" if copy_audio else "codificado",
            "frames": n_total, "workers": workers}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="wavegen-job")
    ap.add_argument("--project", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sample", nargs=2, type=float, metavar=("INICIO", "DURACAO"))
    ap.add_argument("--workers", type=int, default=0, help="processos de desenho (0 = automático)")
    args = ap.parse_args(argv)
    _ensure_qt()
    from .ffmpeg import WavegenError
    from .project import Project

    lock = threading.Lock()

    def emit(ev: dict):
        with lock:
            sys.stdout.write(json.dumps(ev, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    cancel = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: cancel.set())
    signal.signal(signal.SIGINT, lambda *_: cancel.set())
    try:
        project = Project.load(args.project)
        res = run_render(project, Path(args.out), emit, cancel,
                         tuple(args.sample) if args.sample else None, args.workers)
        emit(res)
        return 0
    except WavegenError as e:
        emit({"type": "error", "code": e.code, "message": e.message, "details": e.details})
        return 2 if e.code == "cancelled" else 1
    except Exception as e:  # erro inesperado: mensagem simples + detalhes técnicos
        import traceback
        emit({"type": "error", "code": "internal",
              "message": "Ocorreu um erro inesperado durante a renderização.",
              "details": traceback.format_exc()})
        return 1


if __name__ == "__main__":
    sys.exit(main())
