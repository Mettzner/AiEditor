"""Interpretação do roteiro: a Bíblia entende história, mensagem e visual; as cenas herdam isso; a tela não decide
idioma, buscas, estilo visual nem representação de época."""
from __future__ import annotations

import pytest

from app.api.productions import _build_config
from app.db import session_scope
from app.models import Channel, Preset
from app.pipeline import context as C
from app.pipeline import plan as plan_mod
from app.pipeline.plan import PlanWindow
from app.pipeline.visual import VISION_PROMPT, _entity_for, build_image_prompt
from app.providers.llm.base import LLMUsage

from .test_context import IRELAND, LAB, draft, raw_bible
from .test_selector import make_ctx

STORY = {"summary": "An Irish family survives the potato famine.", "intent": "Show resilience amid loss.",
         "genre": "historical drama narrated as a tale",
         "visual_style": "cold grey Atlantic light, muddy earth tones, soft overcast diffusion",
         "period_look": "cinematic",
         "story_beats": [{"first_unit": 0, "last_unit": 1, "beat": "The blight destroys the harvest.",
                          "emotion": "creeping dread"},
                         {"first_unit": 3, "last_unit": 5, "beat": "The family leaves for the coast.",
                          "emotion": "grief, resolve"}]}


# ---------------------------------------------------------------- bíblia
def test_biblia_guarda_historia_mensagem_e_visual():
    bible = C.finish_bible({**raw_bible(), **STORY}, n_units=6)
    assert bible["summary"].startswith("An Irish family") and bible["intent"] and bible["genre"]
    assert bible["visual_style"].startswith("cold grey") and bible["period_look"] == "cinematic"
    beats = bible["story_beats"]
    # faixas contíguas cobrindo todas as unidades: a lacuna (unidade 2) entra no trecho seguinte
    assert [(b["first_unit"], b["last_unit"]) for b in beats] == [(0, 1), (2, 5)]
    assert C.beat_for_unit(bible, 4)["beat"] == "The family leaves for the coast."


def test_representacao_de_epoca_invalida_vira_cinema():
    assert C.finish_bible({**raw_bible(), "period_look": "sepia"}, n_units=6)["period_look"] == "cinematic"


def test_visual_vem_da_biblia_e_config_so_para_producoes_antigas():
    cfg = Preset(visual_style="noturno, granulado", period_look="archival")
    assert C.video_look({"visual_style": "warm dusk", "period_look": "cinematic"}, cfg) == ("warm dusk", "cinematic")
    assert C.video_look({}, cfg) == ("noturno, granulado", "archival")  # bíblia antiga, sem os campos


# ---------------------------------------------------------------- personagens e prompt
BRIEF = {"visual_style": "cold grey Atlantic light, muddy earth tones", "recurring_entities": [
    {"name": "Maeve", "look": "girl of ten, red hair in a braid, patched grey wool dress", "contexts": ["ctx1"]},
    {"name": "Seamus", "look": "gaunt man of forty, dark beard, flat cap, brown waistcoat", "contexts": ["ctx1"]}]}


def test_personagens_declarados_na_cena_entram_com_a_aparencia_fixa():
    s = {"subject": "farmer", "visual_intent": "a farmer digging", "entities": ["Maeve", "Seamus"],
         "context": {"id": "ctx1"}}
    text = _entity_for(s, BRIEF)
    assert "Maeve: girl of ten" in text and "Seamus: gaunt man" in text
    assert _entity_for({**s, "entities": []}, BRIEF) == ""  # ninguém visível e nenhum nome no texto


def test_cena_antiga_sem_entities_ainda_acha_o_nome_no_texto():
    assert _entity_for({"visual_intent": "Maeve carrying a basket", "context": {"id": "ctx1"}}, BRIEF).startswith(
        "Maeve:")


@pytest.mark.parametrize("ctx", [C.scene_context({**IRELAND}), C.scene_context({**LAB})])
def test_prompt_de_imagem_leva_o_estilo_do_video_e_a_paleta(ctx):
    s = {"subject": "farmer", "action": "digging potatoes", "setting": "muddy field", "shot": "medium",
         "mood": "bleak", "must_show": ["potatoes"], "context": {**ctx, "palette": "muted greens and browns"}}
    prompt = build_image_prompt(s, BRIEF)
    assert "Look: cold grey Atlantic light" in prompt and "muted greens and browns" in prompt
    assert len(prompt) <= 1000


def test_validacao_visual_julga_o_significado_do_momento():
    assert "{meaning}" in VISION_PROMPT and "{beat}" in VISION_PROMPT


# ---------------------------------------------------------------- planejamento ponta a ponta
def test_planejamento_herda_trecho_da_historia_e_personagens(monkeypatch):
    ctx = make_ctx()
    words = []
    for i, w in enumerate("The blight came. Crops rotted. Maeve cried. They walked away.".split()):
        words.append({"text": w, "start": i * 0.8, "end": i * 0.8 + 0.6})
    ctx.write_json("transcript.json", {"words": words, "audio_duration": 8.0})
    bible = {**raw_bible([{**IRELAND, "first_unit": 0, "last_unit": 3}]), **STORY,
             "recurring_entities": [{"name": "Maeve", "look": "girl of ten, red hair", "contexts": ["ctx1"]}]}
    bible["story_beats"] = [{"first_unit": 0, "last_unit": 1, "beat": "The blight destroys the harvest.",
                             "emotion": "dread"},
                            {"first_unit": 2, "last_unit": 3, "beat": "The family gives up and leaves.",
                             "emotion": "grief"}]
    calls = []

    def fake_llm(task, *, schema, **kw):
        calls.append(task)
        if schema is C.ContextBible:
            return C.ContextBible.model_validate(bible), LLMUsage()
        return PlanWindow(scenes=[
            draft(0, 1, meaning="the harvest dies", entities=[]),
            draft(2, 3, meaning="Maeve grieves before leaving", entities=["Maeve", "a stranger"]),
        ], music_mood="somber"), LLMUsage()

    monkeypatch.setattr(plan_mod, "call_llm", fake_llm)
    plan_mod.run(ctx)
    out = ctx.read_json("plan.json")
    assert calls[0] == "bible" and "plan" in calls
    assert out["visual_style"].startswith("cold grey") and out["period_look"] == "cinematic"
    s1, s2 = out["scenes"]
    assert s1["beat"] == "The blight destroys the harvest." and s2["beat_emotion"] == "grief"
    assert s2["meaning"] == "Maeve grieves before leaving"
    assert s2["entities"] == ["Maeve"]  # nomes fora da bíblia são descartados


# ---------------------------------------------------------------- nada disso vem da tela
def test_idioma_e_detectado_e_campos_automaticos_ignoram_a_tela():
    with session_scope() as s:
        ch = Channel(name="auto", preset=Preset(visual_style="neon", period_look="archival").model_dump())
        s.add(ch)
        s.commit()
        s.refresh(ch)
        overrides = {"language": "en", "search_language": "pt", "visual_style": "pastel", "period_look": "archival",
                     "avg_scene_seconds": 5}
        script = ("Era uma vez uma família que vivia no sertão nordestino e plantava milho todos os anos, "
                  "esperando a chuva que nunca chegava.")
        cfg = _build_config(s, ch.id, "t", dict(overrides), "tts", script)
    assert cfg.lang == "pt" and cfg.language == "pt" and cfg.search_language == "en"
    assert cfg.visual_style == "" and cfg.period_look == "cinematic" and cfg.avg_scene_seconds == 5
