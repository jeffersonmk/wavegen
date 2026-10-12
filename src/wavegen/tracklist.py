"""Lista de faixas com o tempo de início de cada música no vídeo (para a descrição do YouTube).

Os tempos seguem exatamente a montagem da trilha (playlist.build_mix_command):
- em sequência: cada faixa começa quando a anterior termina (+ intervalo, se houver);
- com crossfade: a próxima começa no início da sobreposição (ou no meio, se preferir).

O YouTube transforma a lista em capítulos quando: o primeiro tempo é 0:00, há pelo menos
3 tempos e cada trecho tem 10 s ou mais.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .playlist import AudioSettings, Track, effective_crossfade, ordered, total_duration


@dataclass
class Entry:
    index: int          # posição no vídeo (1, 2, …)
    track: Track
    start: float        # s, onde a lista marca o início
    end: float          # s, onde a próxima entrada começa (ou fim do vídeo)

    @property
    def title(self) -> str:
        return self.track.display_title


def entries(tracks: list[Track], st: AudioSettings, mark: str = "start") -> list[Entry]:
    tr = ordered(tracks, st)
    if not tr:
        return []
    durs = [t.duration for t in tr]
    xf = effective_crossfade(durs, st)
    gap = st.gap if xf == 0 else 0.0
    starts, pos = [], 0.0
    for i, d in enumerate(durs):
        s = pos
        if i > 0 and xf > 0 and mark == "middle":
            s += xf / 2
        starts.append(s)
        pos += d - xf + gap
    total = total_duration(durs, st)
    out = []
    for i, t in enumerate(tr):
        end = starts[i + 1] if i + 1 < len(tr) else total
        out.append(Entry(i + 1, t, starts[i], end))
    return out


def format_ts(sec: float, hours: bool) -> str:
    s = max(0, int(math.floor(sec + 1e-6)))     # arredonda para baixo: nunca marca depois do início
    h, m, x = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{x:02d}" if hours else f"{m}:{x:02d}"


def render_text(items: list[Entry], numbered: bool = False) -> str:
    if not items:
        return ""
    hours = items[-1].end >= 3600
    lines = []
    for e in items:
        name = f"{e.index:02d}. {e.title}" if numbered else e.title
        lines.append(f"{format_ts(e.start, hours)} {name}")
    return "\n".join(lines) + "\n"


def youtube_warnings(items: list[Entry]) -> list[str]:
    """Motivos pelos quais o YouTube não criaria capítulos (a lista ainda funciona como texto)."""
    w = []
    if len(items) < 3:
        w.append("O YouTube só cria capítulos com 3 ou mais músicas; a lista aparece como texto "
                 "com links de tempo clicáveis.")
    short = [e for e in items if math.floor(e.end) - math.floor(e.start) < 10]
    if short:
        w.append("Trechos com menos de 10 s impedem os capítulos do YouTube: "
                 + ", ".join(f"#{e.index}" for e in short) + ".")
    return w


def project_text(project, numbered: bool = False) -> str:
    ex = project.export
    return render_text(entries(project.tracks, project.audio, ex.tracklist_mark), numbered)
