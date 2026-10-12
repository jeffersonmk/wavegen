"""Biblioteca de estilos de waveform. Novos estilos: crie um módulo e importe-o aqui."""

from .base import COMMON, REGISTRY, FrameData, ParamSpec, Style, register  # noqa: F401
from . import spectrum, vinyl, waves  # noqa: F401  (registram os estilos)

ORDER = ["bars", "mirror", "wave_line", "circle_wave", "circle_spectrum",
         "minimal", "analyzer", "sym_wave", "filled_wave", "neon", "vinyl"]


def get_style(style_id: str) -> Style:
    cls = REGISTRY.get(style_id) or REGISTRY["bars"]
    return cls()


def style_list() -> list[type[Style]]:
    ids = [i for i in ORDER if i in REGISTRY] + [i for i in REGISTRY if i not in ORDER]
    return [REGISTRY[i] for i in ids]
