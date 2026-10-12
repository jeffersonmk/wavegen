"""Predefinições de exportação e montagem do comando de codificação do FFmpeg."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from .ffmpeg import ffmpeg_path, media_arg

PRESETS: dict[str, tuple[int, int, str]] = {
    "1080p": (1920, 1080, "Full HD 16:9 — 1920×1080"),
    "720p": (1280, 720, "HD 16:9 — 1280×720"),
    "4k": (3840, 2160, "4K 16:9 — 3840×2160"),
    "vertical": (1080, 1920, "Vertical 9:16 — 1080×1920"),
    "square": (1080, 1080, "Quadrado 1:1 — 1080×1080"),
}

FPS_CHOICES: dict[str, tuple[Fraction, str]] = {
    "30": (Fraction(30), "30 fps (padrão)"),
    "60": (Fraction(60), "60 fps (mais fluido, render mais lento)"),
    "24": (Fraction(24), "24 fps"),
    "25": (Fraction(25), "25 fps"),
    "29.97": (Fraction(30000, 1001), "29,97 fps"),
    "59.94": (Fraction(60000, 1001), "59,94 fps"),
}

FORMATS: dict[str, tuple[str, str]] = {
    "mp4": ("MP4 · H.264 + AAC (YouTube)", ".mp4"),
    "mov_alpha": ("MOV transparente · PNG (DaVinci Resolve)", ".mov"),
    "mov_prores": ("MOV transparente · ProRes 4444 (DaVinci Resolve)", ".mov"),
}

QUALITIES = {"high": "Alta qualidade", "small": "Arquivo menor"}

# qualidade por codificador: (software CRF, VAAPI QP, áudio AAC kbps)
_Q = {"high": (18, 20, 320), "small": (23, 26, 192)}


def fps_of(key: str) -> Fraction:
    return FPS_CHOICES.get(key, (Fraction(30), ""))[0]


def resolution(preset: str) -> tuple[int, int]:
    w, h, _ = PRESETS.get(preset, PRESETS["1080p"])
    return w, h


def is_transparent(fmt: str) -> bool:
    return fmt in ("mov_alpha", "mov_prores")


def extension(fmt: str) -> str:
    return FORMATS.get(fmt, FORMATS["mp4"])[1]


def audio_kbps(quality: str) -> int:
    return _Q.get(quality, _Q["high"])[2]


@dataclass
class EncodeJob:
    width: int
    height: int
    fps: Fraction
    fmt: str = "mp4"
    quality: str = "high"
    encoder: str = "software"          # software | vaapi
    vaapi_device: str | None = None
    audio_path: Path | None = None     # trilha mixada (WAV)
    audio_copy_from: Path | None = None  # copiar AAC original sem recodificar
    audio_start: float = 0.0
    duration: float | None = None


def build_encode_command(job: EncodeJob, out: Path) -> list[str]:
    """Comando ffmpeg que recebe quadros BGRA pelo stdin e grava vídeo + áudio."""
    fps = f"{job.fps.numerator}/{job.fps.denominator}"
    gop = max(1, round(float(job.fps) * 2))
    cmd = [ffmpeg_path(), "-hide_banner", "-nostdin", "-v", "error", "-y"]
    if job.encoder == "vaapi" and job.fmt == "mp4":
        cmd += ["-vaapi_device", job.vaapi_device or "/dev/dri/renderD128"]
    cmd += ["-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{job.width}x{job.height}",
            "-r", fps, "-i", "pipe:0"]
    has_audio = False
    if job.audio_copy_from is not None:
        cmd += ["-i", media_arg(job.audio_copy_from)]
        has_audio = True
    elif job.audio_path is not None:
        if job.audio_start > 0:
            cmd += ["-ss", f"{job.audio_start:.6f}"]
        if job.duration is not None:
            cmd += ["-t", f"{job.duration:.6f}"]
        cmd += ["-i", media_arg(job.audio_path)]
        has_audio = True
    cmd += ["-map", "0:v:0"]
    if has_audio:
        cmd += ["-map", "1:a:0"]

    crf, qp, akbps = _Q.get(job.quality, _Q["high"])
    if job.fmt == "mp4":
        tags = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                "-color_range", "tv"]
        if job.encoder == "vaapi":
            cmd += ["-vf", "scale=out_color_matrix=bt709:out_range=tv,format=nv12,hwupload",
                    "-c:v", "h264_vaapi", "-rc_mode", "CQP", "-qp", str(qp),
                    "-profile:v", "high", "-g", str(gop)]
        else:
            cmd += ["-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
                    "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
                    "-profile:v", "high", "-g", str(gop), "-bf", "2"]
        cmd += tags
        if has_audio:
            cmd += (["-c:a", "copy"] if job.audio_copy_from is not None
                    else ["-c:a", "aac", "-b:a", f"{akbps}k", "-ar", "48000"])
        cmd += ["-movflags", "+faststart", "-f", "mp4"]
    elif job.fmt == "mov_alpha":
        cmd += ["-c:v", "png", "-pix_fmt", "rgba"]
        if has_audio:
            cmd += ["-c:a", "pcm_s24le"]
        cmd += ["-f", "mov"]
    elif job.fmt == "mov_prores":
        cmd += ["-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuva444p10le",
                "-alpha_bits", "16", "-qscale:v", "4" if job.quality == "high" else "9",
                "-vendor", "apl0"]
        if has_audio:
            cmd += ["-c:a", "pcm_s24le"]
        cmd += ["-f", "mov"]
    else:
        raise ValueError(f"formato desconhecido: {job.fmt}")
    cmd += [media_arg(out)]
    return cmd


def estimate_size(fmt: str, quality: str, encoder: str, w: int, h: int, fps: Fraction,
                  duration: float) -> tuple[float, float]:
    """Faixa estimada (bytes). Calibrada com imagens reais + waveform; é só uma estimativa."""
    px = w * h * float(fps)
    if fmt == "mp4":
        bpp = {"high": 0.040, "small": 0.017}.get(quality, 0.04)
        if encoder == "vaapi":
            bpp *= 2.0                                    # VAAPI (CQP) gera ~2× o x264 na mesma qualidade
        v = px * bpp * (30 / float(fps)) ** 0.35          # mais fps comprime melhor por quadro
        a = audio_kbps(quality) * 1000
        lo, hi = (v * 0.45 + a), (v * 2.2 + a)
    elif fmt == "mov_prores":
        v = px * (0.9 if quality == "high" else 0.55)
        lo, hi = v * 0.4 + 2304000, v * 1.6 + 2304000
    else:
        v = px * 0.12
        lo, hi = v * 0.3 + 2304000, v * 3.0 + 2304000
    return lo / 8 * duration, hi / 8 * duration


def default_filename(project_tracks: list, fmt: str) -> str:
    stem = Path(project_tracks[0].path).stem if project_tracks else "video"
    if len(project_tracks) > 1:
        stem += f" (+{len(project_tracks) - 1})"
    return stem + extension(fmt)
