"""Fase B: interpretação tipada do roteiro — entidades, afirmações, papel visual, tempo narrado e contexto."""
from __future__ import annotations

from app.pipeline import context as C
from app.pipeline import plan as plan_mod
from app.pipeline import semantics as S
from app.pipeline.plan import PlanWindow
from app.pipeline.visual import vision_context
from app.providers.llm.base import LLMUsage

from .test_context import IRELAND, LAB, draft, raw_bible
from .test_selector import make_ctx

ENTITIES = [
    {"name": "Maeve", "kind": "person", "aliases": ["the girl"], "evidence_units": [2]},
    {"name": "maeve", "kind": "person", "aliases": ["Maeve O'Neill"], "evidence_units": [4],
     "explicit_attributes": ["red hair"]},
    {"name": "potato", "kind": "plant", "scientific_name": "Solanum tuberosum", "evidence_units": [0]},
    {"name": "jasmine", "kind": "plant", "ambiguous_name": True, "evidence_units": [5]},
]
CLAIMS = [
    {"units": [1], "quote": "the potatoes were not poisonous", "subject": "potato", "relation": "is",
     "object": "poisonous", "modality": "negated"},
    {"units": [3], "quote": "the potatoes were poisonous", "subject": "potato", "relation": "is",
     "object": "poisonous", "modality": "asserted"},
]


def bible(**kw) -> dict:
    return C.finish_bible({**raw_bible(), "entities": ENTITIES, "claims": CLAIMS, **kw}, n_units=6)


# ---------------------------------------------------------------- entidades e afirmações
def test_entidades_tem_id_estavel_e_duplicadas_sao_fundidas():
    b = bible()
    names = [(e["id"], e["name"]) for e in b["entities"]]
    assert names == [("ent01", "potato"), ("ent02", "Maeve"), ("ent03", "jasmine")]
    maeve = b["entities"][1]
    assert "Maeve O'Neill" in maeve["aliases"] and maeve["explicit_attributes"] == ["red hair"]
    assert maeve["evidence_units"] == [2, 4]
    assert bible()["entities"] == b["entities"]  # determinístico


def test_afirmacao_e_alegacao_com_sujeito_resolvido():
    claims = bible()["claims"]
    assert [c["id"] for c in claims] == ["clm01", "clm02"]
    assert claims[0]["subject_entity"] == "ent01" and claims[0]["needs_source"] is True


def test_bibia_antiga_sem_campos_novos_continua_valida():
    b = C.finish_bible(raw_bible(), n_units=6)
    assert b["entities"] == [] and b["claims"] == [] and b["ambiguities"] == []
    assert b["recurring_entities"][0]["look_basis"] == "inferred_artistic"


# ---------------------------------------------------------------- cena: negação, metáfora, pronomes, espécie
def _scene(**kw) -> dict:
    base = {"id": "s001", "units": [1, 1], "literal": True, "entity_ids": [], "claim_ids": [],
            "evidence_quote": "", "visual_role": "contextual_illustration", "required_identity": "generic",
            "must_not_imply": [], "unresolved": [], "entities": []}
    base.update(kw)
    return base


def test_negacao_vira_nao_sugerir():
    s = _scene(claim_ids=["clm01"], entity_ids=["ent01"])
    S.check_scene(s, "Luckily the potatoes were not poisonous.", bible())
    assert s["must_not_imply"] == ["poisonous"]


def test_metafora_nunca_e_evidencia_exata():
    s = _scene(literal=False, visual_role="exact_evidence", required_identity="exact_event")
    notes = S.check_scene(s, "Her heart froze.", bible())
    assert s["visual_role"] == "metaphor" and s["required_identity"] == "generic"
    assert notes and s["unresolved"]  # a correção fica registrada, não é silenciosa


def test_pronome_resolvido_por_nome_ou_alias():
    s = _scene(entity_ids=["the girl", "fantasma"])
    S.check_scene(s, "She walked to the shore.", bible())
    assert s["entity_ids"] == ["ent02"]
    assert any("fantasma" in n for n in s["unresolved"])


def test_especie_ambigua_sem_nome_cientifico_fica_pendente():
    s = _scene(entity_ids=["ent03"], required_identity="species")
    S.check_scene(s, "The jasmine bloomed.", bible())
    assert any("ambíguo" in n for n in s["unresolved"]) and S.needs_review(s)
    ok = _scene(entity_ids=["ent01"], required_identity="species")
    S.check_scene(ok, "The potato flowered.", bible())
    assert not ok["unresolved"]


def test_trecho_de_evidencia_precisa_estar_na_narracao():
    s = _scene(evidence_quote="potatoes rotted")
    S.check_scene(s, "Crops failed everywhere.", bible())
    assert s["evidence_quote"] == "" and s["unresolved"]
    units = [{"i": 1, "text": "Crops failed everywhere, the potatoes rotted."}]
    good = _scene(evidence_quote="the potatoes rotted", units=[0, 0])
    S.check_scene(good, units[0]["text"], bible())
    assert S.script_evidence(good, units) == [{"unit": 1, "quote": "the potatoes rotted"}]


def test_grafico_so_com_fonte():
    s = _scene(data_points=[{"label": "1845", "value": 8.5}], data_source="")
    S.check_scene(s, "Population fell.", bible())
    assert s["data_points"] == []
    s = _scene(data_points=[{"label": "1845", "value": 8.5}], data_source="1851 Irish census")
    S.check_scene(s, "Population fell.", bible())
    assert s["visual_role"] == "data_explanation"


def test_contradicao_e_ambiguidade_vao_para_revisao():
    b = bible(ambiguities=[{"units": [5], "question": "Which jasmine species?", "importance": "high"}])
    problems = S.reconcile([{"id": "s001", "units": [0, 5], "entity_ids": [], "unresolved": []}], b)
    codes = [p["code"] for p in problems]
    assert "SCRIPT_CONTRADICTION" in codes and "INTERPRETATION_REVIEW" in codes


# ---------------------------------------------------------------- tempo narrado e contexto
def test_passado_recente_nao_vira_presente_narrativo():
    recent = {**IRELAND, "era": "2005", "era_label": "mid-2000s", "place": "New Orleans", "first_unit": 0,
              "last_unit": 5, "card_text": "New Orleans, 2005", "anachronisms": ["smartphones of 2020s"]}
    b = C.finish_bible(raw_bible([recent]), n_units=6)
    block = C.block_for_unit(b, 0)
    assert block["footage_feasibility"] == "contemporary"  # filmável com tecnologia de hoje
    assert block["narrative_time"] == "past" and block["recent_past"] and block["era"] == "2005"
    ctx = C.scene_context(block)
    text = vision_context(ctx)
    assert "2005" in text and "present day." not in text


def test_contemporaneo_continua_presente():
    b = C.finish_bible(raw_bible(), n_units=6)
    assert C.block_for_unit(b, 5)["narrative_time"] == "present"
    assert C.block_for_unit(b, 0)["narrative_time"] == "past"


def test_cena_que_atravessa_contextos_e_dividida_e_cada_parte_herda_o_seu():
    b = C.finish_bible(raw_bible(), n_units=6)  # ctx1: 0–3 (1850), ctx2: 4–5 (laboratório)
    window = PlanWindow(scenes=[draft(0, 1), draft(2, 5, context_id="ctx1")], music_mood=None)
    out = plan_mod._validate(window, 0, 5, "real_preferred", b).scenes
    assert [(s.first_unit, s.last_unit, s.context_id) for s in out] == [(0, 1, "ctx1"), (2, 3, "ctx1"),
                                                                        (4, 5, "ctx2")]
    assert out[2].unresolved and not out[2].era_markers_to_show  # sem contaminação de época
    assert out[2].archival_query == ""


def test_comparacao_declarada_pode_juntar_contextos():
    b = C.finish_bible(raw_bible(), n_units=6)
    window = PlanWindow(scenes=[draft(0, 5, comparison=True, unresolved=["1850 × hoje"])], music_mood=None)
    out = plan_mod._validate(window, 0, 5, "real_preferred", b).scenes
    assert len(out) == 1


def test_contexto_pedido_sem_comparacao_nao_contamina():
    b = C.finish_bible(raw_bible(), n_units=6)
    window = PlanWindow(scenes=[draft(4, 5, context_id="ctx1")], music_mood=None)
    s = plan_mod._validate(window, 4, 5, "real_preferred", b).scenes[0]
    assert s.context_id == "ctx2" and s.unresolved


# ---------------------------------------------------------------- ponta a ponta: campos chegam ao plan.json
def test_plan_json_leva_interpretacao_e_problemas_viram_avisos(monkeypatch):
    from sqlmodel import select

    from app.db import session_scope
    from app.models import Issue

    ctx = make_ctx()
    words = [{"text": w, "start": i * 0.8, "end": i * 0.8 + 0.6}
             for i, w in enumerate("Maeve planted potatoes. They were not poisonous. She smiled.".split())]
    ctx.write_json("transcript.json", {"words": words, "audio_duration": 8.0})
    raw = {**raw_bible([{**IRELAND, "first_unit": 0, "last_unit": 5}]), "entities": ENTITIES, "claims": CLAIMS,
           "summary": "s", "intent": "i", "genre": "g", "visual_style": "v", "period_look": "cinematic",
           "story_beats": []}

    def fake_llm(task, *, schema, **kw):
        if schema is C.ContextBible:
            return C.ContextBible.model_validate(raw), LLMUsage()
        n = int(kw["user"].split(" to ")[1].split(" ")[0])
        return PlanWindow(scenes=[draft(0, n, entity_ids=["the girl"], claim_ids=["clm01"],
                                        evidence_quote="not poisonous", visual_role="contextual_illustration")],
                          music_mood=None), LLMUsage()

    monkeypatch.setattr(plan_mod, "call_llm", fake_llm)
    plan_mod.run(ctx)
    scenes = ctx.read_json("plan.json")["scenes"]
    first = scenes[0]
    assert first["entity_ids"] == ["ent02"] and first["must_not_imply"] == ["poisonous"]
    assert first["script_evidence"] and first["visual_role"] == "contextual_illustration"
    with session_scope() as s:
        codes = {i.code for i in s.exec(select(Issue).where(Issue.production_id == ctx.production_id))}
    assert "SCRIPT_CONTRADICTION" in codes


def test_grafico_deterministico_com_fonte(tmp_path):
    from PIL import Image

    from app.pipeline.charts import chart_entry

    scene = {"id": "s009", "data_points": [{"label": "1841", "value": 8.2, "unit": "mi"},
                                           {"label": "1851", "value": 6.6, "unit": "mi"}],
             "data_source": "Censo irlandês de 1851"}
    entry = chart_entry(scene, tmp_path / "assets" / "s009_chart.png", "#E63946")
    assert entry["source"] == "chart" and entry["validation"]["score_basis"] == "deterministic"
    with Image.open(tmp_path / "assets" / "s009_chart.png") as im:
        assert im.size == (1920, 1080)
    assert chart_entry({**scene, "data_source": ""}, tmp_path / "x.png") is None
