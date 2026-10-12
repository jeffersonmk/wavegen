"""Integração com FFmpeg: localização, sondagem, VAAPI e tradução de erros.

Todos os comandos são listas de argumentos (sem shell). Caminhos de mídia do usuário
são passados com o prefixo ``file:`` para que nomes com ``:`` ou começando com ``-``
nunca sejam interpretados como protocolo/opção.
"""

from __future__ import annotations

import functools
import glob
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class WavegenError(Exception):
    """Erro com mensagem amigável (PT-BR) + detalhes técnicos opcionais."""

    def __init__(self, message: str, details: str = "", code: str = "error"):
        super().__init__(message)
        self.message = message
        self.details = details
        self.code = code


def media_arg(path: str | os.PathLike) -> str:
    """Caminho seguro para usar como entrada/saída do ffmpeg."""
    return "file:" + str(Path(path).absolute())


@functools.lru_cache(maxsize=None)
def ffmpeg_path() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise WavegenError(
            "O FFmpeg não foi encontrado. Instale com: sudo pacman -S ffmpeg",
            code="ffmpeg_missing")
    return exe


@functools.lru_cache(maxsize=None)
def ffprobe_path() -> str:
    exe = shutil.which("ffprobe")
    if not exe:
        raise WavegenError(
            "O ffprobe (parte do FFmpeg) não foi encontrado. Instale com: sudo pacman -S ffmpeg",
            code="ffmpeg_missing")
    return exe


@functools.lru_cache(maxsize=None)
def ffmpeg_version() -> str:
    out = subprocess.run([ffmpeg_path(), "-hide_banner", "-version"],
                         capture_output=True, text=True).stdout
    return out.splitlines()[0] if out else "desconhecida"


@functools.lru_cache(maxsize=None)
def encoders() -> frozenset[str]:
    out = subprocess.run([ffmpeg_path(), "-hide_banner", "-encoders"],
                         capture_output=True, text=True).stdout
    names = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
            names.add(parts[1])
    return frozenset(names)


@dataclass
class AudioInfo:
    duration: float
    codec: str
    sample_rate: int
    channels: int
    artist: str = ""
    title: str = ""


def probe_audio(path: str | os.PathLike) -> AudioInfo:
    p = Path(path)
    if not p.exists():
        raise WavegenError(f"Arquivo não encontrado: {p}", code="missing_file")
    if not os.access(p, os.R_OK):
        raise WavegenError(f"Sem permissão para ler: {p}", code="permission")
    r = subprocess.run(
        [ffprobe_path(), "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name,sample_rate,channels,duration:format=duration"
         ":format_tags=artist,title,album_artist:stream_tags=artist,title",
         "-of", "json", media_arg(p)],
        capture_output=True, text=True)
    try:
        data = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        data = {}
    streams = data.get("streams") or []
    if r.returncode != 0 or not streams:
        raise WavegenError(f"“{p.name}” não parece ser um arquivo de áudio válido ou suportado.",
                           details=r.stderr.strip(), code="bad_audio")
    s = streams[0]
    dur = None
    for src in (s, data.get("format", {})):
        d = src.get("duration")
        if d not in (None, "N/A"):
            dur = float(d)
            break
    if not dur or dur <= 0:
        raise WavegenError(f"Não foi possível determinar a duração de “{p.name}”.",
                           details=r.stdout, code="bad_audio")
    tags: dict = {}
    for src in (s.get("tags") or {}, data.get("format", {}).get("tags") or {}):
        for k, v in src.items():
            tags.setdefault(k.lower(), str(v).strip())
    return AudioInfo(dur, s.get("codec_name", "?"), int(s.get("sample_rate") or 0),
                     int(s.get("channels") or 0),
                     tags.get("artist") or tags.get("album_artist", ""), tags.get("title", ""))


def probe_streams(path: str | os.PathLike) -> dict:
    r = subprocess.run(
        [ffprobe_path(), "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height,pix_fmt,r_frame_rate,duration:format=duration",
         "-of", "json", media_arg(path)], capture_output=True, text=True)
    if r.returncode != 0:
        raise WavegenError("O arquivo gerado não pôde ser lido.", details=r.stderr, code="verify")
    return json.loads(r.stdout)


# ----------------------------------------------------------------------------- VAAPI

def vaapi_devices() -> list[str]:
    return sorted(glob.glob("/dev/dri/renderD*"))


@dataclass
class VaapiStatus:
    available: bool
    device: str | None
    message: str
    details: str = ""


_vaapi_cache: dict[tuple, VaapiStatus] = {}


def check_vaapi(codec: str = "h264_vaapi", width: int = 1920, height: int = 1080) -> VaapiStatus:
    """Testa de verdade uma codificação curta na GPU (encoder listado ≠ encoder funcionando)."""
    key = (codec, width, height)
    if key in _vaapi_cache:
        return _vaapi_cache[key]
    try:
        ff = ffmpeg_path()
    except WavegenError as e:
        return VaapiStatus(False, None, e.message)
    if codec not in encoders():
        st = VaapiStatus(False, None, f"Este FFmpeg não tem o encoder {codec}.")
        _vaapi_cache[key] = st
        return st
    devs = vaapi_devices()
    if not devs:
        st = VaapiStatus(False, None, "Nenhuma GPU com VAAPI encontrada (/dev/dri/renderD*).")
        _vaapi_cache[key] = st
        return st
    last = ""
    for dev in devs:
        r = subprocess.run(
            [ff, "-hide_banner", "-v", "error", "-vaapi_device", dev,
             "-f", "lavfi", "-i", f"color=c=gray:s={width}x{height}:r=30:d=0.2",
             "-vf", "format=nv12,hwupload", "-c:v", codec, "-qp", "24", "-f", "null", "-"],
            capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            st = VaapiStatus(True, dev, f"GPU disponível ({dev}) para {codec} em {width}×{height}.")
            _vaapi_cache[key] = st
            return st
        last = r.stderr.strip()
    st = VaapiStatus(False, None,
                     f"A GPU não conseguiu codificar {codec} em {width}×{height} "
                     "(driver/VAAPI sem suporte para essa configuração).", last)
    _vaapi_cache[key] = st
    return st


# ----------------------------------------------------------------------------- erros

_PATTERNS = [
    ("No space left on device", "disk_full",
     "Não há espaço suficiente no disco. Libere espaço ou escolha outra pasta de destino."),
    ("Permission denied", "permission",
     "Sem permissão para ler ou gravar um dos arquivos. Verifique a pasta de destino."),
    ("Read-only file system", "permission", "A pasta de destino está em um disco somente leitura."),
    ("No such file or directory", "missing_file", "Um dos arquivos não foi encontrado."),
    ("Invalid data found when processing input", "bad_input",
     "Um dos arquivos está corrompido ou em um formato não suportado."),
    ("vaapi", "vaapi_failed",
     "A codificação pela GPU (VAAPI) falhou. Tente novamente usando a codificação por software (CPU)."),
    ("hwupload", "vaapi_failed",
     "A codificação pela GPU (VAAPI) falhou. Tente novamente usando a codificação por software (CPU)."),
    ("Error while opening encoder", "encoder",
     "O codificador de vídeo não aceitou as configurações escolhidas."),
    ("Conversion failed", "ffmpeg", "O FFmpeg não conseguiu concluir a conversão."),
]


def translate_ffmpeg_error(stderr: str, default: str = "O FFmpeg encontrou um erro.") -> WavegenError:
    low = stderr.lower()
    for needle, code, msg in _PATTERNS:
        if needle.lower() in low:
            return WavegenError(msg, details=stderr[-4000:], code=code)
    return WavegenError(default, details=stderr[-4000:], code="ffmpeg")
