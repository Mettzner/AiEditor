"""Precisão visual + estilo contextual + velocidade (offline, sem rede). IA real: test_visual_live.py."""
from __future__ import annotations

import time

from app.lang import is_mismatch, resolve_video_language
from app.pipeline import direct
from app.pipeline import select as sel_mod
from app.pipeline.select import Decision, Option
from app.pipeline.select.prerank import technical_filter, text_score
from app.pipeline.visual import (build_image_prompt, image_type_for, metadata_check, sanitize_queries, scene_style,
                                 score_row, video_type_for)
from app.providers.llm import gemini
from app.providers.stock.base import Candidate, Rendition

from .test_selector import FakeStock, env, issues, make_ctx, row, scene  # noqa: F401  (fixture e helpers)

CFG = {"min_aspect": 1.55, "max_aspect": 2.0, "duration_margin": 0.5, "min_height": 1080, "youtube_min_height": 720}
FRANCHISES = ["minecraft", "mario", "disney", "fortnite"]


def cand(title: str, provider: str = "pexels", category: str = "", **kw) -> Candidate:
    base = dict(provider=provider, external_id=title[:12], title=title, duration=30.0, width=1920, height=1080,
                page_url="", thumbnail=None, renditions=[Rendition("u", 1920, 1080)], category=category)
    base.update(kw)
    return Candidate(**base)


def cell_scene(**kw) -> dict:
    return {**scene(), "subject": "human cell", "style_allowance": "stylized_ok",
            "allowed_styles": ["cgi_3d", "animation", "real_footage"],
            "style_reason": "inside human cells; no real footage possible", **kw}


# ---------------------------------------------------------------- §5 nota por estilo
def test_nota_por_estilo_da_cena():
    real, cgi = row(1, 8.0), row(1, 8.0, realism="cgi_3d")
    assert score_row(real, "real_only") == 8.0
    assert score_row(cgi, "real_only") == 0  # real_only: estilizado zera
    assert score_row(cgi, "real_preferred", ["real_footage", "cgi_3d"]) == 6.4  # ×0,8
    assert score_row(cgi, "real_preferred", ["real_footage"]) == 4.0  # ×0,5
    assert score_row(cgi, "stylized_ok", ["cgi_3d", "animation"]) == 8.0  # aceito
    assert score_row(row(1, 8.0, realism="illustration"), "stylized_ok", ["cgi_3d"]) == 4.8  # ×0,6


def test_franquia_ia_defeituosa_e_captura_zeram_sempre():
    franchise = row(1, 9.0, realism="animation_cartoon")
    franchise.brand_or_franchise = True
    assert score_row(franchise, "stylized_ok", ["cartoon", "animation"]) == 0
    assert score_row(row(1, 9.0, realism="ai_looking"), "stylized_ok", ["cgi_3d"]) == 0
    assert score_row(row(1, 9.0, realism="screen_capture"), "stylized_ok", ["cgi_3d"]) == 0


def test_proibido_e_sem_assunto_continuam_limitando():
    assert score_row(row(1, 9.0, forbidden=["freezer"])) <= 2
    assert score_row(row(1, 9.0, subject=False)) <= 3


# ---------------------------------------------------------------- §4 metadados: penalidade × bloqueio
def test_termos_de_estilo_viram_penalidade_conforme_a_cena():
    t = "human cell 3d animation"
    assert metadata_check(t, "real_only", ["real_footage"], [], [], FRANCHISES) == ("estilo", 0.0)
    assert metadata_check(t, "real_preferred", ["real_footage", "cgi_3d"], [], [], FRANCHISES) == (None, 0.7)
    assert metadata_check(t, "stylized_ok", ["cgi_3d", "animation"], [], [], FRANCHISES) == (None, 1.0)
    assert metadata_check("forest creature cartoon", "stylized_ok", ["illustration"], [], [], FRANCHISES) == (None, 0.7)
    assert metadata_check("frozen strawberries macro", "real_only", ["real_footage"], [], [], FRANCHISES) == (None, 1.0)


def test_franquias_e_interfaces_sempre_bloqueadas():
    for title in ("minecraft farm gameplay", "Super Mario cartoon", "disney castle animation"):
        assert metadata_check(title, "stylized_ok", ["video_game", "cartoon", "animation"], [], [],
                              FRANCHISES)[0] == "franquia"
    assert metadata_check("app screenshot hud", "stylized_ok", ["video_game"], [], [], FRANCHISES)[0] == \
        "interface/marca"
    # "game" não casa dentro de outra palavra
    assert metadata_check("endgame of summer harvest", "real_only", ["real_footage"], [], [], FRANCHISES) == (None, 1.0)


def test_youtube_gaming_so_quando_a_cena_aceita_jogo():
    gaming = cand("kids playing a racing game", provider="youtube", category="20", height=720, width=1280)
    film_anim = cand("strawberry harvest documentary", provider="youtube", category="1", height=720, width=1280)
    passed, _ = technical_filter([gaming, film_anim], 5.0, set(), CFG, franchises=FRANCHISES)
    assert passed == [film_anim]  # categoria 1 (Film & Animation) não é mais descartada
    passed, _ = technical_filter([gaming], 5.0, set(), CFG, allowance="stylized_ok",
                                 allowed_styles=["real_footage", "video_game"], franchises=FRANCHISES)
    assert passed == [gaming]


def test_parametros_do_pixabay_dependem_da_cena():
    assert video_type_for(["real_footage"]) == "film"
    assert video_type_for(["real_footage", "cgi_3d"]) == "all"
    assert video_type_for(["animation"]) == "animation"
    assert image_type_for(["real_footage"]) == "photo"
    assert image_type_for(["illustration", "painting_historical"]) == "illustration"
    assert image_type_for(["cartoon"]) == "vector"


def test_texto_sem_assunto_perde_metade_e_penalidade_de_estilo():
    good = text_score(cand("frozen strawberries bowl kitchen"), ["frozen strawberries close up"], "", 5,
                      "frozen strawberries", ["strawberries"])
    off = text_score(cand("white freezer kitchen appliance"), ["frozen strawberries close up"], "", 5,
                     "frozen strawberries", ["strawberries"])
    assert good > 2 * off
    penalized = cand("frozen strawberries bowl kitchen")
    penalized.penalty = 0.7
    assert text_score(penalized, ["frozen strawberries close up"], "", 5, "frozen strawberries",
                      ["strawberries"]) == round(good * 0.7, 2)


# ---------------------------------------------------------------- queries e estilos da cena
def test_queries_respeitam_estilo_e_must_avoid():
    qs = sanitize_queries("frozen strawberries", ["frozen strawberries close up", "frozen food freezer aisle",
                                                  "frosted fruit macro benefits"], ["freezer", "refrigerator"])
    assert len(qs) == 3 and all("strawberries" in q for q in qs)
    assert not any("freezer" in q or "benefits" in q for q in qs)
    tea = sanitize_queries("herbal tea", ["herbal tea game changer", "pouring herbal tea"], ["video game"])
    assert not any("game" in q.split() for q in tea)
    cell = sanitize_queries("human cell", ["human cell 3d animation", "human cell microscope"], [],
                            ["cgi_3d", "animation", "real_footage"])
    assert "human cell 3d animation" in cell  # estilo desejado fica na query


def test_preset_so_real_forca_real_only():
    assert scene_style(cell_scene(), "real_only") == ("real_only", ["real_footage"])
    assert scene_style(cell_scene(), "real_preferred")[0] == "stylized_ok"
    assert scene_style({**cell_scene(), "style_allowance": "real_preferred"}, "free")[0] == "stylized_ok"


# ---------------------------------------------------------------- §7 imagem gerada por estilo
SCENE = {"subject": "frozen strawberries", "action": "a hand scooping frozen strawberries from a ceramic bowl",
         "setting": "home kitchen counter", "shot": "close-up", "mood": "bright morning light",
         "must_show": ["strawberries", "frost crystals on the fruit"], "must_avoid": ["freezer", "refrigerator"],
         "visual_intent": "close-up of frozen strawberries"}


def test_prompt_de_imagem_real_por_padrao():
    p = build_image_prompt(SCENE, {"region_culture": "home kitchens", "visual_world": "real kitchens"})
    assert p.startswith("a hand scooping frozen strawberries")
    assert "Photorealistic documentary photograph" in p and "Must show: strawberries" in p
    assert "Avoid: cartoon, illustration, 3D render, CGI, video game look" in p and "freezer" in p
    assert "existing characters or franchises" in p and len(p) <= 1000


def test_prompt_de_imagem_com_estilo_da_cena():
    p = build_image_prompt({**SCENE, "subject": "sailors with lime juice"}, None, style="painting_historical")
    assert "Historical oil painting in the style of the period" in p
    avoid = p.split("Avoid:")[1]
    assert "painting" not in avoid and "existing characters or franchises" in avoid and "watermark" in avoid
    cell = build_image_prompt({**SCENE, "subject": "human cell"}, None, style="cgi_3d")
    assert "scientific 3D medical animation still" in cell and "3D render" not in cell.split("Avoid:")[1]


def test_prompt_longo_corta_sem_perder_assunto_e_must_show():
    long_scene = {**SCENE, "mood": "x" * 300, "setting": "y " * 150, "must_avoid": [f"item{i}" for i in range(40)]}
    p = build_image_prompt(long_scene, {"region_culture": "z " * 100, "visual_world": "w " * 50})
    assert len(p) <= 1000 and "frozen strawberries" in p and "Must show: strawberries, frost crystals" in p
    assert "x" * 300 not in p


# ---------------------------------------------------------------- §10.2 pós-processamento local
def _opt(key: str, score: float, realism: str = "real_footage") -> Option:
    return Option(cand(key, external_id=key), score, 0.5, realism, "stock", "vision")


def test_clipe_repetido_fica_com_a_cena_de_maior_nota(env):
    sel = sel_mod.Selector(make_ctx())
    a = Decision(scene={**scene("s001"), "start": 0}, planned="stock", options=[_opt("X", 8.0), _opt("A2", 6.0)])
    b = Decision(scene={**scene("s002"), "start": 5}, planned="stock", options=[_opt("X", 9.0), _opt("B2", 7.0)])
    sel.resolve([a, b])
    assert b.best.candidate.key.endswith(":X") and a.best.candidate.key.endswith(":A2")


def test_continuidade_de_estilo_forma_blocos(env):
    sel = sel_mod.Selector(make_ctx())
    ds = [Decision(scene={**scene(f"s00{i}"), "start": i}, planned="stock", options=opts) for i, opts in enumerate([
        [_opt("P", 8.0, "cgi_3d")],
        [_opt("R", 8.2, "real_footage"), _opt("C", 7.9, "cgi_3d")],  # 7,9 + 0,5 > 8,2 → vira 3D
        [_opt("N", 8.0, "cgi_3d")],
    ])]
    sel.resolve(ds)
    assert ds[1].best.candidate.key.endswith(":C") and ds[1].stats["continuity"] == "3d"


# ---------------------------------------------------------------- disjuntor do Gemini (causa da lentidão)
def test_cota_diaria_do_gemini_corta_na_hora_sem_esperar(env, monkeypatch):
    calls = []

    def quota_exceeded(*a, **k):
        calls.append(1)
        raise gemini.GeminiQuotaExhausted("Cota diária do Gemini esgotada (PerDay)")

    monkeypatch.setattr(gemini, "rate_sheet", quota_exceeded)
    ctx = make_ctx()
    t = time.perf_counter()
    entry = sel_mod.Selector(ctx).select_scene(scene())
    assert time.perf_counter() - t < 2 and entry["method"] == "text_fallback"
    assert "VISION_QUOTA" in issues(ctx)


def test_disjuntor_bloqueia_ate_meia_noite_do_pacifico(monkeypatch):
    monkeypatch.setattr(gemini, "get_secret", lambda p: "k")
    gemini._block(gemini._next_pacific_midnight(), "teste")
    try:
        assert gemini.quota_status()["blocked"] and not gemini.available()
    finally:
        from app.config import update_settings
        update_settings({"gemini": {"blocked_until": None, "blocked_reason": ""}})
    assert gemini.available()


# ---------------------------------------------------------------- §10 velocidade (latência simulada)
def test_8_cenas_em_paralelo_com_1_visao_por_cena(env, monkeypatch):
    """Busca de 0,3 s por requisição e visão de 1,5 s: sequencial levaria ~40 s; em paralelo, poucos segundos."""
    vision_calls = []

    def slow_vision(image, prompt, model, schema=None):
        vision_calls.append(1)
        time.sleep(1.5)
        n = int(prompt.split("contact sheet with ")[1].split(" numbered")[0])
        from app.pipeline.visual import VisionSheet
        return VisionSheet(candidates=[row(1, 9.0)] + [row(i, 5.0) for i in range(2, n + 1)]), 0.0

    class SlowStock(FakeStock):
        def search(self, query, per_page=15, lang="en", **kw):
            time.sleep(0.3)
            return super().search(query, per_page, lang)

    monkeypatch.setattr(sel_mod, "enabled_providers", lambda: [SlowStock("pexels"), SlowStock("pixabay")])
    monkeypatch.setattr(gemini, "rate_sheet", slow_vision)
    ctx = make_ctx()
    scenes = [{**scene(f"s{i:03d}", q=f"topic{i} subject"), "start": i * 5.0, "end": i * 5.0 + 5.0,
               "subject": f"topic{i} subject", "visual_intent": f"intent {i}"}
              for i in range(1, 9)]
    ctx.write_json("plan.json", {"scenes": scenes})
    t = time.perf_counter()
    sel_mod.run(ctx)
    elapsed = time.perf_counter() - t
    report = ctx.read_json("timing_report.json")
    sel = ctx.read_json("selection.json")
    assert len(vision_calls) == 8 and all(v["vision_calls"] == 1 for v in sel.values())
    assert elapsed < 10, elapsed
    assert report["scenes"] == 8 and report["slowest_stages"]
    assert len({v["external_id"] for v in sel.values()}) == 8  # nenhum clipe repetido em paralelo


# ---------------------------------------------------------------- idioma (§11 do documento anterior)
def test_idioma_dos_overlays():
    assert not is_mismatch("12 Protein-Rich Plants", "en")
    assert is_mismatch("12 Plantas Proteicas", "en")
    assert is_mismatch("US$ 200 bilhões por ano", "en")
    assert not is_mismatch("US$ 200 bilhões por ano", "pt")
    assert not is_mismatch("1987", "en")


def test_idioma_do_video_segue_o_roteiro():
    en = "Every year Americans spend over two hundred billion dollars on meat, but plants can do better."
    pt = "Todos os anos os brasileiros gastam bilhões com carne, mas as plantas podem fazer melhor."
    assert resolve_video_language("en", en) == ("en", None)
    assert resolve_video_language("pt", pt) == ("pt", None)
    assert resolve_video_language("en", pt) == ("pt", "pt")


def test_overlay_em_portugues_num_video_em_ingles_e_corrigido(env, monkeypatch):
    from app.providers.llm import base

    monkeypatch.setattr(base, "call_llm", env["llm"])
    ctx = make_ctx()
    ctx.config.video_language = "en"
    overlays = [{"type": "chapter", "start": 1.0, "end": 3.0, "text": "12 Plantas Proteicas"},
                {"type": "highlight", "start": 5.0, "end": 7.0, "text": "$200 Billion a Year"}]
    out = direct.fix_overlay_language(ctx, overlays, [{"start": 0, "end": 10, "text": "twelve protein-rich plants"}])
    assert [o["text"] for o in out] == ["12 Protein-Rich Plants", "$200 Billion a Year"]
    assert "OVERLAY_LANGUAGE_FIXED" in issues(ctx)


# ---------------------------------------------------------------- cadeia de fallback
def test_candidatos_de_estilo_incompativel_nunca_sao_usados(env):
    env["vision"].mode = "non_real"  # tudo vem como video_game numa cena real_only
    ctx = make_ctx()
    entry = sel_mod.Selector(ctx).select_scene(scene())
    assert entry["method"] == "generic" and entry["is_image"]
    codes = issues(ctx)
    assert "SCENE_GENERIC_FALLBACK" in codes and "STYLE_REJECTED" in codes and "SCENE_LOW_SCORE" not in codes


def test_animacao_3d_aceita_em_cena_de_celula(env):
    def vision(n, prompt, call):
        return [row(1, 8.5, realism="cgi_3d")] + [row(i, 5.0) for i in range(2, n + 1)]

    env["vision"].mode = vision
    ctx = make_ctx()
    entry = sel_mod.Selector(ctx).select_scene(cell_scene())
    assert entry["score"] == 8.5 and entry["realism"] == "cgi_3d"


def test_reescrita_guiada_pelo_que_foi_visto(env):
    def vision(n, prompt, call):
        good = "chest freezer" in prompt  # o must_avoid novo só existe depois da reescrita
        first = row(1, 8.5 if good else 3.0, subject=good)
        first.seen = "frosted strawberries in bowl" if good else "white chest freezer in a garage"
        return [first] + [row(i, 2.0) for i in range(2, n + 1)]

    env["vision"].mode = vision
    ctx = make_ctx()
    s = {**scene(), "must_avoid": ["freezer"]}
    entry = sel_mod.Selector(ctx).select_scene(s)
    assert "QUERY_REWRITTEN" in issues(ctx) and env["llm"].calls == ["QueryRewriteBatch"]
    assert entry["score"] >= 6.5 and "chest freezer" in entry["must_avoid_used"]


def test_imagem_gerada_entra_antes_das_fotos(env, monkeypatch):
    from app.pipeline import imagegen

    env["vision"].mode = "low"
    calls = []

    def fake_gen(scene, brief, style, dest, cfg, stats, reference_key=None, media_style="real_preferred", **kw):
        calls.append(scene["id"])
        dest.write_bytes(b"png")
        return imagegen.GeneratedImage(True, dest, "prompt", 8.0, "frosted strawberries", 1)

    monkeypatch.setattr(imagegen, "generate_validated", fake_gen)
    entry = sel_mod.Selector(make_ctx(real_pct=70)).select_scene(scene())
    assert calls and entry["source_used"] == "ai_image" and entry["is_image"]


def test_imagem_gerada_reprovada_segue_para_fotos(env, monkeypatch):
    from app.pipeline import imagegen

    env["vision"].mode = "low"
    monkeypatch.setattr(imagegen, "generate_validated",
                        lambda *a, **k: imagegen.GeneratedImage(False, None, "p", 2.0, "cartoon fruit", 2, "x"))
    ctx = make_ctx(real_pct=70)
    entry = sel_mod.Selector(ctx).select_scene(scene())
    assert "AI_IMAGE_REJECTED" in issues(ctx) and entry["source_used"] in ("stock_photo", "stock")


def test_url_da_darkvi_com_varios_arquivos_e_pedida_de_novo(monkeypatch, tmp_path):
    from app.providers.darkvi import images

    urls = iter(["https://x.r2.dev/imagens_public/a.jpg%2Cimagens_public/b.jpg?sig=1",
                 "https://x.r2.dev/imagens_public/c.jpg?sig=2"])
    monkeypatch.setattr(images.IMAGE_BUCKET, "acquire", lambda: None)
    monkeypatch.setattr(images.time, "sleep", lambda s: None)
    monkeypatch.setattr(images, "_save_quota", lambda b: None)

    def fake_call(method, path, **kw):
        if method == "POST":
            return {"data": {"id": 1}}
        return {"id": 1, "status": "DONE", "url": next(urls)}

    got = []
    monkeypatch.setattr(images, "call", fake_call)
    monkeypatch.setattr(images, "download", lambda url, dest: got.append(url) or dest)
    images.generate("p", tmp_path / "x.png")
    assert got == ["https://x.r2.dev/imagens_public/c.jpg?sig=2"]


def test_erro_inesperado_na_imagem_nao_derruba_a_selecao(env, monkeypatch):
    from app.pipeline import imagegen

    env["vision"].mode = "low"

    def boom(*a, **k):
        raise RuntimeError("HTTPStatusError 404")

    monkeypatch.setattr(imagegen, "generate_validated", boom)
    ctx = make_ctx(real_pct=70)
    entry = sel_mod.Selector(ctx).select_scene(scene())
    assert entry["source_used"] in ("stock", "stock_photo") and "AI_GEN_FAILED" in issues(ctx)
