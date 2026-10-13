"""Testes das partes críticas: playlist, duração, sincronia, determinismo, estilos, FFmpeg.

Rodar:  uv run python -m unittest discover -s tests -v
(usa QT_QPA_PLATFORM=offscreen automaticamente; precisa do ffmpeg no PATH)
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import unittest
from fractions import Fraction
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# preferências isoladas: os testes nunca alteram ~/.config/wavegen
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="wavegen-iso-")

import numpy as np  # noqa: E402

from wavegen import playlist  # noqa: E402
from wavegen.analysis import Response, SeriesProvider, analyze_pcm  # noqa: E402
from wavegen.export import (EncodeJob, PRESETS, build_encode_command, estimate_size,  # noqa: E402
                            fps_of, resolution)
from wavegen.ffmpeg import ffmpeg_path, media_arg, probe_streams  # noqa: E402
from wavegen.playlist import AudioSettings, Track, ensure_mix, open_pcm, total_duration  # noqa: E402
from wavegen.project import Box, Project  # noqa: E402

SR = 48000
TMP = Path(tempfile.mkdtemp(prefix="wavegen-test-"))


def make_tone(name: str, seconds: float, freq: float = 440.0, amp: float = 0.5,
              codec: str = "pcm_s16le") -> Path:
    """Gera um arquivo de áudio de teste com o próprio ffmpeg."""
    out = TMP / name
    expr = f"{amp}*sin(2*PI*{freq}*t)"
    subprocess.run([ffmpeg_path(), "-v", "error", "-y", "-f", "lavfi",
                    "-i", f"aevalsrc={expr}|{expr}:s={SR}:d={seconds}",
                    "-c:a", codec, media_arg(out)], check=True)
    return out


def load(p: Path) -> Track:
    return Track.load(str(p))


class CacheIsolation(unittest.TestCase):
    """Cada teste usa um cache próprio (não mexe em ~/.cache/wavegen)."""

    def setUp(self):
        self.cache = Path(tempfile.mkdtemp(dir=TMP))
        self._p = mock.patch.object(playlist, "CACHE_DIR", self.cache)
        self._p.start()

    def tearDown(self):
        self._p.stop()


# ============================================================================ playlist
class TestPlaylistMath(unittest.TestCase):
    def test_sequential_sum(self):
        self.assertAlmostEqual(total_duration([10, 20, 30], AudioSettings()), 60)

    def test_gap(self):
        self.assertAlmostEqual(total_duration([10, 20, 30], AudioSettings(gap=2)), 64)

    def test_crossfade_overrides_gap(self):
        self.assertAlmostEqual(total_duration([10, 20, 30], AudioSettings(crossfade=3, gap=2)), 54)

    def test_crossfade_limited_to_half_shortest(self):
        # 4 s não cabe numa faixa de 4 s: limitado a 2 s
        self.assertAlmostEqual(total_duration([4, 20], AudioSettings(crossfade=4)), 22)

    def test_single_track_ignores_transitions(self):
        self.assertAlmostEqual(total_duration([10], AudioSettings(crossfade=3, gap=2)), 10)

    def test_shuffle_is_deterministic(self):
        tr = [Track(f"/x/{i}.mp3", 1) for i in range(8)]
        st = AudioSettings(sequential=False, shuffle_seed=5)
        a = [t.path for t in playlist.ordered(tr, st)]
        b = [t.path for t in playlist.ordered(tr, st)]
        self.assertEqual(a, b)
        self.assertNotEqual(a, [t.path for t in tr])
        self.assertEqual(sorted(a), sorted(t.path for t in tr))


class TestMix(CacheIsolation):
    @classmethod
    def setUpClass(cls):
        cls.a = make_tone("faixa A (ç ã) -teste.wav", 2.0, 440, 0.5)
        cls.b = make_tone("faixa:B.flac", 3.0, 660, 0.25, codec="flac")
        cls.c = make_tone("faixa C.mp3", 1.5, 330, 0.5, codec="libmp3lame")

    def mix(self, files, **kw):
        st = AudioSettings(**kw)
        tracks = [load(f) for f in files]
        path = ensure_mix(tracks, st)
        pcm, sr = open_pcm(path)
        return np.asarray(pcm), sr, tracks, st

    def test_concat_exact_length_no_extra_silence(self):
        pcm, sr, tracks, st = self.mix([self.a, self.b])
        self.assertEqual(sr, SR)
        self.assertEqual(pcm.shape[0], 5 * SR)               # amostra exata, nada cortado/somado
        # sem silêncio no início, na junção e no fim
        self.assertGreater(np.abs(pcm[:480]).max(), 0.1)
        self.assertGreater(np.abs(pcm[2 * SR - 480:2 * SR + 480]).max(), 0.1)
        self.assertGreater(np.abs(pcm[-480:]).max(), 0.05)

    def test_no_normalization(self):
        pcm, *_ = self.mix([self.a, self.b])
        # níveis originais preservados (0,5 e 0,25), sem normalização/compressão
        self.assertAlmostEqual(float(np.abs(pcm[:2 * SR]).max()), 0.5, delta=0.01)
        self.assertAlmostEqual(float(np.abs(pcm[2 * SR:]).max()), 0.25, delta=0.01)

    def test_samples_match_source(self):
        pcm, *_ = self.mix([self.a])
        src = subprocess.run([ffmpeg_path(), "-v", "error", "-i", media_arg(self.a), "-f", "f32le",
                              "-ac", "2", "-"], capture_output=True, check=True).stdout
        src = np.frombuffer(src, "<f4").reshape(-1, 2)
        self.assertEqual(src.shape, pcm.shape)
        self.assertLess(float(np.abs(src - pcm).max()), 1e-6)  # bit a bit (WAV 48k → float)

    def test_gap_inserts_silence(self):
        pcm, *_ = self.mix([self.a, self.b], gap=1.0)
        self.assertEqual(pcm.shape[0], 6 * SR)
        mid = pcm[2 * SR + 2400:3 * SR - 2400]
        self.assertLess(float(np.abs(mid).max()), 1e-6)

    def test_crossfade_length(self):
        pcm, sr, tracks, st = self.mix([self.a, self.b], crossfade=1.0)
        self.assertEqual(pcm.shape[0], 4 * SR)
        self.assertAlmostEqual(total_duration([t.duration for t in tracks], st), 4.0, places=2)

    def test_mp3_and_three_tracks(self):
        pcm, sr, tracks, st = self.mix([self.a, self.c, self.b])
        exp = total_duration([t.duration for t in tracks], st)
        self.assertLess(abs(pcm.shape[0] / SR - exp), 0.06)  # mp3 tem atraso de codec pequeno

    def test_fades_and_volume(self):
        pcm, *_ = self.mix([self.a], fade_in=0.5, fade_out=0.5, volume_db=-6.0)
        self.assertLess(float(np.abs(pcm[:48]).max()), 0.01)
        self.assertLess(float(np.abs(pcm[-48:]).max()), 0.01)
        self.assertAlmostEqual(float(np.abs(pcm[SR - 2400:SR + 2400]).max()), 0.5 * 10 ** (-6 / 20),
                               delta=0.01)

    def test_cache_reused(self):
        t = [load(self.a)]
        p1 = ensure_mix(t, AudioSettings())
        with mock.patch.object(playlist.subprocess, "Popen", side_effect=AssertionError("refez a trilha")):
            p2 = ensure_mix(t, AudioSettings())       # vem do cache: o FFmpeg não roda de novo
        self.assertEqual(p1, p2)
        self.assertNotEqual(p1, ensure_mix(t, AudioSettings(volume_db=-1)))

    def test_missing_file(self):
        from wavegen.ffmpeg import WavegenError
        with self.assertRaises(WavegenError) as cm:
            ensure_mix([Track(str(TMP / "nao existe.mp3"), 1)], AudioSettings())
        self.assertEqual(cm.exception.code, "missing_file")

    def test_corrupt_audio(self):
        from wavegen.ffmpeg import WavegenError
        bad = TMP / "corrompido.mp3"
        bad.write_bytes(b"isto nao e audio" * 100)
        with self.assertRaises(WavegenError):
            Track.load(str(bad))


# ============================================================================ lista de faixas
class TestTracklist(CacheIsolation):
    def setUp(self):
        super().setUp()
        from wavegen.tracklist import entries, format_ts, render_text, youtube_warnings
        self.entries, self.fmt, self.text, self.warn = entries, format_ts, render_text, youtube_warnings

    def tr(self, durs):
        return [Track(f"/m/Faixa_{i + 1}.mp3", d) for i, d in enumerate(durs)]

    def test_sequential_and_gap(self):
        e = self.entries(self.tr([200, 185.5, 300]), AudioSettings())
        self.assertEqual([x.start for x in e], [0, 200, 385.5])
        e = self.entries(self.tr([200, 185.5, 300]), AudioSettings(gap=2))
        self.assertEqual([x.start for x in e], [0, 202, 389.5])
        self.assertEqual(self.text(e), "0:00 Faixa 1\n3:22 Faixa 2\n6:29 Faixa 3\n")

    def test_crossfade_marks(self):
        st = AudioSettings(crossfade=6)
        self.assertEqual([x.start for x in self.entries(self.tr([200, 180, 240]), st)], [0, 194, 368])
        self.assertEqual([x.start for x in self.entries(self.tr([200, 180, 240]), st, "middle")],
                         [0, 197, 371])

    def test_order_follows_shuffle(self):
        tracks = self.tr([100 + i for i in range(6)])
        st = AudioSettings(sequential=False, shuffle_seed=3)
        e = self.entries(tracks, st)
        self.assertEqual([x.track.path for x in e], [t.path for t in playlist.ordered(tracks, st)])

    def test_hour_format_and_rounding(self):
        e = self.entries(self.tr([1800, 1799.6, 600]), AudioSettings())
        self.assertEqual(self.text(e).splitlines(), ["0:00:00 Faixa 1", "0:30:00 Faixa 2", "0:59:59 Faixa 3"])
        self.assertEqual(self.fmt(59.999, False), "0:59")              # nunca marca depois

    def test_warnings(self):
        self.assertTrue(self.warn(self.entries(self.tr([100, 100]), AudioSettings())))
        self.assertTrue(self.warn(self.entries(self.tr([100, 8, 100]), AudioSettings())))
        self.assertFalse(self.warn(self.entries(self.tr([100, 100, 100]), AudioSettings())))

    def test_titles_from_tags_and_saved(self):
        f = TMP / "com tags.mp3"
        subprocess.run([ffmpeg_path(), "-v", "error", "-y", "-f", "lavfi", "-i", "sine=d=1",
                        "-metadata", "artist=Noisia", "-metadata", "title=Machine Gun",
                        media_arg(f)], check=True)
        t = load(f)
        self.assertEqual(t.display_title, "Noisia - Machine Gun")
        self.assertEqual(Track("/x/Rote_Linien  (edit).m4a").display_title, "Rote Linien (edit)")
        p = Project()
        p.tracks = [t]
        t.title = "Meu título"
        path = TMP / "titulos.wavegen.json"
        p.save(path)
        self.assertEqual(Project.load(path).tracks[0].display_title, "Meu título")

    def test_times_match_real_mix(self):
        """A troca de música no áudio montado acontece no tempo que a lista mostra."""
        files = [make_tone("tl1.wav", 11.3, 300, 0.5), make_tone("tl2.flac", 12.7, 1200, 0.5, "flac"),
                 make_tone("tl3.wav", 10.0, 4000, 0.5)]
        tracks = [load(f) for f in files]
        for st in (AudioSettings(), AudioSettings(gap=1.5)):
            pcm, sr = open_pcm(ensure_mix(tracks, st))
            pcm = np.asarray(pcm)[:, 0]
            for e in self.entries(tracks, st)[1:]:
                n = int(round(e.start * sr))
                before = pcm[n - 2400:n - 240]
                after = pcm[n + 240:n + 2400]
                # frequência dominante muda exatamente no tempo marcado (±5 ms)
                fb = np.argmax(np.abs(np.fft.rfft(before, 48000)))
                fa = np.argmax(np.abs(np.fft.rfft(after, 48000)))
                self.assertNotEqual(fb, fa, f"{st} entrada {e.index}")
                self.assertLess(abs(int(fa) - [300, 1200, 4000][e.index - 1]), 30)


# ============================================================================ análise / sincronia
def click_track(seconds: float, clicks: list[float]) -> np.ndarray:
    pcm = np.zeros((int(seconds * SR), 2), np.float32)
    rng = np.random.default_rng(1)
    for c in clicks:
        s = int(c * SR)
        pcm[s:s + int(0.03 * SR)] = rng.uniform(-0.9, 0.9, (int(0.03 * SR), 1))
    return pcm


class TestAnalysisSync(unittest.TestCase):
    def test_frame_count_matches_duration(self):
        for fps in (Fraction(30), Fraction(60), Fraction(30000, 1001)):
            f = analyze_pcm(click_track(10.0, []), SR, fps)
            self.assertLessEqual(abs(f.n_frames - 10.0 * float(fps)), 1)

    def test_clicks_land_on_right_frame(self):
        clicks = [1.0, 2.5, 4.0, 7.3]
        for fps in (Fraction(30), Fraction(60)):
            f = analyze_pcm(click_track(9.0, clicks), SR, fps)
            lvl = f.level_db
            for c in clicks:
                n = int(round(c * float(fps)))
                lo, hi = max(0, n - 3), n + 4
                peak = lo + int(np.argmax(lvl[lo:hi]))
                # o pico de energia cai no quadro do clique (±1 quadro = ±33 ms a 30 fps)
                self.assertLessEqual(abs(peak - n), 1, f"fps={fps} clique={c}s quadro={peak} esperado={n}")
                self.assertGreater(lvl[peak], lvl[max(0, n - 10)] + 20)

    def test_bands_follow_frequency(self):
        t = np.arange(4 * SR) / SR
        lowp = np.stack([0.5 * np.sin(2 * np.pi * 80 * t)] * 2, 1).astype(np.float32)
        highp = np.stack([0.5 * np.sin(2 * np.pi * 6000 * t)] * 2, 1).astype(np.float32)
        fl = analyze_pcm(lowp, SR, Fraction(30))
        fh = analyze_pcm(highp, SR, Fraction(30))
        n = 60
        self.assertLess(int(np.argmax(fl.band_db[n])), int(np.argmax(fh.band_db[n])))


class TestDeterminism(unittest.TestCase):
    def test_series_independent_of_access_order(self):
        rng = np.random.default_rng(3)
        pcm = (rng.standard_normal((30 * SR, 2)) * 0.2).astype(np.float32)
        pcm *= (0.5 + 0.5 * np.sin(np.arange(30 * SR) / SR * 3))[:, None]
        f = analyze_pcm(pcm, SR, Fraction(30))
        r = Response()
        seq = SeriesProvider(f, 48, r)
        a = [seq.frame(n)[0].copy() for n in range(f.n_frames)]
        rnd = SeriesProvider(f, 48, r)
        order = rng.permutation(f.n_frames)
        b = {int(n): rnd.frame(int(n))[0].copy() for n in order}
        for n in range(f.n_frames):
            np.testing.assert_array_equal(a[n], b[n])

    def test_analysis_repeatable(self):
        pcm = click_track(5.0, [1, 2, 3])
        f1 = analyze_pcm(pcm, SR, Fraction(30))
        f2 = analyze_pcm(pcm, SR, Fraction(30))
        np.testing.assert_array_equal(f1.band_db, f2.band_db)
        np.testing.assert_array_equal(f1.level_db, f2.level_db)


# ============================================================================ estilos
class TestStyles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtGui import QGuiApplication
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        from wavegen.gui.demo import demo_features, demo_pcm
        cls.feats = demo_features(Fraction(30))
        cls.pcm = demo_pcm()

    def render(self, sid: str, n: int = 95, params: dict | None = None):
        from wavegen.compositor import Compositor
        p = Project()
        p.style = sid
        if params:
            p.style_params[sid] = params
        c = Compositor(p, 480, 270, self.feats, self.pcm, transparent=True)
        img = c.render(n)
        from PySide6.QtGui import QImage
        img = img.convertToFormat(QImage.Format_ARGB32)
        return np.frombuffer(bytes(img.constBits()), np.uint8).reshape(270, 480, 4).copy()

    def test_ten_distinct_styles(self):
        from wavegen.styles import style_list
        ids = [s.id for s in style_list()]
        self.assertGreaterEqual(len(ids), 10)
        self.assertEqual(len(ids), len(set(ids)))
        alphas = {}
        for sid in ids:
            a = self.render(sid)[..., 3]
            self.assertGreater(int((a > 0).sum()), 300, f"{sid} não desenhou nada")
            alphas[sid] = a > 32
        # cada par de estilos deve diferir visualmente (forma, não só cor)
        for i, x in enumerate(ids):
            for y in ids[i + 1:]:
                inter = np.logical_and(alphas[x], alphas[y]).sum()
                union = np.logical_or(alphas[x], alphas[y]).sum()
                self.assertLess(inter / max(union, 1), 0.8, f"{x} e {y} parecem iguais")

    def test_styles_react_to_audio(self):
        from wavegen.styles import style_list
        for cls in style_list():
            a, b = self.render(cls.id, 95), self.render(cls.id, 104)
            self.assertFalse(np.array_equal(a, b), f"{cls.id} não muda com o áudio")

    def test_silence_draws_little(self):
        from wavegen.analysis import analyze_pcm as an
        from wavegen.compositor import Compositor
        sil = np.zeros((3 * SR, 2), np.float32)
        f = an(sil, SR, Fraction(30))
        for sid in ("bars", "mirror", "circle_spectrum"):
            p = Project()
            p.style = sid
            loud = (self.render(sid)[..., 3] > 0).sum()
            img = Compositor(p, 480, 270, f, sil, transparent=True).render(45)
            from PySide6.QtGui import QImage
            img = img.convertToFormat(QImage.Format_ARGB32)
            quiet = (np.frombuffer(bytes(img.constBits()), np.uint8).reshape(270, 480, 4)[..., 3] > 0).sum()
            self.assertLess(quiet, loud, sid)

    def test_params_change_output(self):
        a = self.render("bars", params={"color1": "#ff0000", "color2": "#ff0000", "gradient": False})
        b = self.render("bars", params={"color1": "#00ff00", "color2": "#00ff00", "gradient": False})
        m = a[..., 3] > 200
        self.assertGreater(a[..., 2][m].mean(), 200)      # BGRA: canal R
        self.assertGreater(b[..., 1][b[..., 3] > 200].mean(), 200)
        few = (self.render("bars", params={"bars": 8})[..., 3] > 0).sum()
        many = (self.render("bars", params={"bars": 128})[..., 3] > 0).sum()
        self.assertNotEqual(few, many)

    def test_preview_and_export_match(self):
        """Mesmo projeto em 2 resoluções: o desenho é proporcional (preview = vídeo final)."""
        from PySide6.QtGui import QImage
        from wavegen.compositor import Compositor
        p = Project()
        p.style = "mirror"
        p.boxes["mirror"] = Box(0.3, 0.7, 0.4, 0.3, 1.0)
        small = Compositor(p, 480, 270, self.feats, self.pcm, transparent=True).render(95)
        big = Compositor(p, 1920, 1080, self.feats, self.pcm, transparent=True).render(95)
        big = big.scaled(480, 270).convertToFormat(QImage.Format_ARGB32)
        small = small.convertToFormat(QImage.Format_ARGB32)
        sa = np.frombuffer(bytes(small.constBits()), np.uint8).reshape(270, 480, 4)[..., 3] > 64
        ba = np.frombuffer(bytes(big.constBits()), np.uint8).reshape(270, 480, 4)[..., 3] > 64
        iou = np.logical_and(sa, ba).sum() / max(np.logical_or(sa, ba).sum(), 1)
        self.assertGreater(iou, 0.6)
        ys, xs = np.nonzero(sa)
        self.assertAlmostEqual(xs.mean() / 480, 0.3, delta=0.05)  # posição respeitada
        self.assertAlmostEqual(ys.mean() / 270, 0.7, delta=0.06)


# ============================================================================ exportação
class TestEncodeCommand(unittest.TestCase):
    def test_presets(self):
        self.assertEqual(resolution("1080p"), (1920, 1080))
        self.assertEqual(resolution("720p"), (1280, 720))
        self.assertEqual(resolution("4k"), (3840, 2160))
        self.assertEqual(resolution("vertical"), (1080, 1920))
        self.assertEqual(resolution("square"), (1080, 1080))
        self.assertEqual(fps_of("30"), Fraction(30))
        self.assertIn("60", [k for k in __import__("wavegen.export").export.FPS_CHOICES])
        self.assertEqual(len(PRESETS), 5)

    def test_mp4_software(self):
        cmd = build_encode_command(EncodeJob(1920, 1080, Fraction(30), audio_path=Path("/a b/mix.wav")),
                                   Path("/saída/-meu vídeo.mp4"))
        s = " ".join(cmd)
        for part in ("libx264", "-crf 18", "yuv420p", "-c:a aac", "-b:a 320k", "+faststart", "-r 30/1"):
            self.assertIn(part, s)
        self.assertEqual(cmd[-1], "file:/saída/-meu vídeo.mp4")   # caminho seguro, nunca vira opção
        self.assertIn("file:/a b/mix.wav", cmd)

    def test_small_profile(self):
        s = " ".join(build_encode_command(EncodeJob(1280, 720, Fraction(60), quality="small",
                                                    audio_path=Path("m.wav")), Path("o.mp4")))
        self.assertIn("-crf 23", s)
        self.assertIn("-b:a 192k", s)

    def test_vaapi(self):
        s = " ".join(build_encode_command(EncodeJob(1920, 1080, Fraction(30), encoder="vaapi",
                                                    vaapi_device="/dev/dri/renderD128",
                                                    audio_path=Path("m.wav")), Path("o.mp4")))
        self.assertIn("h264_vaapi", s)
        self.assertIn("hwupload", s)
        self.assertIn("-vaapi_device /dev/dri/renderD128", s)

    def test_audio_copy(self):
        cmd = build_encode_command(EncodeJob(1920, 1080, Fraction(30), audio_copy_from=Path("x.m4a")),
                                   Path("o.mp4"))
        i = cmd.index("-c:a")
        self.assertEqual(cmd[i + 1], "copy")

    def test_estimate_size_scales(self):
        a = estimate_size("mp4", "high", "software", 1920, 1080, Fraction(30), 600)
        b = estimate_size("mp4", "high", "software", 1920, 1080, Fraction(30), 1200)
        c = estimate_size("mp4", "small", "software", 1920, 1080, Fraction(30), 600)
        self.assertLess(a[0], a[1])
        self.assertAlmostEqual(b[0] / a[0], 2, places=5)
        self.assertLess(c[1], a[1])


class TestProject(unittest.TestCase):
    def test_roundtrip_and_missing(self):
        img = TMP / "fundo ç.png"
        img.write_bytes(b"x")
        p = Project()
        p.background.image = str(img)
        p.tracks = [Track(str(TMP / "sumiu.mp3"), 12.5, "mp3")]
        p.style = "neon"
        p.style_params["neon"] = {"color1": "#123456"}
        p.boxes["neon"] = Box(0.2, 0.3, 0.4, 0.5, 1.2)
        p.audio.crossfade = 3
        p.export.preset = "vertical"
        path = TMP / "proj.wavegen.json"
        p.save(path)
        q = Project.load(path)
        self.assertEqual(q.to_dict(), p.to_dict())
        self.assertEqual(q.missing_files(), [str(TMP / "sumiu.mp3")])
        self.assertEqual(json.loads(path.read_text())["app"], "wavegen")


# ============================================================================ ponta a ponta
class TestRenderEndToEnd(CacheIsolation):
    """Renderiza vídeos curtos de verdade (FFmpeg) e confere o resultado."""

    @classmethod
    def setUpClass(cls):
        cls.a = make_tone("e2e A.wav", 2.0, 220, 0.6)
        cls.b = make_tone("e2e B.ogg", 2.5, 880, 0.4, codec="libvorbis")
        from PySide6.QtGui import QColor, QGuiApplication, QImage
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        img = QImage(800, 600, QImage.Format_RGB32)
        img.fill(QColor("#336699"))
        cls.img = TMP / "fundo 4x3.png"
        img.save(str(cls.img))

    def project(self, **export):
        p = Project()
        p.tracks = [load(self.a), load(self.b)]
        p.background.image = str(self.img)
        p.style = "bars"
        p.export.preset = "720p"
        for k, v in export.items():
            setattr(p.export, k, v)
        return p

    def run_job(self, p, out, workers=0):
        from wavegen.job import run_render
        events = []
        res = run_render(p, out, events.append, threading.Event(), workers=workers)
        return res, events

    def framemd5(self, path):
        r = subprocess.run([ffmpeg_path(), "-v", "error", "-i", media_arg(path), "-map", "0:v",
                            "-f", "framemd5", "-"], capture_output=True, text=True, check=True)
        return [ln.split(",")[-1].strip() for ln in r.stdout.splitlines() if not ln.startswith("#")]

    def test_full_render_duration_streams_and_determinism(self):
        out1 = TMP / "saída 1 [teste].mp4"
        res, ev = self.run_job(self.project(), out1, workers=1)
        self.assertTrue(out1.exists())
        info = probe_streams(out1)
        streams = {s["codec_type"]: s for s in info["streams"]}
        self.assertEqual(streams["video"]["codec_name"], "h264")
        self.assertEqual(streams["audio"]["codec_name"], "aac")
        self.assertEqual((streams["video"]["width"], streams["video"]["height"]), (1280, 720))
        self.assertEqual(streams["video"]["pix_fmt"], "yuv420p")
        vdur, adur = float(streams["video"]["duration"]), float(streams["audio"]["duration"])
        self.assertAlmostEqual(vdur, 4.5, delta=0.05)            # duração = trilha completa
        self.assertAlmostEqual(adur, 4.5, delta=0.05)
        self.assertLess(abs(vdur - adur), 0.05)                  # áudio e vídeo alinhados
        self.assertTrue(any(e.get("stage") == "render" for e in ev if e["type"] == "progress"))
        # mesma configuração com vários processos = quadros idênticos
        out2 = TMP / "saída 2.mp4"
        self.run_job(self.project(), out2, workers=3)
        self.assertEqual(self.framemd5(out1), self.framemd5(out2))
        self.assertFalse(list(TMP.glob(".*.part*")))             # nada parcial sobrando
        txt = out1.with_name(f"{out1.stem} - lista de faixas.txt")
        self.assertEqual(res["tracklist"], str(txt))
        self.assertEqual(txt.read_text(encoding="utf-8"), "0:00 e2e A\n0:02 e2e B\n")

    def test_background_not_distorted_and_visible(self):
        out = TMP / "fundo.mp4"
        self.run_job(self.project(), out)
        raw = subprocess.run([ffmpeg_path(), "-v", "error", "-ss", "0.5", "-i", media_arg(out),
                              "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True, check=True).stdout
        fr = np.frombuffer(raw, np.uint8).reshape(720, 1280, 3)
        corner = fr[20, 20].astype(int)
        self.assertLess(np.abs(corner - [0x33, 0x66, 0x99]).max(), 12)  # sem quadro preto

    def test_cancel_removes_partial(self):
        from wavegen.ffmpeg import WavegenError
        from wavegen.job import run_render
        cancel = threading.Event()
        out = TMP / "cancelado.mp4"

        def emit(ev):
            if ev["type"] == "progress" and ev.get("stage") == "render":
                cancel.set()
        with self.assertRaises(WavegenError) as cm:
            run_render(self.project(), out, emit, cancel, workers=2)
        self.assertEqual(cm.exception.code, "cancelled")
        self.assertFalse(out.exists())
        self.assertFalse(list(TMP.glob(".cancelado.part*")))

    def test_bad_output_dir(self):
        from wavegen.ffmpeg import WavegenError
        from wavegen.job import run_render
        with self.assertRaises(WavegenError) as cm:
            run_render(self.project(), Path("/nao/existe/x.mp4"), lambda e: None, threading.Event())
        self.assertEqual(cm.exception.code, "permission")


# ============================================================================ vídeo de fundo
def make_video(name: str, seconds: float, size: str = "320x240", rate: int = 24) -> Path:
    """Vídeo de teste: cada segundo tem uma cor/brilho diferente (fácil de identificar)."""
    out = TMP / name
    subprocess.run([ffmpeg_path(), "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c=black:s={size}:r={rate}:d={seconds},"
                    "geq=lum='mod(floor(T)*40+20,250)':cb=128:cr=128",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", media_arg(out)], check=True)
    return out


def gray_at(path: Path, t: float) -> float:
    """Brilho médio do canto superior esquerdo (onde não há visualizador) no instante t."""
    r = subprocess.run([ffmpeg_path(), "-v", "error", "-ss", f"{t:.3f}", "-i", media_arg(path),
                        "-vf", "crop=iw/8:ih/8:0:0,format=gray", "-frames:v", "1",
                        "-f", "rawvideo", "-"], capture_output=True, check=True)
    return float(np.frombuffer(r.stdout, np.uint8).mean())


class TestVideoBackground(CacheIsolation):
    """Fundo em vídeo: loop até o fim da trilha ou último quadro parado."""

    @classmethod
    def setUpClass(cls):
        cls.vid = make_video("fundo curto.mp4", 2.0)          # 2 s de vídeo
        cls.a = make_tone("vid A.wav", 2.5, 330, 0.5)
        cls.b = make_tone("vid B.wav", 2.5, 550, 0.5)          # trilha de 5 s
        from PySide6.QtGui import QGuiApplication
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def render(self, loop: bool, name: str) -> Path:
        from wavegen.job import run_render
        p = Project()
        p.tracks = [load(self.a), load(self.b)]
        p.background.image = str(self.vid)
        p.background.loop_video = loop
        p.style = "vinyl"
        p.export.preset = "720p"
        out = TMP / name
        run_render(p, out, lambda e: None, threading.Event(), workers=1)
        return out

    def test_probe_and_detection(self):
        from wavegen.video import is_video, probe_video
        self.assertTrue(is_video("x/clipe.MP4"))
        self.assertFalse(is_video("x/capa.png"))
        info = probe_video(str(self.vid))
        self.assertEqual((info.width, info.height), (320, 240))
        self.assertAlmostEqual(info.duration, 2.0, delta=0.1)

    def test_loop_repeats_until_end(self):
        out = self.render(True, "loop.mp4")
        st = {s["codec_type"]: s for s in probe_streams(out)["streams"]}
        self.assertAlmostEqual(float(st["video"]["duration"]), 5.0, delta=0.1)
        # 0,5 s, 2,5 s e 4,5 s caem no mesmo ponto do vídeo de 2 s
        g = [gray_at(out, t) for t in (0.5, 2.5, 4.5, 1.5)]
        self.assertLess(abs(g[0] - g[1]), 4)
        self.assertLess(abs(g[0] - g[2]), 4)
        self.assertGreater(abs(g[0] - g[3]), 15)            # segundos diferentes = cores diferentes

    def test_no_loop_freezes_last_frame(self):
        out = self.render(False, "sem loop.mp4")
        st = {s["codec_type"]: s for s in probe_streams(out)["streams"]}
        self.assertAlmostEqual(float(st["video"]["duration"]), 5.0, delta=0.1)
        g = [gray_at(out, t) for t in (1.5, 3.0, 4.7, 0.5)]
        self.assertLess(abs(g[0] - g[1]), 4)                # parado no último segundo
        self.assertLess(abs(g[0] - g[2]), 4)
        self.assertGreater(abs(g[0] - g[3]), 15)

    def test_video_sound_is_removed(self):
        """O som original do vídeo de fundo nunca entra no vídeo final (só as músicas)."""
        from wavegen.job import run_render
        vid = TMP / "fundo com som.mp4"
        subprocess.run([ffmpeg_path(), "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=gray:s=320x240:r=24:d=3", "-f", "lavfi", "-i",
                        "sine=f=1000:d=3,volume=4", "-c:v", "libx264", "-c:a", "aac",
                        "-shortest", media_arg(vid)], check=True)
        music = make_tone("musica 220.wav", 4.0, 220, 0.5)
        p = Project()
        p.tracks = [load(music)]
        p.background.image = str(vid)
        p.style = "bars"
        p.export.preset = "720p"
        out = TMP / "sem som do video.mp4"
        run_render(p, out, lambda e: None, threading.Event(), workers=1)
        streams = probe_streams(out)["streams"]
        self.assertEqual(sum(1 for s in streams if s["codec_type"] == "audio"), 1)
        r = subprocess.run([ffmpeg_path(), "-v", "error", "-i", media_arg(out), "-map", "0:a",
                            "-ac", "1", "-ar", "48000", "-f", "f32le", "-"],
                           capture_output=True, check=True)
        a = np.frombuffer(r.stdout, np.float32)
        sp = np.abs(np.fft.rfft(a))
        fr = np.fft.rfftfreq(len(a), 1 / 48000)
        band = lambda hz: sp[(fr > hz - 20) & (fr < hz + 20)].sum() / sp.sum()  # noqa: E731
        self.assertLess(band(1000), 0.005)          # apito do vídeo: ausente
        self.assertGreater(band(220), 0.5)          # música: presente

    def test_preview_decoder_matches_grid(self):
        from wavegen.project import BackgroundSettings
        from wavegen.video import VideoDecoder
        bg = BackgroundSettings(image=str(self.vid), loop_video=True)
        dec = VideoDecoder(bg, 160, 90, Fraction(30))
        try:
            def g(n):
                img = dec.frame(n)
                return np.frombuffer(img.constBits(), np.uint8).reshape(90, 160, 4)[..., :3].mean()
            a, b, c = g(15), g(75), g(45)                    # 0,5 s / 2,5 s (loop) / 1,5 s
            self.assertLess(abs(a - b), 4)
            self.assertGreater(abs(a - c), 15)
        finally:
            dec.close()


class TestTrackInfoOverlay(unittest.TestCase):
    """O compositor informa ao estilo qual faixa toca e há quanto tempo (nome + contador)."""

    def test_track_info_follows_tracklist(self):
        from wavegen.compositor import Compositor
        from wavegen.tracklist import entries
        a, b = make_tone("info A.wav", 3.0), make_tone("info B.wav", 2.0)
        p = Project()
        p.tracks = [load(a), load(b)]
        p.audio.gap = 0.5
        ents = entries(p.tracks, p.audio, mark="middle")
        comp = Compositor.__new__(Compositor)
        comp.feats = mock.Mock(duration=5.5, fps=Fraction(30))
        comp._init_tracks(p)
        from wavegen.styles import FrameData
        d = FrameData(t=1.0, n=30)
        comp._track_info(d)
        self.assertEqual((d.track_index, d.track_count), (1, 2))
        self.assertEqual(d.track_title, ents[0].title)
        self.assertAlmostEqual(d.track_pos, 1.0, places=3)
        d = FrameData(t=ents[1].start + 0.25, n=0)
        comp._track_info(d)
        self.assertEqual(d.track_index, 2)
        self.assertAlmostEqual(d.track_pos, 0.25, places=3)
        self.assertAlmostEqual(d.track_len, ents[1].end - ents[1].start, places=3)


# ============================================================================ player do preview
class FakeSD:
    """Imita o sounddevice: guarda as saídas abertas e chama os callbacks à mão."""

    class CallbackStop(Exception):
        pass

    def __init__(self):
        self.streams = []

    def OutputStream(self, callback, **kw):  # noqa: N802
        sd = self

        class S:
            def __init__(self):
                self.active = False
                self.closed = False

            def start(self):
                self.active = True
                sd.streams.append(self)

            def stop(self):
                self.active = False

            def close(self):
                self.active = False
                self.closed = True

            def pull(self, frames):
                if not self.active:
                    return
                out = np.zeros((frames, 2), np.float32)
                try:
                    callback(out, frames, None, None)
                except sd.CallbackStop:
                    self.active = False
        return S()

    def tick(self, frames=1024):
        for s in list(self.streams):
            s.pull(frames)

    def open_count(self):
        return sum(1 for s in self.streams if s.active)


class TestPreviewPlayer(unittest.TestCase):
    def make(self, seconds=1.0):
        from wavegen.gui.player import AudioPlayer
        pl = AudioPlayer()
        pl._sd = FakeSD()
        pl.error = None
        pl.set_source(np.zeros((int(48000 * seconds), 2), np.float32), 48000)
        return pl

    def play_to_end(self, pl):
        pl.play()
        for _ in range(500):
            if not pl.playing:
                break
            pl._sd.tick()
        self.assertFalse(pl.playing)

    def test_replay_after_end_keeps_normal_speed(self):
        """Bug: ao tocar de novo depois do fim, saídas antigas continuavam abertas e o
        áudio/preview acelerava (2×, 3×…) a cada repetição."""
        pl = self.make()
        for _ in range(4):
            self.play_to_end(pl)
            pl.play()
            self.assertLessEqual(pl._sd.open_count(), 1)
            before = pl.position()
            pl._sd.tick(1000)
            self.assertEqual(pl.position() - before, 1000)      # velocidade normal
            pl.pause()
        self.assertEqual(pl._sd.open_count(), 0)

    def test_play_twice_does_not_open_two_outputs(self):
        pl = self.make()
        pl.play()
        pl.play()
        self.assertEqual(pl._sd.open_count(), 1)
        pl.close()


class TestVinylQuality(unittest.TestCase):
    """Vinil com capa: barras com largura uniforme (alinhadas aos pixels) e capa nítida."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtGui import QGuiApplication
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def test_bars_have_uniform_width(self):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QImage, QPainter, Qt
        from wavegen.styles import FrameData, get_style
        st = get_style("vinyl")
        prm = st.resolve({"show_title": False, "show_counter": False})
        img = QImage(1000, 300, QImage.Format_ARGB32_Premultiplied)
        for t in (10.0, 10.013, 10.051):                     # várias posições de rolagem
            img.fill(Qt.transparent)
            d = FrameData(t=t, n=0)
            rng = np.random.default_rng(1)
            d.envelope_from = lambda t0, win, k: rng.uniform(0.0, 1.0, k).astype(np.float32)
            p = QPainter(img)
            p.setRenderHint(QPainter.Antialiasing)
            st._draw_wave(p, QRectF(300, 0, 680, 300), d, prm, 1.0, 300)
            p.end()
            al = np.frombuffer(img.constBits(), np.uint8).reshape(300, 1000, 4)[:, 300:980, 3]
            full = (al > 250).sum(axis=0)                     # pixels opacos por coluna
            runs, n, tall = [], 0, []
            for h in full:
                if h > 0:
                    n += 1
                    tall.append(h)
                elif n:
                    if max(tall) > 40:                        # só barras altas (não os pontos)
                        runs.append(sum(1 for x in tall if x > 0.8 * max(tall)))
                    n, tall = 0, []
            runs = runs[1:-1]                                 # ignora barras cortadas nas bordas
            self.assertGreater(len(runs), 8)
            self.assertLessEqual(max(runs) - min(runs), 0, f"larguras {sorted(set(runs))}")

    def test_cover_prescaled_with_area_filter(self):
        from PySide6.QtGui import QColor, QImage
        from wavegen.styles.vinyl import scaled_square
        # xadrez 1 px: reduzido 8× deve virar cinza médio (sem serrilhado/moiré)
        a = np.zeros((1200, 1600), np.uint8)
        a[(np.indices(a.shape).sum(0) % 2) == 0] = 255
        rgb = np.dstack([a, a, a, np.full_like(a, 255)])
        big = QImage(rgb.tobytes(), 1600, 1200, 1600 * 4, QImage.Format_RGB32).copy()
        out = scaled_square(big, 150)
        self.assertEqual((out.width(), out.height()), (150, 150))
        g = np.frombuffer(out.constBits(), np.uint8).reshape(150, 150, 4)[..., 0].astype(float)
        self.assertLess(abs(g.mean() - 127.5), 6)
        self.assertLess(g.std(), 6)


if __name__ == "__main__":
    unittest.main()
