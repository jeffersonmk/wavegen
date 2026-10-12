"""Modelo do projeto (salvo como JSON) e preferências do aplicativo."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .analysis import Response
from .playlist import AudioSettings, Track

PROJECT_VERSION = 1
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "wavegen"


@dataclass
class Box:
    """Área do visualizador em frações do quadro (centro x/y, largura, altura)."""
    x: float = 0.5
    y: float = 0.5
    w: float = 0.84
    h: float = 0.32
    scale: float = 1.0

    @classmethod
    def from_dict(cls, d: dict) -> "Box":
        return cls(**{k: float(d[k]) for k in cls.__dataclass_fields__ if k in d})


@dataclass
class BackgroundSettings:
    image: str = ""
    fit: str = "cover"            # cover = preencher (recorta) | contain = imagem inteira
    focus_x: float = 0.5          # posição do recorte / alinhamento (0 = esquerda, 1 = direita)
    focus_y: float = 0.5
    zoom: float = 1.0
    fill: str = "blur"            # com "contain": blur = imagem desfocada | color = cor sólida
    color: str = "#101014"
    dim: float = 0.0              # escurecer o fundo (0..0.8)
    loop_video: bool = True       # vídeo de fundo: repetir até o fim da trilha (senão, congela no fim)

    @classmethod
    def from_dict(cls, d: dict) -> "BackgroundSettings":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass
class ExportSettings:
    preset: str = "1080p"
    fps: str = "30"
    quality: str = "high"         # high | small
    encoder: str = "software"     # software | vaapi
    format: str = "mp4"           # mp4 | mov_alpha | mov_prores
    out_dir: str = str(Path.home() / "Videos")
    filename: str = ""
    save_tracklist: bool = True    # grava "<vídeo>.txt" com a lista de faixas para a descrição
    tracklist_mark: str = "start"  # start | middle: onde marcar a troca durante um crossfade

    @classmethod
    def from_dict(cls, d: dict) -> "ExportSettings":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


@dataclass
class Project:
    background: BackgroundSettings = field(default_factory=BackgroundSettings)
    tracks: list[Track] = field(default_factory=list)
    audio: AudioSettings = field(default_factory=AudioSettings)
    style: str = "bars"
    style_params: dict[str, dict] = field(default_factory=dict)   # por estilo
    boxes: dict[str, Box] = field(default_factory=dict)            # posição por estilo
    response: Response = field(default_factory=Response)
    export: ExportSettings = field(default_factory=ExportSettings)

    # --- acesso conveniente ----------------------------------------------
    def params_for(self, style_id: str | None = None) -> dict:
        from .styles import get_style
        sid = style_id or self.style
        return get_style(sid).resolve(self.style_params.get(sid, {}))

    def box_for(self, style_id: str | None = None) -> Box:
        from .styles import get_style
        sid = style_id or self.style
        if sid not in self.boxes:
            x, y, w, h = get_style(sid).default_box
            self.boxes[sid] = Box(x, y, w, h)
        return self.boxes[sid]

    # --- serialização ------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "app": "wavegen", "version": PROJECT_VERSION,
            "background": asdict(self.background),
            "tracks": [{"path": t.path, "duration": t.duration, "codec": t.codec, "title": t.title}
                       for t in self.tracks],
            "audio": asdict(self.audio),
            "style": self.style,
            "style_params": self.style_params,
            "boxes": {k: asdict(v) for k, v in self.boxes.items()},
            "response": asdict(self.response),
            "export": asdict(self.export),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        p = cls()
        p.background = BackgroundSettings.from_dict(d.get("background", {}))
        p.tracks = [Track(str(t["path"]), float(t.get("duration", 0) or 0), t.get("codec", ""),
                          str(t.get("title", "") or ""))
                    for t in d.get("tracks", []) if t.get("path")]
        p.audio = AudioSettings.from_dict(d.get("audio", {}))
        p.style = str(d.get("style", "bars"))
        p.style_params = {k: dict(v) for k, v in (d.get("style_params") or {}).items()}
        p.boxes = {k: Box.from_dict(v) for k, v in (d.get("boxes") or {}).items()}
        p.response = Response.from_dict(d.get("response", {}))
        p.export = ExportSettings.from_dict(d.get("export", {}))
        return p

    def save(self, path: str | Path) -> None:
        path = Path(path)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        if d.get("app") != "wavegen":
            raise ValueError("Este arquivo não é um projeto do Wavegen.")
        return cls.from_dict(d)

    def missing_files(self) -> list[str]:
        out = []
        if self.background.image and not Path(self.background.image).is_file():
            out.append(self.background.image)
        out += [t.path for t in self.tracks if not t.exists()]
        out += [v for sid, _, v in self.image_params()
                if sid == self.style and not Path(v).is_file() and v not in out]
        return out

    def image_params(self) -> list[tuple[str, str, str]]:
        """(estilo, parâmetro, caminho) de todas as imagens usadas pelos estilos (ex.: capa)."""
        from .styles import REGISTRY
        out = []
        for sid, prm in self.style_params.items():
            cls = REGISTRY.get(sid)
            if cls is None:
                continue
            for spec in cls.params:
                if spec.kind == "image" and prm.get(spec.key):
                    out.append((sid, spec.key, str(prm[spec.key])))
        return out


# ----------------------------------------------------------------------------- preferências

def load_prefs() -> dict:
    try:
        return json.loads((CONFIG_DIR / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_prefs(prefs: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    f = CONFIG_DIR / "settings.json"
    tmp = f.with_name("settings.json.tmp")
    tmp.write_text(json.dumps(prefs, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(f)
