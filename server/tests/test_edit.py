"""Recursos de edição: overlays animados, efeitos sonoros, transições e decisões da direção."""
import json
import threading
import wave

import numpy as np
import pytest
from PIL import Image

from app.directions.classico import overlays as ov
from app.models import ProductionConfig
from app.pipeline import direct
from app.pipeline.render import anim, ffmpeg
from app.pipeline.render.compose import SFX_RATE, mix_sfx
from app.pipeline.render.subtitles import write_ass
from app.providers import sfx as sfx_library
from app.providers.sfx import synth

STYLE = {"font": "Arial Bold", "color_primary": "#FFFFFF", "color_accent": "#E63946"}


# ---------------------------------------------------------------- contador animado
@pytest.mark.parametrize("text,final,start", [
    ("47 testigos", "47 testigos", "0 testigos"),
    ("$200 Billion a Year", "$200 Billion a Year", "$0 Billion a Year"),
    ("1.500 km", "1.500 km", "0 km"),
    ("12,5% do PIB", "12,5% do PIB", "0,0% do PIB"),
])
def test_contador_preserva_o_formato(text, final, start):
    c = anim.find_counter(text)
    assert c is not None
    assert anim.counter_text(text, c, 1.0) == final
    assert anim.counter_text(text, c, 0.0) == start


def test_anos_nao_viram_contador():
    assert anim.find_counter("1987 — Ohio") is None
    assert anim.find_counter("Sonora, México") is None
    assert anim.find_counter("1 vez") is None


# ---------------------------------------------------------------- templates animados
@pytest.mark.parametrize("kind,o", [
    ("highlight", {"text": "47 testigos", "start": 0, "end": 3.5}),
    ("chapter", {"text": "La noche", "start": 0, "end": 3.0, "index": 2}),
    ("place_card", {"text": "Ohio, 1987", "start": 0, "end": 3.5}),
    ("quote", {"text": "nadie volvió a verlo", "start": 0, "end": 4.0, "word_times": [0.3, 0.6, 0.9, 1.2]}),
    ("light_leak", {"text": "", "start": 0, "end": 1.6}),
])
def test_templates_desenham_quadros_transparentes(kind, o):
    frame = ov.ANIMATED[kind](o, STYLE)
    first, mid = frame(0.0), frame((o["end"] - o["start"]) / 2)
    assert first.size == mid.size == (1920, 1080) and mid.mode == "RGBA"
    assert mid.getchannel("A").getextrema()[1] > 0  # algo visível no meio da animação
    assert frame(o["end"] - o["start"]).getchannel("A").getextrema()[1] < 255 or kind == "chapter"


def test_citacao_revela_palavras_no_tempo_da_fala():
    o = {"text": "uno dos tres", "start": 0, "end": 4.0, "word_times": [0.5, 1.5, 2.5]}
    frame = ov.quote(o, STYLE)
    a1 = np.asarray(frame(1.0).getchannel("A"), dtype=np.int64).sum()
    a2 = np.asarray(frame(3.0).getchannel("A"), dtype=np.int64).sum()
    assert a2 > a1  # mais palavras visíveis depois


def test_cliques_da_maquina_de_escrever_seguem_os_caracteres():
    o = {"text": "Ohio, 1987", "start": 0, "end": 3.5}
    cues = ov.SFX["place_card"](o)
    assert len(cues) == len(o["text"].replace(" ", "")) and all(c == "click" for c, _ in cues)
    assert [t for _, t in cues] == sorted(t for _, t in cues)


# ---------------------------------------------------------------- efeitos sonoros
@pytest.mark.parametrize("cat", synth.CATEGORIES)
def test_sons_sintetizados(tmp_path, cat):
    dest = synth.generate(cat, 0, tmp_path / f"{cat}.wav")
    with wave.open(str(dest)) as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        assert w.getframerate() == synth.SR
    assert 0.03 < len(data) / synth.SR < 4 and np.abs(data).max() > 20000
    assert synth.generate(cat, 0, tmp_path / "b.wav").read_bytes() == dest.read_bytes()  # determinístico


def test_biblioteca_sem_sons_usa_sintetizados_e_indexa_arquivos_manuais():
    snd = sfx_library.pick("whoosh", 0, [])
    assert snd["source"] == "synth" and snd["file"].endswith(".wav")
    manual = sfx_library._dir() / "impact" / "meu_impacto.wav"
    manual.parent.mkdir(exist_ok=True)
    synth.generate("impact", 1, manual)
    sounds = sfx_library.load()
    assert any(s["file"] == "impact/meu_impacto.wav" and s["source"] == "manual" for s in sounds)
    assert sfx_library.pick("impact", 5, sounds)["file"].endswith("meu_impacto.wav")


def test_mix_posiciona_cada_som_pelo_alinhamento(tmp_path):
    click = np.zeros(4800, np.float32)
    click[0] = 1.0
    swell = np.zeros(48000, np.float32)
    swell[24000] = 1.0  # pico no meio
    sounds = {"a": click, "b": swell, "c": click}

    def load(path, cache):
        return sounds[path]

    events = [{"file": "a", "category": "click", "start": 1.0, "align": "start", "gain_db": 0},
              {"file": "b", "category": "whoosh", "start": 3.0, "align": "peak", "gain_db": 0},
              {"file": "c", "category": "impact", "start": 5.0, "align": "end", "gain_db": 0}]
    out = mix_sfx(events, 6.0, tmp_path / "bed.wav", load=load)
    with wave.open(str(out)) as w:
        bed = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    hits = sorted(np.flatnonzero(bed > 30000) / SFX_RATE)
    assert hits == pytest.approx([1.0, 3.0, 5.0 - 0.1], abs=0.002)


def test_espacamento_minimo_entre_efeitos():
    cues = [("whoosh", 10.0, "peak"), ("swoosh_soft", 10.5, "start"), ("riser", 10.6, "end"),
            ("click", 10.7, "start"), ("impact", 12.0, "start")]
    out = direct.build_sfx(cues, {"sfx_min_gap_seconds": 1.2}, [])
    assert [e["category"] for e in out] == ["whoosh", "riser", "click", "impact"]


def test_legenda_some_durante_a_citacao(tmp_path):
    words = [{"text": t, "start": i * 0.5, "end": i * 0.5 + 0.4} for i, t in enumerate(
        "Primera frase aquí. Segunda frase dicha. Tercera frase final.".split())]
    out = write_ass(words, {"font": "Arial", "size": 54, "color": "#FFFFFF", "outline_color": "#000000",
                            "outline": 3, "position": "bottom", "margin_v": 70}, tmp_path / "s.ass", mute=[[1.6, 2.9]])
    text = out.read_text(encoding="utf-8-sig")
    assert "Primera" in text and "Tercera" in text and "Segunda" not in text and "\\fad(80,60)" in text


# ---------------------------------------------------------------- citação × narração
WORDS = [{"text": w, "start": 10 + i * 0.4, "end": 10 + i * 0.4 + 0.3} for i, w in enumerate(
    "con la firmeza de quien ha aprendido que la vida no regala nada.".split())]


def test_citacao_literal_ganha_o_tempo_de_cada_palavra():
    times = direct.quote_word_times("quien ha aprendido que la vida no regala nada", WORDS, 10, 16)
    assert times is not None and times[0] == pytest.approx(11.6) and len(times) == 9


def test_citacao_parafraseada_e_descartada():
    assert direct.quote_word_times("a vida é dura para todos nós", WORDS, 10, 16) is None


# ---------------------------------------------------------------- direção ponta a ponta
class Ctx:
    production_id = 1
    channel_id = 1

    def __init__(self, d):
        self.dir = d
        self.config = ProductionConfig(title="t", channel_name="c", language="es", video_language="es")
        self.config.music.enabled = False
        self.issues = []
        self.render_lock = threading.Semaphore(1)

    def path(self, *parts):
        p = self.dir.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def read_json(self, name):
        return json.loads(self.path(name).read_text(encoding="utf-8"))

    def write_json(self, name, data):
        p = self.path(name)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return p

    def issue(self, code, message, **kw):
        self.issues.append(code)

    def record_llm(self, *a, **k):
        pass


def _scene(i, start, end, **kw):
    base = {"id": f"s{i:03d}", "start": start, "end": end, "text": "", "source": "ai", "chapter_break": False,
            "chapter_title": None, "highlight": None, "emphasis": "none", "quote": None,
            "context": {"id": "ctx1", "setting_type": "historical", "footage_feasibility": "early_film",
                        "card_text": "Ohio, 1887"}}
    base.update(kw)
    return base


def test_direcao_traduz_as_intencoes_em_recursos(tmp_path, monkeypatch):
    monkeypatch.setattr(direct, "write_report", lambda ctx: None)
    monkeypatch.setattr(direct, "write_visual_report", lambda ctx: None)
    (tmp_path / "assets").mkdir()
    Image.new("RGB", (1920, 1080), "gray").save(tmp_path / "assets" / "wide.png")
    Image.new("RGB", (800, 1000), "gray").save(tmp_path / "assets" / "tall.png")
    ctx2 = {"id": "ctx2", "setting_type": "historical", "footage_feasibility": "early_film", "card_text": "Texas"}
    scenes = [
        _scene(1, 0, 8, emphasis="calm"),
        _scene(2, 8, 20, emphasis="calm"),
        _scene(3, 20, 28, highlight="47 testigos"),  # 20 s depois do card: respeita o intervalo mínimo
        _scene(4, 28, 36, chapter_break=True, chapter_title="La noche"),
        _scene(5, 36, 44, context=ctx2),
        _scene(6, 44, 52, emphasis="reveal", context=ctx2),
        _scene(7, 52, 64, quote="quien ha aprendido que la vida no regala nada", context=ctx2),
    ]
    selection = {s["id"]: {"source": "ai_image", "asset": "assets/wide.png", "is_image": True} for s in scenes}
    selection["s003"] = {"source": "archive", "asset": "assets/tall.png", "is_image": True}
    words = [{"text": w["text"], "start": w["start"] + 44, "end": w["end"] + 44} for w in WORDS]
    ctx = Ctx(tmp_path)
    ctx.write_json("plan.json", {"scenes": scenes})
    ctx.write_json("selection.json", selection)
    ctx.write_json("transcript.json", {"words": words})
    direct.run(ctx)
    tl = ctx.read_json("timeline.json")
    trans = [s["transition_in"]["type"] for s in tl["scenes"]]
    assert trans == ["cut", "fade", "cut", "fadeblack", "fade", "fadewhite", "cut"]
    assert tl["scenes"][2]["frame"] == "photo"
    kinds = [o["type"] for o in tl["overlays"]]
    assert kinds == ["place_card", "highlight", "chapter", "light_leak", "place_card", "quote"]
    assert tl["overlays"][2]["index"] == 1
    quote = tl["overlays"][-1]
    assert len(quote["word_times"]) == 9 and tl["subtitles"]["mute"] == [[quote["start"], quote["end"]]]
    cats = {e["category"] for e in tl["audio"]["sfx"]}
    assert {"click", "swoosh_soft", "impact", "whoosh", "riser"} <= cats
    assert tl["look"] == {"grain": 3, "vignette": "PI/5"}


def test_direcao_sem_efeitos_nem_textura(tmp_path, monkeypatch):
    monkeypatch.setattr(direct, "write_report", lambda ctx: None)
    monkeypatch.setattr(direct, "write_visual_report", lambda ctx: None)
    (tmp_path / "assets").mkdir()
    Image.new("RGB", (1920, 1080), "gray").save(tmp_path / "assets" / "a.png")
    ctx = Ctx(tmp_path)
    ctx.config.sfx = False
    ctx.config.film_look = False
    ctx.write_json("plan.json", {"scenes": [_scene(1, 0, 8, emphasis="reveal"), _scene(2, 8, 16, emphasis="reveal")]})
    ctx.write_json("selection.json", {f"s00{i}": {"source": "ai_image", "asset": "assets/a.png", "is_image": True}
                                      for i in (1, 2)})
    direct.run(ctx)
    tl = ctx.read_json("timeline.json")
    assert tl["audio"]["sfx"] == [] and tl["look"] is None


# ---------------------------------------------------------------- FFmpeg (pulado sem FFmpeg instalado)
needs_ffmpeg = pytest.mark.skipif(not ffmpeg.available(), reason="FFmpeg não instalado")


@needs_ffmpeg
def test_overlay_animado_vira_video_com_alfa(tmp_path):
    out = anim.encode(ov.highlight({"text": "47 testigos", "start": 0, "end": 0.5}, STYLE), 0.5, tmp_path / "o.mkv")
    info = ffmpeg.probe(out)
    v = info["streams"][0]
    assert v["codec_name"] == "ffv1" and v["pix_fmt"] == "bgra" and int(v["width"]) == 1920
