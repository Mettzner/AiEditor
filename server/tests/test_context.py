"""CONTEXTO_PROFUNDO_DO_ROTEIRO.md: Bíblia de Contexto, estratégias por época, validação de anacronismos,
prompt de imagem de época, acervos históricos e unidade visual no render (offline, sem rede).

Os testes da §9 que dependem do Claude de verdade ficam em test_visual_live.py (AIEDITOR_LIVE=1).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.pipeline import context as C
from app.pipeline import direct
from app.pipeline import select as sel_mod
from app.pipeline.plan import Affinity, PlanWindow, SceneDraft, _validate
from app.pipeline.select.prerank import technical_filter
from app.pipeline.visual import (VISION_PROMPT, build_image_prompt, sanitize_free_query, sanitize_queries, score_row,
                                 vision_context)
from app.providers.stock import archives
from app.providers.stock.base import Candidate, Rendition

from .test_selector import FakeStock, env, issues, make_ctx, row, scene  # noqa: F401  (fixture e helpers)

IRELAND = {
    "id": "ctx1", "setting_type": "historical", "era": "1850", "era_label": "mid-19th century",
    "era_confidence": "explicit", "era_evidence": "In 1850", "place": "rural Ireland", "place_confidence": "explicit",
    "climate": "damp, overcast", "landscape": "green fields, stone walls", "society": "poor tenant farmers",
    "clothing": "wool coats, shawls, long skirts, flat caps", "architecture": "stone cottages with thatched roofs",
    "interiors": "earthen floor, open hearth, wooden table, iron pot", "lighting": "hearth fire, candles, oil lamps",
    "transport": "on foot, horse-drawn carts", "objects": ["iron pot", "wooden bucket", "potato baskets"],
    "era_markers_to_show": ["thatched roofs", "period wool clothing"],
    "anachronisms": ["cars", "power lines", "electric lights", "plastic", "asphalt", "modern clothing", "phones"],
    "search_vocabulary": ["victorian", "19th century", "period costume", "reenactment"], "palette": "earthy",
    "mood": "suffering", "footage_feasibility": "pre_film", "card_text": "Rural Ireland, 1850"}
LAB = {**IRELAND, "id": "ctx2", "setting_type": "contemporary", "era": "present day", "era_label": "contemporary",
       "place": "modern research laboratory", "anachronisms": ["period costume"], "search_vocabulary": ["laboratory"],
       "era_markers_to_show": [], "footage_feasibility": "contemporary", "card_text": ""}


def raw_bible(blocks=None) -> dict:
    blocks = blocks or [{**IRELAND, "first_unit": 0, "last_unit": 3}, {**LAB, "first_unit": 4, "last_unit": 5}]
    return {"topic": "potato famine", "audience": "adults", "tone": "somber", "region_culture": "rural Ireland",
            "visual_world": "farms", "contexts": blocks, "recurring_entities": [
                {"name": "the grandmother", "look": "elderly woman, wool shawl", "contexts": ["ctx1"]}],
            "recurring_places": [], "global_avoid": ["tractor"]}


def hist_scene(sid: str = "s001", **kw) -> dict:
    ctx = C.scene_context({**IRELAND})
    return {**scene(sid), "text": "In 1850, Irish farmers watched their potato crops rot in the fields.",
            "subject": "irish farmer", "visual_intent": "irish farmer in a muddy potato field, overcast",
            "queries": ["19th century irish farmer field", "irish farmer potato field", "irish farmer"],
            "archival_query": "irish famine engraving", "timeless_query": "hands rotten potato",
            "timeless_alternative": "close-up of hands holding a rotten potato", "context_id": "ctx1",
            "context": ctx, "anachronisms": ctx["anachronisms"], "must_avoid": ["tractor"],
            "strategy_order": C.strategy_order("pre_film"), "style_allowance": "real_preferred",
            "allowed_styles": ["real_footage", "painting_historical", "illustration"], **kw}


# ---------------------------------------------------------------- §3 viabilidade por época
@pytest.mark.parametrize("setting,era,label,expected", [
    ("historical", "1850", "", "pre_film"),
    ("historical", "1700s", "", "pre_photo"),
    ("historical", "medieval", "Middle Ages", "pre_photo"),
    ("historical", "", "mid-19th century", "pre_film"),
    ("historical", "1920s", "roaring twenties", "early_film"),
    ("historical", "1960s", "", "historical_modern"),
    ("contemporary", "present day", "", "contemporary"),
    ("timeless", "timeless", "", "timeless"),
])
def test_viabilidade_pela_epoca(setting, era, label, expected):
    assert C.feasibility_for(setting, era, label, "pre_film") == expected


def test_biblia_normalizada_cobre_todas_as_unidades():
    blocks = [{**IRELAND, "id": "ctx1", "first_unit": 0, "last_unit": 2, "footage_feasibility": "contemporary"},
              {**LAB, "id": "ctx1", "first_unit": 5, "last_unit": 9,
               "search_vocabulary": ["1850", "laboratory"]}]  # buraco, id repetido, ano no vocabulário
    out = C.finish_bible(raw_bible(blocks), n_units=8)
    tl = out["context_timeline"]
    assert [t["units"] for t in tl] == [[0, 2], [3, 7]] and [t["id"] for t in tl] == ["ctx1", "ctx2"]
    assert out["contexts"]["ctx1"]["footage_feasibility"] == "pre_film"  # o ano explícito vence o LLM
    assert out["contexts"]["ctx2"]["search_vocabulary"] == ["laboratory"]
    assert "tractor" in out["global_avoid"] and "watermarks" in out["global_avoid"]
    assert C.block_for_unit(out, 4)["id"] == "ctx2" and C.block_by_id(out, "ctx1")["place"] == "rural Ireland"


def test_biblia_neutra_e_atemporal_nunca_dia_atual():
    out = C.neutral_bible("t", "", 5)
    block = C.block_for_unit(out, 0)
    assert block["setting_type"] == "timeless" and block["footage_feasibility"] == "timeless"


def test_estrategias_por_epoca_e_preset():
    assert C.strategy_order("pre_film") == ["period_reenactment", "archival_art", "timeless", "ai_period"]
    assert C.strategy_order("early_film")[0] == "archival_film"
    assert "archival_art" not in C.strategy_order("pre_photo", "real_only")  # sem foto possível antes de 1840
    assert C.strategy_order("contemporary") == []
    allowance, allowed = C.period_allowed_styles("pre_film", "real_only", ["real_footage"], "real_preferred")
    assert allowance == "real_preferred" and {"painting_historical", "illustration"} <= set(allowed)
    assert C.period_allowed_styles("pre_film", "real_only", ["real_footage"], "real_only") == ("real_only",
                                                                                              ["real_footage"])


# ---------------------------------------------------------------- §4 brief da cena herda o contexto
def draft(first: int, last: int, **kw) -> SceneDraft:
    base = dict(first_unit=first, last_unit=last, literal=True, subject="irish farmer", subject_category="person",
                must_show=["potato field"], setting="muddy field", action="digging", shot="medium", mood="bleak",
                must_avoid=["tractor"], style_allowance="real_only", allowed_styles=["real_footage"],
                style_reason="real people", visual_intent="farmer digging", kind="concreto", energy="baixa",
                affinity=Affinity(stock=0.8, youtube=0.2, ai=0.5), ai_kind="image", chapter_break=False,
                chapter_title=None, highlight=None, overlay_language="en", context_id="",
                era_markers_to_show=["thatched roofs"], archival_query="", timeless_alternative="",
                timeless_query="", queries=["1850 irish farmer field", "irish farmer digging", "irish farmer"])
    base.update(kw)
    return SceneDraft(**base)


def test_cena_herda_o_bloco_e_ganha_vocabulario_de_epoca():
    bible = C.finish_bible(raw_bible(), n_units=6)
    window = PlanWindow(scenes=[
        draft(0, 1, archival_query="irish famine 1847 engraving", timeless_query="hands rotten potato",
              timeless_alternative="hands holding a rotten potato"),
        draft(2, 3), draft(4, 5, subject="scientist", queries=["scientist microscope lab"],
                           archival_query="should vanish")], music_mood=None)
    out = _validate(window, 0, 5, "real_preferred", bible).scenes
    a, b, lab = out
    assert a.context_id == b.context_id == "ctx1" and lab.context_id == "ctx2"
    assert all("1850" not in q for q in a.queries)  # anos fora das buscas
    assert any(v in q.split() for q in b.queries for v in ("victorian", "19th"))  # vocabulário de época
    assert a.archival_query == "irish famine engraving" and a.timeless_query == "hands rotten potato"
    assert b.archival_query and b.timeless_query == ""  # sem plano atemporal descrito → sem query
    assert {"painting_historical", "illustration"} <= set(a.allowed_styles)  # arte da época antes de 1890
    assert lab.archival_query == "" and lab.era_markers_to_show == []  # bloco atual


def test_queries_sem_anos():
    assert sanitize_queries("family", ["1850 family dinner", "1920s family photo"], []) == \
        ["family dinner", "1920s family photo", "family"]
    assert sanitize_free_query("hands peeling potato 1850 candlelight", ["tractor"]) == \
        "hands peeling potato candlelight"


# ---------------------------------------------------------------- §6 validação de época e lugar
def test_nota_com_contexto_de_epoca():
    good = row(1, 8.0)
    anach = row(1, 9.0).model_copy(update={"anachronisms_seen": ["power lines"]})
    off_era = row(1, 9.0).model_copy(update={"era_consistent": False})
    timeless = row(1, 8.0).model_copy(update={"era_consistent": False, "is_timeless": True})
    wrong_place = row(1, 9.0).model_copy(update={"place_consistent": False})
    hist = ("real_preferred", ["real_footage"], "historical")
    assert score_row(good, *hist) == 8.0
    assert score_row(anach, *hist) == 0  # qualquer anacronismo visto zera
    assert score_row(off_era, *hist) == 2.0
    assert score_row(timeless, *hist) == 8.0  # plano atemporal é válido
    assert score_row(off_era, "real_only", None, "contemporary") == 4.0
    assert score_row(wrong_place, "real_only", None, "contemporary") == 3.0
    assert score_row(anach, "real_only", None, "timeless") == 2.0
    assert score_row(anach, "real_only") == 9.0  # sem contexto (produções antigas): nada muda


def test_prompt_de_visao_traz_epoca_lugar_e_anacronismos():
    block = vision_context(hist_scene()["context"])
    assert 'Time period of this scene: "1850 (mid-19th century)". Place: "rural Ireland".' in block
    assert "Anachronisms that must NOT appear: cars, power lines" in block
    assert "lighting from hearth fire" in block
    assert '"anachronisms_seen":[]' in VISION_PROMPT.replace(" ", "") and "{context}" in VISION_PROMPT


# ---------------------------------------------------------------- §3/§5 cadeia de estratégias na seleção
class FakeArchives:
    def __init__(self, photos: int = 2):
        self.photos, self.calls = photos, []

    def search_photos(self, query, per_page=10, **kw):
        self.calls.append(("photo", query))
        return [Candidate(provider="wikimedia", external_id=f"{query[:8]}-{i}", title=f"{query} engraving 1847",
                          duration=0.0, width=1600, height=1100, page_url=f"https://commons/{i}", thumbnail="t",
                          renditions=[Rendition(f"https://upload/{i}.jpg", 1600, 1100)], query=query,
                          author="Illustrated London News", license="Public domain", is_image=True,
                          preview_frames=[f"https://upload/{i}-640.jpg"]) for i in range(self.photos)]

    def search(self, query, per_page=6, **kw):
        self.calls.append(("video", query))
        return []


def anachronism_vision(good_when):
    """Visão simulada: candidatos com anacronismo, exceto quando good_when(n, prompt) aprova a folha."""
    def mode(n, prompt, call):
        if good_when(n, prompt):
            return [row(1, 8.5).model_copy(update={"is_timeless": "hands rotten potato" in prompt})] + \
                [row(i, 5.0) for i in range(2, n + 1)]
        return [row(i, 9.0).model_copy(update={"anachronisms_seen": ["power lines"], "era_consistent": False})
                for i in range(1, n + 1)]
    return mode


def test_cena_historica_sem_reconstituicao_boa_vai_para_o_arquivo(env, monkeypatch):
    fake = FakeArchives()
    monkeypatch.setattr(sel_mod, "ARCHIVES", fake)
    prompts = []
    base = anachronism_vision(lambda n, p: n == 2)  # só a folha dos acervos (2 candidatos) é de época

    def mode(n, prompt, call):
        prompts.append(prompt)
        return base(n, prompt, call)

    env["vision"].mode = mode
    ctx = make_ctx()
    entry = sel_mod.Selector(ctx).select_scene(hist_scene())
    assert entry["provider"] == "wikimedia" and entry["strategy"] == "arquivo"
    assert entry["license"] == "Public domain" and entry["era_consistent"] is True
    assert ("photo", "irish famine engraving") in fake.calls  # query de arquivo, não a do assunto
    assert "Anachronisms that must NOT appear" in prompts[0]
    assert "power lines" in entry["must_avoid_used"] and "tractor" in entry["must_avoid_used"]


def test_sem_arquivo_cai_no_plano_atemporal_nunca_na_filmagem_moderna(env, monkeypatch):
    monkeypatch.setattr(sel_mod, "ARCHIVES", FakeArchives(photos=0))
    env["vision"].mode = anachronism_vision(lambda n, p: '"hands rotten potato"' in p)
    entry = sel_mod.Selector(make_ctx()).select_scene(hist_scene())
    assert entry["strategy"] == "atemporal" and entry["is_timeless"] is True
    assert entry["queries_used"] and entry["score"] >= 6.5


def test_tudo_anacronico_nao_usa_foto_generica_sem_validacao(env, monkeypatch):
    monkeypatch.setattr(sel_mod, "ARCHIVES", FakeArchives(photos=0))
    env["vision"].mode = anachronism_vision(lambda n, p: False)
    with pytest.raises(sel_mod.NoCandidate):
        sel_mod.Selector(make_ctx()).select_scene(hist_scene())


def test_cena_atual_segue_a_cadeia_normal(env, monkeypatch):
    fake = FakeArchives()
    monkeypatch.setattr(sel_mod, "ARCHIVES", fake)
    lab = {**scene(), "context": C.scene_context({**LAB}), "context_id": "ctx2"}
    entry = sel_mod.Selector(make_ctx()).select_scene(lab)
    assert entry["source_used"] == "stock" and "strategy" not in entry and not fake.calls


def test_sem_visao_texto_fraco_nao_entra_e_a_cadeia_segue(env, monkeypatch):
    """Produção 5: sem cota do Gemini, 'waterfall autumn' (nota de texto ~1) entrou para 'riendas de caballo'."""
    from app.pipeline import imagegen
    from app.providers.llm import gemini

    class Unrelated(FakeStock):
        def search(self, query, per_page=15, lang="en", **kw):
            return [Candidate(provider=self.id, external_id=f"w{i}", title="waterfall autumn background",
                              duration=30.0, width=1920, height=1080, page_url="", thumbnail=None,
                              renditions=[Rendition("u", 1920, 1080)], preview_frames=["f"]) for i in range(5)]

    monkeypatch.setattr(sel_mod, "enabled_providers", lambda: [Unrelated("pexels")])
    monkeypatch.setattr(gemini, "available", lambda: False)
    monkeypatch.setattr(imagegen, "generate_validated", lambda scene, brief, style, dest, cfg, stats, **kw: (
        dest.write_bytes(b"png"), imagegen.GeneratedImage(True, dest, "prompt", None, "", 1))[1])
    reins = {**scene(), "subject": "horse reins", "queries": ["horse reins leather", "horse reins hand"]}
    entry = sel_mod.Selector(make_ctx(real_pct=70)).select_scene(reins)
    assert entry["source_used"] == "ai_image"


def test_filtro_tecnico_aceita_resolucao_de_arquivo():
    cfg = {"min_aspect": 1.55, "max_aspect": 2.0, "duration_margin": 0.5, "min_height": 1080,
           "youtube_min_height": 720, "archive_min_photo_height": 500, "archive_min_video_height": 240}
    film = Candidate(provider="internet_archive", external_id="FarmerMi1920", title="farm 1920", duration=700.0,
                     width=640, height=480, page_url="", thumbnail=None, renditions=[Rendition("u", 640, 480)])
    portrait = Candidate(provider="wikimedia", external_id="1", title="stamp", duration=0.0, width=288, height=484,
                         page_url="", thumbnail=None, is_image=True)
    stock_480 = Candidate(provider="pexels", external_id="2", title="farm", duration=30.0, width=640, height=480,
                          page_url="", thumbnail=None, renditions=[Rendition("u", 640, 480)])
    passed, rejected = technical_filter([film, portrait, stock_480], 5.0, set(), cfg)
    assert passed == [film] and rejected == {"proporção": 2}


# ---------------------------------------------------------------- §5 licenças dos acervos
@pytest.mark.parametrize("code,short,ok", [
    ("pd", "Public domain", True), ("cc0", "CC0", True), ("cc-by-4.0", "CC BY 4.0", True),
    ("cc-by-sa-2.0", "CC BY-SA 2.0", False), ("cc-by-nc-4.0", "CC BY-NC 4.0", False), ("", "", False),
    ("attribution", "Attribution", False),
])
def test_licenca_livre_do_commons(code, short, ok):
    assert bool(archives.free_license(code, short)) is ok


def test_licencas_do_internet_archive_e_da_loc():
    assert archives.ia_license("http://creativecommons.org/licenses/publicdomain/")
    assert archives.ia_license("https://creativecommons.org/licenses/by/4.0/")
    assert not archives.ia_license("https://creativecommons.org/licenses/by-nc/3.0/")
    assert not archives.ia_license("")
    assert archives.loc_license({"rights_advisory": ["No known restrictions on publication."]})
    assert not archives.loc_license({"title": "sem direitos informados"})


# ---------------------------------------------------------------- §7 imagem gerada de época
def test_prompt_de_imagem_de_epoca():
    s = {**hist_scene(), "action": "a poor farming family sharing boiled potatoes at a rough wooden table",
         "setting": "inside a dim stone cottage", "subject": "farming family",
         "must_show": ["boiled potatoes"], "era_markers_to_show": ["iron pot"]}
    p = build_image_prompt(s, {"recurring_entities": []})
    assert "in rural Ireland, 1850." in p
    assert "Period-accurate details: wool coats" in p and "lighting from hearth fire" in p
    assert "earthen floor" in p  # cena de interior traz os interiores do contexto
    assert "Photorealistic still from a high-budget historical period film" in p
    assert "Must show: boiled potatoes, iron pot." in p
    avoid = p.split("Avoid: ")[1]
    assert avoid.startswith("cars, power lines") and "text, watermark, logos" in avoid
    assert len(p) <= 1000


def test_prompt_de_epoca_aparencia_de_arquivo_e_avoid_extra():
    s = hist_scene()
    pre_film = build_image_prompt(s, {}, period_look="archival", extra_avoid=["electric light bulb"])
    assert "Authentic 1850s daguerreotype photograph, sepia tones" in pre_film
    assert pre_film.split("Avoid: ")[1].startswith("electric light bulb, cars")
    early = {**s, "context": {**s["context"], "era": "1920s", "footage_feasibility": "early_film"}}
    assert "black-and-white archival photograph from the 1920s" in build_image_prompt(early, {},
                                                                                       period_look="archival")


def test_entidade_recorrente_com_a_mesma_descricao_no_contexto():
    s = {**hist_scene(), "subject": "the grandmother", "visual_intent": "the grandmother by the hearth"}
    bible = {"recurring_entities": [{"name": "the grandmother", "look": "elderly woman, wool shawl, hair tied back",
                                     "contexts": ["ctx1"]}]}
    assert "the grandmother: elderly woman, wool shawl, hair tied back." in build_image_prompt(s, bible)
    other = {**s, "context": {**s["context"], "id": "ctx9"}}
    assert "elderly woman" not in build_image_prompt(other, bible)


def test_prompt_longo_continua_dentro_do_limite():
    s = {**hist_scene(), "must_avoid": [f"confusion number {i}" for i in range(30)], "mood": "x" * 200}
    assert len(build_image_prompt(s, {})) <= 1000


def test_imagem_com_anacronismo_e_regerada_com_o_item_no_avoid(monkeypatch, tmp_path):
    from app.pipeline import imagegen
    from app.providers.llm import gemini

    prompts = []
    monkeypatch.setattr(imagegen.darkvi_images, "generate",
                        lambda prompt, dest, reference_key=None: prompts.append(prompt) or dest.write_bytes(b"png"))
    monkeypatch.setattr(gemini, "available", lambda: True)
    notes = iter([
        {"seen": "cottage with an electric light bulb", "realism": "real_footage", "subject_visible": True,
         "forbidden_present": [], "anachronisms_seen": ["electric light bulb"], "era_consistent": False, "score": 0.0},
        {"seen": "family by candlelight", "realism": "real_footage", "subject_visible": True,
         "forbidden_present": [], "anachronisms_seen": [], "era_consistent": True, "score": 8.0},
    ])
    monkeypatch.setattr(imagegen, "rate_local_image", lambda *a, **k: next(notes))
    result = imagegen.generate_validated(hist_scene(), {}, "", tmp_path / "s.png", {"gemini_model": "g",
                                                                                    "min_score": 6.5}, {})
    assert result.ok and result.attempts == 2
    assert "electric light bulb" not in prompts[0].split("Avoid: ")[1]
    assert prompts[1].split("Avoid: ")[1].startswith("electric light bulb")
    assert "out-of-period electric light bulb" in prompts[1]


# ---------------------------------------------------------------- §8 unidade visual no render
def test_gradacao_por_bloco():
    assert C.grade_for("pre_film") == "period_warm" and C.grade_for("pre_film", "archival") == "sepia"
    assert C.grade_for("early_film", "archival") == "bw" and C.grade_for("contemporary") is None
    assert set(C.GRADE_FILTERS) == {"period_warm", "sepia", "early_film", "bw", "vintage"}


def test_direcao_aplica_gradacao_e_card_de_lugar_na_troca_de_bloco(env, monkeypatch):
    ctx = make_ctx()
    hist = C.scene_context({**IRELAND})
    lab = C.scene_context({**LAB, "card_text": "Today, in the lab"})
    scenes = []
    for i, (c, start) in enumerate([(hist, 0.0), (hist, 6.0), (lab, 12.0)]):
        sid = f"s{i + 1:03d}"
        scenes.append({"id": sid, "start": start, "end": start + 6.0, "text": "x", "source": "stock",
                       "context": c, "context_id": c["id"], "chapter_break": False, "highlight": None})
        (ctx.dir / "assets").mkdir(exist_ok=True)
        (ctx.dir / "assets" / f"{sid}.mp4").write_bytes(b"x")
    ctx.write_json("plan.json", {"scenes": scenes, "music_mood": "x"})
    ctx.write_json("selection.json", {s["id"]: {"source": "stock", "source_used": "stock", "asset": f"assets/{s['id']}.mp4",
                                                "is_image": False} for s in scenes})
    monkeypatch.setattr(direct, "fix_overlay_language", lambda ctx, overlays, scenes: overlays)
    direct.run(ctx)
    tl = ctx.read_json("timeline.json")
    assert [s.get("grade") for s in tl["scenes"]] == ["period_warm", "period_warm", None]
    cards = [o for o in tl["overlays"] if o["type"] == "place_card"]
    assert [(c["text"], c["start"] >= 0.5) for c in cards] == [("Rural Ireland, 1850", True),
                                                                ("Today, in the lab", True)]
    assert cards[1]["start"] >= 12.0
    report = ctx.read_json("output/visual_report.json")
    assert report[0]["contexto"].startswith("ctx1: 1850")
    assert "estrategia" in report[0] and "era_consistent" in report[0]


def test_overlay_do_card_de_lugar_renderiza(tmp_path):
    from app.directions.classico.overlays import TEMPLATES

    style = {"font": "Montserrat Bold", "color_primary": "#FFFFFF", "color_accent": "#E63946"}
    try:
        out = TEMPLATES["place_card"]("Rural Ireland, 1850", style, tmp_path / "card.png")
    except (OSError, FileNotFoundError) as e:  # máquina sem a fonte
        pytest.skip(str(e))
    assert Path(out).stat().st_size > 0
