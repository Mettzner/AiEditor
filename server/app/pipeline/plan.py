"""Etapa 3: planejamento de cenas → plan.json (§6.3).

O código quebra a narração em "unidades" (frases ou pedaços de frase cortados em pausas), e o LLM
agrupa unidades contíguas em cenas e descreve cada uma. Assim, todo corte cai numa fronteira de
frase ou pausa, nunca no meio de uma palavra, e os tempos vêm sempre da transcrição.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from ..directions import get_direction
from ..providers.llm.base import get_llm
from ..worker.context import JobContext
from .allocate import allocate

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
    first_unit: int
    last_unit: int
    visual_intent: str
    kind: Literal["concreto", "abstrato", "evento_especifico"]
    energy: Literal["baixa", "media", "alta"]
    affinity: Affinity
    queries: list[str]
    ai_kind: Literal["video", "image"]
    chapter_break: bool
    chapter_title: str | None
    highlight: str | None


class PlanWindow(BaseModel):
    scenes: list[SceneDraft]
    music_mood: str | None


SYSTEM = """Você é o diretor de edição de um canal de vídeos narrados (B-roll sobre narração).
Recebe a narração dividida em unidades numeradas, com tempo e duração, e devolve as cenas do vídeo.

Regras gerais:
- Cada cena é um intervalo contíguo de unidades [first_unit, last_unit]. As cenas cobrem TODAS as unidades da janela, em ordem, sem lacunas nem sobreposição.
- Duração média alvo por cena: {avg:.1f}s, variação ±{var:.0%}. Uma unidade nunca é dividida; uma cena pode ter uma unidade só.
- visual_intent: descrição concreta e filmável do que aparece na tela (sujeito, ação, ambiente, luz, enquadramento), em português. Mantém o estilo visual do canal.
- kind: "concreto" (algo filmável e genérico), "abstrato" (conceito, emoção, ideia) ou "evento_especifico" (fato, lugar ou pessoa real e identificável).
- affinity (0 a 1): quão bem cada fonte serve a cena. stock = bancos de vídeo genéricos; youtube = material real de arquivo/notícia; ai = geração por IA (bom para abstratos, cenas impossíveis de filmar ou muito específicas visualmente).
- queries: exatamente 3 buscas curtas (2 a 6 palavras) em {search_lang} para bancos de vídeo: 1) literal, 2) atmosférica, 3) alternativa/lateral. Sem nomes próprios que um banco genérico não teria.
- ai_kind: "video" se o movimento é essencial à cena; "image" se um quadro com movimento de câmera lento basta.
- music_mood: só na primeira janela, descreva o clima musical do vídeo inteiro em 3 a 5 palavras; nas demais, null.

Estilo visual do canal: {style}

{direction}"""


def _fallback_window(units: list[dict], avg: float) -> PlanWindow:
    """Agrupamento determinístico, usado se o LLM falhar duas vezes na mesma janela."""
    scenes, start = [], 0
    while start < len(units):
        end = start
        while end + 1 < len(units) and units[end]["end"] - units[start]["start"] < avg * 0.8:
            end += 1
        text = " ".join(u["text"] for u in units[start:end + 1])
        kw = " ".join(re.findall(r"[A-Za-zÀ-ÿ]{5,}", text)[:4]) or "cinematic landscape"
        scenes.append(SceneDraft(first_unit=units[start]["i"], last_unit=units[end]["i"], visual_intent=text[:200],
                                 kind="concreto", energy="media", affinity=Affinity(stock=0.7, youtube=0.2, ai=0.5),
                                 queries=[kw, f"{kw} cinematic", "atmospheric b-roll"], ai_kind="image",
                                 chapter_break=False, chapter_title=None, highlight=None))
        start = end + 1
    return PlanWindow(scenes=scenes, music_mood=None)


def _validate(window: PlanWindow, first: int, last: int) -> PlanWindow:
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
        s.queries = [q.strip() for q in s.queries if q.strip()][:3] or [s.visual_intent[:60]]
    window.scenes = fixed
    return window


def run(ctx: JobContext) -> str:
    transcript = ctx.read_json("transcript.json")
    avg = ctx.config.avg_scene_seconds
    direction = get_direction(ctx.config.direction)
    units = build_units(transcript["words"], avg)
    ctx.write_json("units.json", units)

    llm, cfg = get_llm("plan")
    system = SYSTEM.format(avg=avg, var=direction.params.get("duration_variation", 0.3),
                           search_lang=ctx.config.search_language, style=ctx.config.visual_style or "(livre)",
                           direction=direction.prompt)
    drafts: list[SceneDraft] = []
    mood = None
    windows = [units[i:i + WINDOW_UNITS] for i in range(0, len(units), WINDOW_UNITS)]
    for wi, win in enumerate(windows):
        ctx.check_cancel()
        ctx.progress(wi / len(windows), f"Planejando cenas (parte {wi + 1}/{len(windows)})")
        listing = "\n".join(f"[{u['i']}] {u['start']:.2f}–{u['end']:.2f} ({u['end'] - u['start']:.1f}s) {u['text']}"
                            for u in win)
        user = (f"Título: {ctx.config.title}\n\nRoteiro completo (contexto):\n{ctx.script}\n\n"
                f"--- Janela {wi + 1}/{len(windows)}: unidades {win[0]['i']} a {win[-1]['i']} ---\n{listing}")
        result = None
        for attempt in range(2):
            try:
                parsed, usage = llm.structured(model=cfg["model"], system=system, user=user, schema=PlanWindow,
                                               effort=cfg.get("effort"))
                ctx.add_cost(usage.cost)
                result = _validate(parsed, win[0]["i"], win[-1]["i"])
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 1:
                    ctx.issue("STEP_FAILED", f"Planejamento por IA falhou na parte {wi + 1}; usando agrupamento "
                              "automático", detail=repr(e), severity="warning")
        if result is None:
            result = _fallback_window(win, avg)
        mood = mood or result.music_mood
        drafts.extend(result.scenes)

    duration = transcript["audio_duration"]
    scenes = []
    for n, d in enumerate(drafts):
        u0, u1 = units[d.first_unit], units[d.last_unit]
        prev_end = units[d.first_unit - 1]["end"] if d.first_unit > 0 else 0.0
        start = 0.0 if n == 0 else round((prev_end + u0["start"]) / 2, 3)  # corta no meio da pausa
        scenes.append({
            "id": f"s{n + 1:03d}", "start": start, "end": None,
            "text": " ".join(u["text"] for u in units[d.first_unit:d.last_unit + 1]),
            **d.model_dump(exclude={"first_unit", "last_unit"}),
            "units": [d.first_unit, d.last_unit], "speech_end": u1["end"],
        })
    for a, b in zip(scenes, scenes[1:]):
        a["end"] = b["start"]
    scenes[-1]["end"] = round(duration, 3)

    allocation = allocate(scenes, ctx.config)
    for s in scenes:
        s["source"] = allocation[s["id"]]
    out = ctx.write_json("plan.json", {"music_mood": mood or ctx.config.music.mood, "scenes": scenes})
    ctx.progress(1.0, f"{len(scenes)} cenas planejadas")
    return str(out)
