"""Etapas [3] e [4] do funil: folha de miniaturas validada pela IA de visão, com desempate opcional.

A IA descreve o que vê (`seen`), classifica o estilo (`realism`), aponta franquias, se o assunto aparece e o
que há de proibido; a nota final é calculada no código (visual.score_row) conforme os estilos aceitos na cena.
Modo rápido: 1 chamada por fonte (sem desempate). Sem Gemini, ou com a cota esgotada, vale o ranking de texto.

Cache de visão (Fase A3): a chave é a requisição efetiva — prompt renderizado (narração, intenção, must_show,
must_avoid, contexto, cena anterior, estilos aceitos), bytes da folha de miniaturas, modelos configurados e versão
do prompt/schema. Bytes diferentes (ex.: outra imagem gerada com o mesmo prompt) ou outra exigência = nova
avaliação. O que a IA VIU em cada candidato (descrição observável) fica à parte, em asset_description, e pode ser
reaproveitado por outras cenas sem herdar a adequação.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlmodel import select

from ... import singleflight
from ...db import session_scope
from ...models import AssetDescription, VisionCache
from ...providers.llm import gemini, vision
from ...providers.stock.base import Candidate
from ..visual import VISION_PROMPT, VisionSheet, score_row, vision_context
from .sheet import build_sheet, frame_bytes, pick_frames

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
OBSERVED_FIELDS = ("seen", "realism", "brand_or_franchise", "is_timeless")  # só o que se vê, sem julgamento
VISION_CACHE_VERSION = "v6"  # mude ao alterar VISION_PROMPT, VisionRow ou a montagem da folha


def _schema_hash() -> str:
    return hashlib.sha256(json.dumps(VisionSheet.model_json_schema(), sort_keys=True).encode()).hexdigest()[:16]


def vision_cache_key(stage: str, prompt: str, sheet: bytes, model: str, refs: list[str] | None = None) -> str:
    """Requisição efetiva: etapa, versão, modelos configurados, schema, prompt renderizado, bytes da folha e a
    identidade de cada linha (frames que falharam viram células vazias iguais para candidatos diferentes)."""
    raw = json.dumps([VISION_CACHE_VERSION, stage, vision.signature(model), _schema_hash(),
                      hashlib.sha256(sheet).hexdigest(), prompt, refs or []], ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def description_ref(c: Candidate, frames: list) -> str:
    """Identidade do que foi visto: bytes (imagem local) ou candidato + URLs dos frames (miniaturas da API)."""
    if c.provider == "local":
        return "sha256:" + hashlib.sha256(b"".join(frame_bytes(f) for f in frames)).hexdigest()
    return "ref:" + hashlib.sha1("|".join([c.key] + [str(f) for f in frames]).encode()).hexdigest()


def _render_prompt(ctx: SceneContext, n: int, frames: int) -> str:
    return VISION_PROMPT.format(
        topic=ctx.topic or "(unknown)", visual_world=ctx.visual_world or "(real life)", text=ctx.text,
        meaning=ctx.meaning or ctx.intent, beat=ctx.beat or "(not available)",
        subject=ctx.subject or ctx.intent, must_show=json.dumps(ctx.must_show, ensure_ascii=False),
        must_avoid=json.dumps(ctx.must_avoid, ensure_ascii=False), intent=ctx.intent,
        allowed_styles=", ".join(ctx.allowed_styles), style_reason=ctx.style_reason or "-",
        previous=ctx.previous or "(none)", context=vision_context(ctx.context), n=n, frames=frames)


def _store_descriptions(rows: list[list], cands: list[Candidate], result: list[dict], provider: str) -> None:
    with session_scope() as s:
        for row, c, d in zip(rows, cands, result):
            if d.get("realism") == "unknown":
                continue
            content = hashlib.sha256(b"".join(frame_bytes(f) for f in row)).hexdigest()
            s.merge(AssetDescription(ref=description_ref(c, row), candidate_key=c.key, content_hash=content,
                                     seen=str(d.get("seen") or "")[:300], realism=str(d.get("realism") or ""),
                                     brand_or_franchise=bool(d.get("brand_or_franchise")),
                                     is_timeless=bool(d.get("is_timeless")), provider=provider,
                                     prompt_version=VISION_CACHE_VERSION))
        s.commit()


def observed_descriptions(cands: list[Candidate], frames_per_candidate: int) -> int:
    """Preenche c.observed com o que uma IA já viu nesse candidato (outra cena). Devolve quantos tinham."""
    refs = {description_ref(c, pick_frames(c, frames_per_candidate)[0]): c for c in cands}
    if not refs:
        return 0
    found = 0
    with session_scope() as s:
        for d in s.exec(select(AssetDescription).where(AssetDescription.ref.in_(list(refs)))):
            refs[d.ref].observed = d.seen
            found += 1
    return found


def _rate(stage: str, rows: list[list], cands: list[Candidate], ctx: SceneContext, model: str,
          stats: dict) -> list[dict]:
    """Avaliação por linha (cache primeiro): [{row, seen, realism, ..., best_frame, score}] na ordem de cands.

    A nota é recalculada a cada uso, porque depende dos estilos aceitos na cena.
    """
    frames = max((len(r) for r in rows), default=1)
    prompt = _render_prompt(ctx, len(rows), frames)
    timer = stats.get("_timer")
    if timer:
        with timer.track(stats["_scene"], "folha de miniaturas"):
            image = build_sheet(rows)
    else:
        image = build_sheet(rows)
    key = vision_cache_key(stage, prompt, image, model, [description_ref(c, r) for c, r in zip(cands, rows)])

    def lookup():
        with session_scope() as s:
            cached = s.get(VisionCache, key)
            return cached.result if cached else None

    def compute():
        if timer:
            with timer.track(stats["_scene"], "chamada de visão"):
                sheet, cost, provider = vision.rate_sheet(image, prompt, model, schema=VisionSheet,
                                                          budget=stats.get("_budget"), record=stats.get("_record"))
        else:
            sheet, cost, provider = vision.rate_sheet(image, prompt, model, schema=VisionSheet,
                                                      budget=stats.get("_budget"), record=stats.get("_record"))
        stats.setdefault("vision_providers", {})
        stats["vision_providers"][provider] = stats["vision_providers"].get(provider, 0) + 1
        stats["vision_calls"] = stats.get("vision_calls", 0) + 1
        if provider not in ("claude", "openai") or not stats.get("_record"):  # pagas com _record já entraram
            stats["vision_cost"] = stats.get("vision_cost", 0.0) + cost
        by_row = {c.row: c for c in sheet.candidates}
        out = []
        for i, row in enumerate(rows, start=1):
            r = by_row.get(i)
            if r is None:
                out.append({"row": i, "seen": "sem avaliação", "realism": "unknown", "brand_or_franchise": False,
                            "subject_visible": False, "forbidden_present": [], "context_match": 0, "quality": 0,
                            "best_frame": 0})
                continue
            d = r.model_dump()
            d["best_frame"] = min(max(0, int(r.best_frame)), max(0, len(row) - 1))
            out.append(d)
        payload = {"candidates": out, "provider": provider, "model_config": vision.signature(model),
                   "version": VISION_CACHE_VERSION}
        with session_scope() as s:
            s.merge(VisionCache(key=key, stage=stage, result=payload))
            s.commit()
        try:
            _store_descriptions(rows, cands, out, provider)
        except Exception as e:  # noqa: BLE001 — a descrição é um extra; nunca derruba a avaliação
            log.warning("descrição observável não foi salva: %s", e)
        return payload

    result = [dict(d) for d in singleflight.run(f"vision:{key}", lookup, compute, seconds=180)["candidates"]]
    for d in result:
        d["score"] = (0.0 if d.get("realism") == "unknown"
                      else score_row(d, ctx.allowance, ctx.allowed_styles, ctx.setting_type))
    return result


def rate_local_image(path: Path, ctx: SceneContext, model: str, stats: dict, key_hint: str = "") -> dict:
    """Valida uma imagem já existente (ex.: gerada pela Darkvi) com o mesmo prompt de visão.

    A identidade é o conteúdo do arquivo (key_hint é ignorado; mantido por compatibilidade)."""
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
    fake = Candidate(provider="local", external_id=digest, title="", duration=0, width=0, height=0, page_url="",
                     thumbnail=None, is_image=True)
    return _rate("generated", [[path]], [fake], ctx, model, stats)[0]


def choose(ranked: list[Candidate], ctx: SceneContext, cfg: dict, stats: dict) -> Choice:
    """ranked: candidatos já filtrados e ordenados pelo texto (top N)."""
    top = ranked[0]

    def mid(c: Candidate) -> float:
        return c.positions()[len(c.preview_frames) // 2] if c.preview_frames else 0.25

    text_choice = Choice(top, top.score, mid(top), "ranking de texto", "text",
                         alternatives=[(c, c.score, mid(c), None) for c in ranked[1:]])
    if not vision.available():
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
    except vision.VisionBudgetExceeded:
        stats["vision_budget"] = True
        text_choice.method = "text_fallback"
        text_choice.vision_calls = stats.get("vision_calls", 0) - calls_before
        return text_choice
    except Exception as e:  # noqa: BLE001 — visão nunca derruba a produção
        log.warning("IA de visão falhou: %s", e)
        stats["vision_failed"] = stats.get("vision_failed", 0) + 1
        stats["vision_error"] = str(e)[:300]
        text_choice.method = "text_fallback"
        text_choice.vision_calls = stats.get("vision_calls", 0) - calls_before
        return text_choice
