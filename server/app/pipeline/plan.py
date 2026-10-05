"""Etapa 3: planejamento de cenas → context_bible.json + plan.json (§6.3, MELHORIA_PRECISAO_VISUAL.md e
CONTEXTO_PROFUNDO_DO_ROTEIRO.md).

O código quebra a narração em "unidades" (frases ou pedaços de frase cortados em pausas), e o LLM
agrupa unidades contíguas em cenas e descreve cada uma. Assim, todo corte cai numa fronteira de
frase ou pausa, nunca no meio de uma palavra, e os tempos vêm sempre da transcrição.

A primeira chamada lê o roteiro inteiro e gera só a Bíblia de Contexto (tema, público, tom, linha do tempo de
contextos com época, lugar, sociedade, cultura material e anacronismos, entidades e lugares recorrentes). As
chamadas das cenas usam o mesmo sistema e o mesmo contexto (prefixo em cache, lido a 10% do preço) e recebem a
bíblia no pedido. Bíblia e cenas não cabem num único esquema de saída estruturada (a gramática compilada fica
grande demais para a API). Toda cena herda o bloco de contexto em que está.
"""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel

from ..directions import get_direction
from ..lang import name as lang_name
from ..providers.llm.base import call_llm
from ..worker.context import JobContext
from .allocate import allocate
from .context import (ContextBible, beat_for_unit, block_by_id, block_for_unit, finish_bible, neutral_bible,
                      period_allowed_styles, scene_context, strategy_order, video_look)
from .visual import (MEDIA_STYLE_RULES, NUMBER_HINTS, PLAN_SYSTEM, Style, StyleAllowance, brief_section,
                     sanitize_free_query, sanitize_queries, scene_style)

SENTENCE_END = re.compile(r"[.!?…]['\")\]”’]*$")
SOFT_BREAK = re.compile(r"[,;:—–-]['\")\]”’]*$")
WINDOW_UNITS = 90


# ---------- unidades ----------------------------------------------------------

def build_units(words: list[dict], avg: float) -> list[dict]:
    max_unit = avg * 1.3
    sentences, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        gap = words[i + 1]["start"] - w["end"] if i + 1 < len(words) else 0
        if SENTENCE_END.search(w["text"]) or gap > 0.6:
            sentences.append(cur)
            cur = []
    if cur:
        sentences.append(cur)

    units: list[list[dict]] = []
    for s in sentences:
        units.extend(_split_long(s, max_unit))
    return [{"i": n, "start": u[0]["start"], "end": u[-1]["end"], "text": " ".join(w["text"] for w in u)}
            for n, u in enumerate(units)]


def _split_long(ws: list[dict], max_unit: float) -> list[list[dict]]:
    if ws[-1]["end"] - ws[0]["start"] <= max_unit or len(ws) < 4:
        return [ws]
    # melhor ponto de corte: pontuação branda ou maior pausa, perto do meio
    mid = (ws[0]["start"] + ws[-1]["end"]) / 2
    best, best_score = None, float("-inf")
    for k in range(1, len(ws) - 1):
        gap = ws[k + 1]["start"] - ws[k]["end"]
        score = gap * 4 + (1.0 if SOFT_BREAK.search(ws[k]["text"]) else 0) - abs(ws[k]["end"] - mid) / max_unit
        if score > best_score:
            best, best_score = k, score
    assert best is not None
    return _split_long(ws[: best + 1], max_unit) + _split_long(ws[best + 1:], max_unit)


# ---------- schema do LLM ----------------------------------------------------

class Affinity(BaseModel):
    stock: float
    youtube: float
    ai: float


class SceneDraft(BaseModel):
    """Brief visual por cena (Etapa B)."""

    first_unit: int
    last_unit: int
    # interpretação: o que este momento transmite na história e quem (das entidades recorrentes) aparece
    meaning: str
    entities: list[str]
    literal: bool
    subject: str
    subject_category: Literal["food", "plant", "person", "animal", "object", "place", "activity", "nature",
                              "other"]
    must_show: list[str]
    setting: str
    action: str
    shot: str
    mood: str
    must_avoid: list[str]
    style_allowance: StyleAllowance
    allowed_styles: list[Style]
    style_reason: str
    visual_intent: str
    queries: list[str]
    kind: Literal["concreto", "abstrato", "evento_especifico"]
    energy: Literal["baixa", "media", "alta"]
    affinity: Affinity
    ai_kind: Literal["video", "image"]
    chapter_break: bool
    chapter_title: str | None
    highlight: str | None
    # recursos de edição (a direção decide o efeito): peso narrativo da cena e citação em tela cheia
    emphasis: Literal["none", "calm", "tension", "reveal"]
    quote: str | None
    overlay_language: str
    # CONTEXTO_PROFUNDO_DO_ROTEIRO.md §4
    context_id: str
    era_markers_to_show: list[str]
    archival_query: str
    timeless_alternative: str
    timeless_query: str


class PlanWindow(BaseModel):
    scenes: list[SceneDraft]
    music_mood: str | None


def _fallback_window(units: list[dict], avg: float, lang: str) -> PlanWindow:
    """Agrupamento determinístico, usado se o LLM falhar duas vezes na mesma janela."""
    scenes, start = [], 0
    while start < len(units):
        end = start
        while end + 1 < len(units) and units[end]["end"] - units[start]["start"] < avg * 0.8:
            end += 1
        text = " ".join(u["text"] for u in units[start:end + 1])
        kw = " ".join(re.findall(r"[A-Za-zÀ-ÿ]{5,}", text)[:2]).lower() or "landscape"
        scenes.append(SceneDraft(
            first_unit=units[start]["i"], last_unit=units[end]["i"], meaning=text[:160], entities=[], literal=True,
            subject=kw,
            subject_category="other", must_show=[kw], setting="", action="", shot="medium", mood="",
            must_avoid=[], style_allowance="real_only", allowed_styles=["real_footage"],
            style_reason="fallback automático", visual_intent=text[:200], queries=[kw, f"{kw} close up", f"{kw} outdoor"],
            kind="concreto", energy="media", affinity=Affinity(stock=0.7, youtube=0.2, ai=0.5), ai_kind="image",
            chapter_break=False, chapter_title=None, highlight=None, emphasis="none", quote=None,
            overlay_language=lang, context_id="", era_markers_to_show=[], archival_query="", timeless_alternative="",
            timeless_query=""))
        start = end + 1
    return PlanWindow(scenes=scenes, music_mood=None)


def _validate(window: PlanWindow, first: int, last: int, media_style: str = "real_preferred",
              bible: dict | None = None) -> PlanWindow:
    scenes = sorted(window.scenes, key=lambda s: s.first_unit)
    if not scenes:
        raise ValueError("nenhuma cena")
    fixed: list[SceneDraft] = []
    expected = first
    for s in scenes:
        if s.last_unit < expected:
            continue  # sobreposta por completo
        s.first_unit = expected  # fecha lacunas e cortes sobrepostos
        s.last_unit = min(max(s.last_unit, s.first_unit), last)
        fixed.append(s)
        expected = s.last_unit + 1
        if expected > last:
            break
    if expected <= last:
        fixed[-1].last_unit = last
    for s in fixed:
        block = None
        if bible:
            # a cena herda o bloco que contém suas unidades; um id válido diferente vale como sobrescrita pontual
            block = block_by_id(bible, s.context_id) or block_for_unit(bible, s.first_unit)
            s.context_id = block["id"] if block else ""
        feas = (block or {}).get("footage_feasibility")
        historical = (block or {}).get("setting_type") == "historical"
        allowance, allowed = scene_style(s.model_dump(), media_style)  # o preset "Só real" sempre vence
        if allowance == "real_preferred" and "real_footage" not in allowed:
            allowed = ["real_footage"] + allowed
        allowance, allowed = period_allowed_styles(feas, allowance, allowed, media_style)
        s.style_allowance, s.allowed_styles = allowance, allowed  # type: ignore[assignment]
        s.queries = sanitize_queries(s.subject, s.queries, s.must_avoid, s.allowed_styles)
        if historical:
            vocab = [v.lower() for v in (block or {}).get("search_vocabulary") or [] if v.strip()]

            def has_vocab(q: str) -> bool:
                words = q.split()
                return any(all(w in words for w in v.split()) for v in vocab)

            if vocab and not has_vocab(s.queries[0]):
                # a 1ª query é a de reconstituição (e a do YouTube antes de ~1890): precisa do vocabulário de época
                with_vocab = next((i for i, q in enumerate(s.queries) if has_vocab(q)), None)
                if with_vocab is not None:
                    s.queries.insert(0, s.queries.pop(with_vocab))
                else:
                    short = min(vocab, key=lambda v: len(v.split()))
                    s.queries[0] = " ".join((short.split() + s.queries[0].split())[:5])
            s.archival_query = (sanitize_free_query(s.archival_query, s.must_avoid, s.allowed_styles)
                                or " ".join(vocab[:1] + [s.subject]).strip())
            s.timeless_query = sanitize_free_query(s.timeless_query, s.must_avoid, s.allowed_styles)
            if not s.timeless_alternative.strip():
                s.timeless_query = ""
        else:
            s.archival_query = s.timeless_alternative = s.timeless_query = ""
            s.era_markers_to_show = []
    window.scenes = fixed
    return window


def load_bible(job_dir) -> dict:
    """Bíblia de Contexto da produção (context_bible.json); produções antigas só têm o video_brief.json."""
    for name in ("context_bible.json", "video_brief.json"):
        p = job_dir / name
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    return {}


BIBLE_REQUEST = ("Write ONLY the CONTEXT BIBLE for the whole script (no scenes yet). Cover all units "
                 "{first} to {last} with context blocks.")


def write_bible(ctx: JobContext, context: str, units: list[dict], economy: bool = False) -> dict:
    """Etapa 1: Bíblia de Contexto a partir do roteiro inteiro. Se o LLM falhar 2x, um bloco atemporal neutro."""
    user = BIBLE_REQUEST.format(first=units[0]["i"], last=units[-1]["i"])
    for attempt in range(2):
        try:
            if economy:
                ctx.progress(0.05, "Aguardando IA (lote econômico, até 24 h)")
            # etapa própria ("bible"): a interpretação do roteiro inteiro merece mais raciocínio que as cenas
            parsed, usage = call_llm("bible", system=PLAN_SYSTEM, context=context, user=user, schema=ContextBible,
                                     max_tokens=16000, batch=economy)
            ctx.record_llm(usage)
            return finish_bible(parsed.model_dump(), len(units), units)
        except Exception as e:  # noqa: BLE001
            if attempt == 1:
                ctx.issue("STEP_FAILED", "Bíblia de Contexto falhou; usando um contexto atemporal neutro",
                          detail=repr(e), severity="warning")
    return neutral_bible(ctx.config.title, "", len(units))


def plan_context(ctx: JobContext, units: list[dict]) -> str:
    """Bloco de contexto da produção (em cache): configurações + roteiro inteiro em unidades numeradas.

    Gerado uma vez por produção e reaproveitado byte a byte em todas as janelas e retentativas. Nada de
    timestamps por palavra: só as unidades com início e fim.
    """
    direction = get_direction(ctx.config.direction)
    lang = ctx.config.lang
    var = direction.params.get("duration_variation", 0.3)
    settings = "\n".join([
        "PRODUCTION SETTINGS",
        f"- Title: {ctx.config.title}",
        f"- Target average scene duration: {ctx.config.avg_scene_seconds:.1f}s, varying ±{var:.0%}.",
        f"- Video language: {lang_name(lang)} ({lang}). Number format: {NUMBER_HINTS.get(lang, '').strip()}",
        f"- Channel media style: {MEDIA_STYLE_RULES.get(ctx.config.media_style, MEDIA_STYLE_RULES['real_preferred'])}",
        "- Visual style and period look: decided by the CONTEXT BIBLE from the script itself.",
        "- Direction:",
        direction.prompt.strip(),
    ])
    listing = "\n".join(f"[{u['i']}|{u['start']:.1f}-{u['end']:.1f}] {u['text']}" for u in units)
    return f"{settings}\n\nSCRIPT UNITS\n{listing}"


def run(ctx: JobContext) -> str:
    transcript = ctx.read_json("transcript.json")
    avg = ctx.config.avg_scene_seconds
    units = build_units(transcript["words"], avg)
    ctx.write_json("units.json", units)

    lang = ctx.config.lang
    media_style = ctx.config.media_style
    economy = bool(getattr(ctx.config, "llm_economy", False))
    windows = [units[i:i + WINDOW_UNITS] for i in range(0, len(units), WINDOW_UNITS)]
    context = plan_context(ctx, units)

    def plan_window(wi: int, bible: dict) -> PlanWindow:
        win = windows[wi]
        user = (f"{brief_section(bible)}\nPlan units {win[0]['i']} to {win[-1]['i']} "
                f"(window {wi + 1} of {len(windows)}).")
        for attempt in range(2):
            try:
                if economy:
                    ctx.progress(0.3, "Aguardando IA (lote econômico, até 24 h)")
                parsed, usage = call_llm("plan", system=PLAN_SYSTEM, context=context, user=user, schema=PlanWindow,
                                         max_tokens=16000, batch=economy)
                ctx.record_llm(usage)
                return _validate(parsed, win[0]["i"], win[-1]["i"], media_style, bible)
            except Exception as e:  # noqa: BLE001
                if attempt == 1:
                    ctx.issue("STEP_FAILED", f"Planejamento por IA falhou na parte {wi + 1}; usando agrupamento "
                              "automático", detail=repr(e), severity="warning")
        return _validate(_fallback_window(win, avg, lang), win[0]["i"], win[-1]["i"], media_style, bible)

    # Etapa 1: Bíblia de Contexto (1 chamada por produção; reaproveitada se a etapa for retomada)
    ctx.progress(0.05, "Lendo o roteiro: época, lugar, pessoas e ambiente")
    bible = ctx.read_json("context_bible.json") if (ctx.dir / "context_bible.json").exists() else None
    if bible is None:
        bible = write_bible(ctx, context, units, economy)
        ctx.write_json("context_bible.json", bible)
    # Etapa 2: cenas, já com a bíblia (roteiros longos: janelas em paralelo)
    ctx.progress(0.3, "Planejando as cenas no contexto do roteiro")
    if len(windows) == 1:
        results = [plan_window(0, bible)]
    else:
        from concurrent.futures import ThreadPoolExecutor

        # a 1ª janela sozinha grava o prefixo (sistema + roteiro, ~23 mil tokens) no cache do prompt; as demais,
        # em paralelo, leem do cache a 1/12 do preço em vez de cada uma gravar de novo
        first = plan_window(0, bible)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = [first, *pool.map(lambda wi: plan_window(wi, bible), range(1, len(windows)))]
    drafts: list[SceneDraft] = [sc for r in results for sc in r.scenes]
    mood = next((r.music_mood for r in results if r.music_mood), None)

    duration = transcript["audio_duration"]
    scenes = []
    for n, d in enumerate(drafts):
        u0, u1 = units[d.first_unit], units[d.last_unit]
        prev_end = units[d.first_unit - 1]["end"] if d.first_unit > 0 else 0.0
        start = 0.0 if n == 0 else round((prev_end + u0["start"]) / 2, 3)  # corta no meio da pausa
        block = block_by_id(bible, d.context_id) or block_for_unit(bible, d.first_unit)
        context = scene_context(block)
        beat = beat_for_unit(bible, d.first_unit) or {}
        known = {(e.get("name") or "").lower(): e["name"] for e in bible.get("recurring_entities") or []
                 if e.get("name")}
        scene = {
            "id": f"s{n + 1:03d}", "start": start, "end": None,
            "text": " ".join(u["text"] for u in units[d.first_unit:d.last_unit + 1]),
            **d.model_dump(exclude={"first_unit", "last_unit"}),
            "units": [d.first_unit, d.last_unit], "speech_end": u1["end"],
            "entities": [known[n.lower()] for n in d.entities if n.lower() in known],
            "beat": beat.get("beat", ""), "beat_emotion": beat.get("emotion", ""),
            "context_id": context.get("id", ""), "context": context,
            # must_avoid efetivo = confusões da cena + anacronismos do bloco (as queries usam só as confusões)
            "anachronisms": list(context.get("anachronisms") or []),
        }
        if context.get("setting_type") == "historical":
            scene["strategy_order"] = strategy_order(context.get("footage_feasibility"), media_style)
            scene["queries_by_strategy"] = {
                "period_reenactment": scene["queries"][:1], "archival": [scene["archival_query"]],
                "timeless": [scene["timeless_query"]] if scene["timeless_query"] else [],
                "variation": scene["queries"][2:3] or scene["queries"][1:2]}
        scenes.append(scene)
    for a, b in zip(scenes, scenes[1:]):
        a["end"] = b["start"]
    scenes[-1]["end"] = round(duration, 3)

    allocation = allocate(scenes, ctx.config)
    for s in scenes:
        s["source"] = allocation[s["id"]]
    visual_style, period_look = video_look(bible, ctx.config)
    out = ctx.write_json("plan.json", {"music_mood": mood or ctx.config.music.mood, "video_language": lang,
                                       "visual_style": visual_style, "period_look": period_look,
                                       "summary": bible.get("summary", ""), "scenes": scenes})
    ctx.progress(1.0, f"{len(scenes)} cenas planejadas")
    return str(out)
