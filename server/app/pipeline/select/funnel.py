"""Etapas [3] e [4] do funil: folha de miniaturas validada pela IA de visão, com desempate opcional.

A IA descreve o que vê (`seen`), classifica o estilo (`realism`), aponta franquias, se o assunto aparece e o
que há de proibido; a nota final é calculada no código (visual.score_row) conforme os estilos aceitos na cena.
Modo rápido: 1 chamada por fonte (sem desempate). Notas em cache (vision_cache). Sem Gemini, ou com a cota
esgotada, vale o ranking de texto, sem espera.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from ...db import session_scope
from ...models import VisionCache
from ...providers.llm import gemini
from ...providers.stock.base import Candidate
from ..visual import VISION_PROMPT, VisionSheet, compact_json, score_row, vision_context
from .sheet import build_sheet, pick_frames

log = logging.getLogger("aieditor.select")


@dataclass
class Choice:
    candidate: Candidate
    score: float  # 0–10 (calculada a partir da visão) ou nota do ranking de texto
    best_pos: float  # posição relativa (0–1) do melhor frame no clipe
    reason: str  # o que a IA viu (`seen`) ou "ranking de texto"
    method: str  # vision | vision_tiebreak | text | text_fallback
    vision_calls: int = 0
    # próximos colocados (candidato, nota, posição, estilo): pós-processamento e falhas de download
    alternatives: list[tuple] = field(default_factory=list)
    realism: str | None = None
    rejected_seen: list[str] = field(default_factory=list)  # `seen` dos melhores reprovados (reescrita)
    style_rejected: int = 0  # candidatos zerados pela visão por estilo incompatível ou franquia
    # época do vencedor (CONTEXTO_PROFUNDO_DO_ROTEIRO.md §6): era_consistent, anachronisms_seen, is_timeless
    era: dict = field(default_factory=dict)


@dataclass
class SceneContext:
    intent: str
    text: str
    style: str
    previous: str
    subject: str = ""
    must_show: list[str] = field(default_factory=list)
    must_avoid: list[str] = field(default_factory=list)
    topic: str = ""
    visual_world: str = ""
    allowance: str = "real_only"
    allowed_styles: list[str] = field(default_factory=lambda: ["real_footage"])
    style_reason: str = ""
    context: dict = field(default_factory=dict)  # contexto do bloco (época, lugar, anacronismos)
    meaning: str = ""  # o que o momento transmite na história (planejamento)
    beat: str = ""  # trecho da estrutura narrativa em que a cena está (bíblia)

    @property
    def setting_type(self) -> str | None:
        return self.context.get("setting_type") if self.context else None


ERA_FIELDS = ("era_consistent", "anachronisms_seen", "is_timeless", "place_consistent")
CONTEXT_KEY_FIELDS = ("setting_type", "era", "era_label", "place", "clothing", "architecture", "lighting",
                      "transport", "anachronisms")


def _cache_key(stage: str, cands: list[Candidate], ctx: SceneContext, model: str, frames: int) -> str:
    context = compact_json({k: ctx.context.get(k) for k in CONTEXT_KEY_FIELDS}) if ctx.context else ""
    raw = "|".join([stage, "v5", str(frames), ",".join(c.key for c in cands), ctx.intent, ctx.subject,
                    ",".join(ctx.must_avoid), ctx.allowance, ",".join(ctx.allowed_styles), ctx.style, model, context,
                    ctx.meaning, ctx.beat])
    return hashlib.sha1(raw.encode()).hexdigest()


def _rate(stage: str, rows: list[list], cands: list[Candidate], ctx: SceneContext, model: str,
          stats: dict) -> list[dict]:
    """Avaliação por linha (cache primeiro): [{row, seen, realism, ..., best_frame, score}] na ordem de cands.

    A nota é recalculada a cada uso, porque depende dos estilos aceitos na cena.
    """
    frames = max((len(r) for r in rows), default=1)
    key = _cache_key(stage, cands, ctx, model, frames)
    with session_scope() as s:
        cached = s.get(VisionCache, key)
        result = cached.result["candidates"] if cached else None
    if result is None:
        prompt = VISION_PROMPT.format(
            topic=ctx.topic or "(unknown)", visual_world=ctx.visual_world or "(real life)", text=ctx.text,
            meaning=ctx.meaning or ctx.intent, beat=ctx.beat or "(not available)",
            subject=ctx.subject or ctx.intent, must_show=json.dumps(ctx.must_show, ensure_ascii=False),
            must_avoid=json.dumps(ctx.must_avoid, ensure_ascii=False), intent=ctx.intent,
            allowed_styles=", ".join(ctx.allowed_styles), style_reason=ctx.style_reason or "-",
            previous=ctx.previous or "(none)", context=vision_context(ctx.context), n=len(rows), frames=frames)
        timer = stats.get("_timer")
        if timer:
            with timer.track(stats["_scene"], "folha de miniaturas"):
                image = build_sheet(rows)
            with timer.track(stats["_scene"], "chamada de visão"):
                sheet, cost = gemini.rate_sheet(image, prompt, model, schema=VisionSheet)
        else:
            sheet, cost = gemini.rate_sheet(build_sheet(rows), prompt, model, schema=VisionSheet)
        stats["vision_calls"] = stats.get("vision_calls", 0) + 1
        stats["vision_cost"] = stats.get("vision_cost", 0.0) + cost
        by_row = {c.row: c for c in sheet.candidates}
        result = []
        for i, row in enumerate(rows, start=1):
            r = by_row.get(i)
            if r is None:
                result.append({"row": i, "seen": "sem avaliação", "realism": "unknown", "brand_or_franchise": False,
                               "subject_visible": False, "forbidden_present": [], "context_match": 0, "quality": 0,
                               "best_frame": 0})
                continue
            d = r.model_dump()
            d["best_frame"] = min(max(0, int(r.best_frame)), max(0, len(row) - 1))
            result.append(d)
        with session_scope() as s:
            s.merge(VisionCache(key=key, stage=stage, result={"candidates": result}))
            s.commit()
    for d in result:
        d["score"] = (0.0 if d.get("realism") == "unknown"
                      else score_row(d, ctx.allowance, ctx.allowed_styles, ctx.setting_type))
    return result


def rate_local_image(path: Path, ctx: SceneContext, model: str, stats: dict, key_hint: str) -> dict:
    """Valida uma imagem já existente (ex.: gerada pela Darkvi) com o mesmo prompt de visão."""
    fake = Candidate(provider="local", external_id=key_hint, title="", duration=0, width=0, height=0, page_url="",
                     thumbnail=None, is_image=True)
    return _rate("generated", [[path]], [fake], ctx, model, stats)[0]


def choose(ranked: list[Candidate], ctx: SceneContext, cfg: dict, stats: dict) -> Choice:
    """ranked: candidatos já filtrados e ordenados pelo texto (top N)."""
    top = ranked[0]

    def mid(c: Candidate) -> float:
        return c.positions()[len(c.preview_frames) // 2] if c.preview_frames else 0.25

    text_choice = Choice(top, top.score, mid(top), "ranking de texto", "text",
                         alternatives=[(c, c.score, mid(c), None) for c in ranked[1:]])
    if not gemini.available():
        return text_choice
    model = cfg["gemini_model"]
    calls_before = stats.get("vision_calls", 0)
    try:
        frames = [pick_frames(c, cfg["frames_per_candidate"]) for c in ranked]
        notes = _rate("sheet", [f[0] for f in frames], ranked, ctx, model, stats)
        order = sorted(range(len(ranked)), key=lambda i: notes[i]["score"], reverse=True)
        zeroed = sum(1 for n in notes if n["score"] == 0 and n.get("realism") != "unknown")
        first = order[0]
        gap = notes[first]["score"] - (notes[order[1]]["score"] if len(order) > 1 else 0)

        def build(cands, nts, idx, frs, method, extra_alts=()):
            n = nts[idx]
            pos = frs[idx][1][n["best_frame"]] if frs[idx][1] else mid(cands[idx])
            others = sorted((i for i in range(len(cands)) if i != idx), key=lambda i: nts[i]["score"], reverse=True)
            alts = [(cands[i], nts[i]["score"], frs[i][1][nts[i]["best_frame"]] if frs[i][1] else mid(cands[i]),
                     nts[i].get("realism"), {k: nts[i].get(k) for k in ERA_FIELDS})
                    for i in others if nts[i]["score"] > 0]
            return Choice(cands[idx], n["score"], pos, n.get("seen", ""), method,
                          stats.get("vision_calls", 0) - calls_before, alts + list(extra_alts), n.get("realism"),
                          [nts[i].get("seen", "") for i in others[:3]], zeroed, {k: n.get(k) for k in ERA_FIELDS})

        accepted = notes[first]["score"] >= cfg["accept_score"] and gap >= cfg["accept_gap"]
        if accepted or not cfg.get("tiebreak", True):
            return build(ranked, notes, first, frames, "vision")

        # [4] desempate (só no modo preciso): top 3 com mais frames, entre os que não zeraram
        finalists_idx = [i for i in order[: cfg["tiebreak_top"]] if notes[i]["score"] > 0]
        if len(finalists_idx) < 2:
            return build(ranked, notes, first, frames, "vision")
        finalists = [ranked[i] for i in finalists_idx]
        frames2 = [pick_frames(c, cfg["tiebreak_frames"]) for c in finalists]
        notes2 = _rate("tiebreak", [f[0] for f in frames2], finalists, ctx, model, stats)
        best = max(range(len(finalists)), key=lambda i: notes2[i]["score"])
        rest = [(ranked[i], notes[i]["score"], mid(ranked[i]), notes[i].get("realism"),
                 {k: notes[i].get(k) for k in ERA_FIELDS})
                for i in order[cfg["tiebreak_top"]:] if notes[i]["score"] > 0]
        choice = build(finalists, notes2, best, frames2, "vision_tiebreak", rest)
        choice.rejected_seen = [notes[i].get("seen", "") for i in order if ranked[i] is not choice.candidate][:3]
        return choice
    except gemini.GeminiQuotaExhausted as e:
        stats["vision_quota"] = str(e)
        text_choice.method = "text_fallback"
        return text_choice
    except Exception as e:  # noqa: BLE001 — visão nunca derruba a produção
        log.warning("IA de visão falhou: %s", e)
        stats["vision_failed"] = stats.get("vision_failed", 0) + 1
        stats["vision_error"] = str(e)[:300]
        text_choice.method = "text_fallback"
        text_choice.vision_calls = stats.get("vision_calls", 0) - calls_before
        return text_choice
