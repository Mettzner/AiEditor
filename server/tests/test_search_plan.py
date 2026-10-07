"""Fase C: plano de busca por bloco, YouTube com 50 por página e paginação adaptativa, fallback banco → YouTube,
comparação entre fontes nas cenas exatas, consulta documental, identidade e diversidade no ranking."""
from __future__ import annotations

import httpx
import pytest

from app.pipeline import select as sel_mod
from app.pipeline.select import plan_search, prerank
from app.providers import embeddings
from app.providers.stock.base import Candidate
from app.providers.youtube import client as yt
from app.providers.youtube import quota

from .test_selector import env, make_ctx, row, scene  # noqa: F401  (fixture env)

BIBLE = {"entities": [{"id": "ent01", "name": "moringa", "kind": "plant", "scientific_name": "Moringa oleifera",
                       "aliases": ["drumstick tree"]}]}


def ytc(i: int, title: str, channel: str = "c", dur: float = 120.0) -> Candidate:
    return Candidate(provider="youtube", external_id=f"y{i}", title=title, duration=dur, width=1280, height=720,
                     page_url=f"https://youtu.be/y{i}", thumbnail=None, author=channel, channel_id=channel,
                     preview_frames=[f"https://i/{i}/{k}.jpg" for k in (1, 2, 3)], frame_positions=[.25, .5, .75])


# ---------------------------------------------------------------- consultas e identidade (C4/C5)
def test_consulta_documental_mantem_nome_cientifico_ano_e_entidade():
    s = {"id": "s1", "entity_ids": ["ent01"], "documentary_query": "1998 harvest Kerala village",
         "must_avoid": ["village"]}
    q = plan_search.documentary_query(s, BIBLE, s["must_avoid"])
    assert q.startswith("Moringa oleifera") and "1998" in q and "village" not in q.lower()
    assert plan_search.documentary_query({"id": "s2"}, BIBLE) == ""


def test_sem_identidade_a_nota_e_limitada_e_qualidade_nao_compensa():
    good = ytc(1, "moringa leaves harvest")
    pretty = ytc(2, "beautiful green leaves 4k cinematic", channel="d")
    terms = plan_search.identity_terms({"entity_ids": ["ent01"]}, BIBLE)
    ranked = prerank.rank([pretty, good], ["green leaves harvest"], "green leaves", 5, subject="leaves",
                          identity=terms, exact=True)
    assert ranked[0].external_id == "y1"
    loose = prerank.rank([ytc(2, "beautiful green leaves 4k cinematic"), ytc(1, "moringa leaves harvest")],
                         ["green leaves harvest"], "green leaves", 5, subject="leaves", identity=terms, exact=False)
    assert loose[0].score >= ranked[1].score  # sem exigência de identidade, não há corte


def test_amostra_para_visao_e_diversa_por_canal():
    cands = [ytc(i, "moringa tree", channel="mesmo") for i in range(5)] + [ytc(9, "moringa tree", channel="outro")]
    out = prerank.rank(cands, ["moringa tree"], "moringa", 5, keep=3, subject="moringa", per_channel=2)
    assert len(out) == 3 and {c.channel_id for c in out} == {"mesmo", "outro"}


def test_ranking_semantico_opcional_com_fallback(monkeypatch):
    a, b = ytc(1, "clip"), ytc(2, "clip")
    monkeypatch.setattr(embeddings, "similarities", lambda q, texts: None)  # desligado/sem biblioteca
    assert prerank.rank([a, b], ["x"], "x", 5)[0].score == prerank.rank([a, b], ["x"], "x", 5)[1].score
    monkeypatch.setattr(embeddings, "similarities", lambda q, texts: [0.1, 0.9])
    assert prerank.rank([ytc(1, "clip"), ytc(2, "clip")], ["x"], "x", 5)[0].external_id == "y2"
    assert embeddings.enabled() is False  # nunca liga sozinho


# ---------------------------------------------------------------- plano por bloco (C1)
def test_plano_agrupa_por_contexto_e_entidade_com_orcamento_por_importancia():
    scenes = [
        {"id": "s1", "context_id": "ctx1", "entity_ids": ["ent01"], "visual_role": "exact_evidence",
         "documentary_query": "Moringa oleifera seeds"},
        {"id": "s2", "context_id": "ctx1", "entity_ids": ["ent01"]},
        {"id": "s3", "context_id": "ctx1", "subject": "sunset", "visual_role": "metaphor", "literal": False},
    ]
    plan = plan_search.build_search_plan(scenes, BIBLE, {"max_youtube_pages_per_group": 3})
    g = plan["groups"][plan["scene_group"]["s1"]]
    assert g["scenes"] == ["s1", "s2"] and g["importance"] == "high" and g["budget"]["youtube_pages"] == 3
    assert g["budget"]["compare_sources"] and g["documentary_query"].startswith("Moringa oleifera")
    low = plan["groups"][plan["scene_group"]["s3"]]
    assert low["budget"]["youtube_pages"] == 1 and not low["budget"]["compare_sources"]


# ---------------------------------------------------------------- YouTube: 50 por página e paginação (C2)
def _youtube(env, pages: dict[str | None, tuple[list, str | None]], calls: list):
    def fake(q, n=50, lang="en", token=None):
        calls.append((q, n, token))
        return pages.get(token, ([], None))

    env["monkeypatch"].setattr(yt, "search_page", fake)


def test_youtube_pede_50_mesmo_no_modo_rapido(env):
    calls = []
    _youtube(env, {None: ([ytc(i, "dark country road night") for i in range(10)], None)}, calls)
    env["monkeypatch"].setattr(yt, "download_segment", lambda vid, a, b, dest, **kw: dest.write_bytes(b"v") or dest)
    sel = sel_mod.Selector(make_ctx(mode="fast"))
    assert int(sel.cfg["results_per_query"]) == 10  # o modo rápido mexe só nos bancos
    sel.select_scene(scene(source="youtube"))
    assert calls and all(n == 50 for _, n, _ in calls)


def test_pagina_extra_so_quando_util_e_com_cota(env):
    calls = []
    poor = ([ytc(1, "dark country road", dur=2)], "P2")  # curto demais: nada útil na 1ª página
    _youtube(env, {None: poor, "P2": ([ytc(i, "dark country road night") for i in range(2, 12)], None)}, calls)
    env["monkeypatch"].setattr(yt, "download_segment", lambda vid, a, b, dest, **kw: dest.write_bytes(b"v") or dest)
    sel = sel_mod.Selector(make_ctx())
    exact = {**scene(source="youtube"), "visual_role": "exact_evidence", "context_id": "c"}
    sel.plan_searches([exact])
    sel.select_scene(exact)
    first_query = calls[0][0]
    assert [t for q, _, t in calls if q == first_query] == [None, "P2"]  # cena "high": também 2 consultas
    # sem folga de cota (reserva de paginação), não pagina
    calls.clear()
    quota.spend(80, bucket="search")
    sel2 = sel_mod.Selector(make_ctx())
    exact2 = {**exact, "id": "s077", "queries": ["foggy dark road"]}
    _youtube(env, {None: ([ytc(31, "foggy dark road", dur=2)], "P2")}, calls)
    sel2.plan_searches([exact2])
    sel2.select_scene(exact2)
    assert all(t is None for _, _, t in calls)


# ---------------------------------------------------------------- alocação como preferência (C3)
def test_cena_de_banco_sem_resultado_tenta_youtube_antes_da_geracao(env):
    for p in env["stock"]:
        p.videos = 0
    calls = []
    _youtube(env, {None: ([ytc(i, "dark country road night fog") for i in range(6)], None)}, calls)
    env["monkeypatch"].setattr(yt, "download_segment", lambda vid, a, b, dest, **kw: dest.write_bytes(b"v") or dest)
    entry = sel_mod.Selector(make_ctx()).select_scene(scene(source="stock"))
    assert calls and entry["source_used"] == "youtube"


def test_cena_exata_compara_fontes_na_mesma_escala(env):
    calls = []
    _youtube(env, {None: ([ytc(i, "dark country road night") for i in range(6)], None)}, calls)
    env["monkeypatch"].setattr(yt, "download_segment", lambda vid, a, b, dest, **kw: dest.write_bytes(b"v") or dest)

    def scores(n, prompt, k):  # 1ª fonte (YouTube) nota 7,5; 2ª (bancos) nota 9
        top = 7.5 if k == 1 else 9.0
        return [row(1, top)] + [row(i, 5.0) for i in range(2, n + 1)]

    env["vision"].mode = scores
    sel = sel_mod.Selector(make_ctx())
    exact = {**scene(source="youtube"), "visual_role": "exact_evidence", "required_identity": "exact_event"}
    sel.plan_searches([exact])
    entry = sel.select_scene(exact)
    assert entry["source_used"] == "stock" and entry["score"] == 9.0
    assert env["vision"].calls == 2  # uma comparação só


def test_artefato_do_plano_de_busca(env, monkeypatch):
    from app.pipeline.select import run

    ctx = make_ctx()
    s1 = {**scene("s001"), "context_id": "c1", "chapter_break": False, "highlight": None}
    s2 = {**scene("s002"), "context_id": "c1", "chapter_break": False, "highlight": None, "start": 5.0, "end": 10.0}
    ctx.write_json("plan.json", {"scenes": [s1, s2]})
    run(ctx)
    plan = ctx.read_json("search_plan.json")
    group = plan["groups"][0]
    assert group["scenes"] == ["s001", "s002"] and group["queries"]["stock"]
    assert plan["youtube_results_per_query"] == 50 and "rejected" in group
