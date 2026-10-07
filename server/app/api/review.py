"""Revisão da produção (Fase F): cena a cena, com ações que alteram só o que depende delas.

Leitura: narração, intenção, entidades, papel visual, origem, estado de validação, alternativas com motivo,
referências do YouTube, afirmações sem fonte e métricas por fonte. Ações (com a produção parada): trocar pelo
alternativo ou por mídia autorizada, fixar, editar o trecho, buscar mais para a cena (dentro do teto de gasto),
corrigir interpretação (assunto, entidade, papel visual), corrigir o idioma e renderizar de novo. A retomada usa
os hashes de dependência (app/artifacts.py): só as etapas afetadas rodam; o render refaz só as cenas alteradas.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from ..config import job_dir, load_settings
from ..db import get_session
from ..lang import LANGUAGES
from ..models import AuthorizedMedia, Production, ProductionStep, now
from ..pipeline.provenance import build_manifest, credits_text
from ..pipeline.report import config_min_score, scene_validation
from ..pipeline.semantics import check_scene, needs_review
from ..pipeline.validation import classify, policy

router = APIRouter(tags=["review"])
EDITABLE = ("done", "failed", "cancelled")


def _load(job: Path, name: str, default=None):
    p = job / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def _write(job: Path, name: str, data) -> None:
    from ..fsutil import atomic_write_text

    atomic_write_text(job / name, json.dumps(data, indent=2, ensure_ascii=False))


def _production(s: Session, pid: int, editable: bool = False) -> Production:
    p = s.get(Production, pid)
    if not p:
        raise HTTPException(404, "Produção não encontrada")
    if editable and p.status not in EDITABLE:
        raise HTTPException(409, "Espere a produção terminar (ou cancele) para editar")
    return p


def _flags(scene: dict, entry: dict, validation: dict, pending_claims: set[str], min_score: float) -> list[str]:
    flags = []
    if needs_review(scene) or scene.get("interpretation_confidence") == "uncertain":
        flags.append("identity_uncertain")
    if validation.get("score_basis") != "vision" and entry.get("asset") and entry.get("source") != "chart":
        flags.append("no_vision")
    score = entry.get("score")
    if isinstance(score, (int, float)) and validation.get("score_basis") == "vision" and score < min_score:
        flags.append("low_score")
    if any(c in pending_claims for c in scene.get("claim_ids") or []):
        flags.append("claim_without_source")
    if validation.get("status") == "review_required":
        flags.append("review_required")
    return flags


@router.get("/productions/{production_id}/review")
def review(production_id: int, s: Session = Depends(get_session)):
    p = _production(s, production_id)
    job = job_dir(production_id)
    plan = _load(job, "plan.json")
    if not plan:
        raise HTTPException(404, "A produção ainda não chegou ao planejamento de cenas")
    selection = _load(job, "selection.json", {})
    bible = _load(job, "context_bible.json", {})
    refs = (_load(job, "references.json", {}) or {}).get("scenes", {})
    entities = {e["id"]: e for e in bible.get("entities") or []}
    claims = {c["id"]: c for c in bible.get("claims") or []}
    pending = {cid for cid, c in claims.items() if c.get("needs_source")}
    min_score = config_min_score(job)
    rows = []
    by_source: dict[str, dict] = {}
    for sc in plan["scenes"]:
        e = selection.get(sc["id"]) or {}
        v = scene_validation(e, sc, min_score)
        src = e.get("source_used") or e.get("source") or "pendente"
        agg = by_source.setdefault(src, {"scenes": 0, "seconds": 0.0, "validated": 0})
        agg["scenes"] += 1
        agg["seconds"] = round(agg["seconds"] + sc["end"] - sc["start"], 2)
        agg["validated"] += 1 if v["status"] == "validated" else 0
        rows.append({
            "id": sc["id"], "start": sc["start"], "end": sc["end"], "text": sc["text"],
            "meaning": sc.get("meaning") or sc.get("visual_intent"), "subject": sc.get("subject"),
            "entities": [{"id": i, "name": entities[i]["name"], "scientific_name": entities[i].get("scientific_name")}
                         for i in sc.get("entity_ids") or [] if i in entities],
            "visual_role": sc.get("visual_role") or "contextual_illustration",
            "required_identity": sc.get("required_identity") or "generic",
            "unresolved": sc.get("unresolved") or [],
            "claims": [{"id": c, "quote": claims[c].get("quote"), "needs_source": claims[c].get("needs_source")}
                       for c in sc.get("claim_ids") or [] if c in claims],
            "documentary_query": sc.get("documentary_query") or "",
            "source": src, "provider": e.get("provider"), "title": e.get("title"), "page_url": e.get("page_url"),
            "author": e.get("author"), "license": e.get("license"), "obtained_how": e.get("obtained_how"),
            "asset": e.get("asset"), "is_image": e.get("is_image"), "in_point": e.get("in_point"),
            "out_point": e.get("out_point"), "clip_duration": e.get("clip_duration"),
            "score": e.get("score"), "seen": e.get("seen"), "method": e.get("method"),
            "validation": v, "segment_check": e.get("segment_check"), "pinned": bool(e.get("pinned")),
            "alternatives": [{"index": i, "provider": a["candidate"].get("provider"),
                              "title": a["candidate"].get("title", "")[:160], "page_url": a["candidate"].get("page_url"),
                              "thumbnail": (a["candidate"].get("preview_frames") or [a["candidate"].get("thumbnail")])[0],
                              "score": a.get("score"), "method": a.get("method"), "reason": a.get("reason"),
                              "source": a.get("source")}
                             for i, a in enumerate(e.get("alternatives") or [])],
            "references": refs.get(sc["id"], []),
            "flags": _flags(sc, e, v, pending, min_score),
        })
    plan_search = _load(job, "search_plan.json", {}) or {}
    timing = _load(job, "timing_report.json", {}) or {}
    from ..budget import ledger_summary

    return {
        "production": {"id": p.id, "title": p.title, "status": p.status, "language": (p.config or {}).get(
            "video_language") or (p.config or {}).get("language"), "cost_actual": p.cost_actual},
        "scenes": rows,
        "metrics": {
            "by_source": by_source,
            "validation": {st: sum(1 for r in rows if r["validation"]["status"] == st)
                           for st in ("validated", "unvalidated", "review_required", "rejected")},
            "segments": plan_search.get("segments"),
            "youtube_references": sum(len(v) for v in refs.values()),
            "search": {"searches": timing.get("searches"), "vision_calls": timing.get("vision_calls"),
                       "cache": timing.get("search_cache")},
            "cost_by_task": ledger_summary(production_id),
        },
        "languages": [{"code": k, "name": v[1]} for k, v in LANGUAGES.items()],
    }


# ---------------------------------------------------------------- ações
def _requeue(s: Session, p: Production, steps: list[str], label: str) -> None:
    """Volta as etapas afetadas para pendente e a produção para a fila (o runner confere os hashes)."""
    for row in s.exec(select(ProductionStep).where(ProductionStep.production_id == p.id,
                                                   ProductionStep.step.in_(steps))):
        row.status = "pending"
        s.add(row)
    p.status, p.error, p.step_label, p.finished_at, p.updated_at = "queued", None, label, None, now()
    s.add(p)
    s.commit()


def _ctx(p: Production):
    from ..worker.context import JobContext

    ctx = JobContext(p, {"select": 100}, threading.Semaphore(1))
    ctx.begin_step("select", "Revisão")
    return ctx


def _scene(plan: dict, sid: str) -> dict:
    sc = next((x for x in plan["scenes"] if x["id"] == sid), None)
    if not sc:
        raise HTTPException(404, "Cena não encontrada")
    return sc


class ReplaceIn(BaseModel):
    alternative: int | None = None
    authorized_media_id: int | None = None


@router.post("/productions/{production_id}/scenes/{sid}/replace")
def replace_asset(production_id: int, sid: str, body: ReplaceIn, s: Session = Depends(get_session)):
    from ..pipeline.select import NoCandidate, Option, Selector
    from ..providers.stock.base import Candidate, candidate_from_dict

    p = _production(s, production_id, editable=True)
    job = job_dir(production_id)
    plan = _load(job, "plan.json")
    sc = _scene(plan, sid)
    ctx = _ctx(p)
    sel = Selector(ctx)
    old = sel.selection.get(sid) or {}
    alts = list(old.get("alternatives") or [])
    if body.alternative is not None:
        if not 0 <= body.alternative < len(alts):
            raise HTTPException(422, "Alternativa inexistente")
        a = alts[body.alternative]
        opt = Option(candidate_from_dict(a["candidate"]), float(a.get("score") or 0), float(a.get("pos") or 0.5),
                     a.get("realism"), a.get("source") or "stock", a.get("method") or "text", a.get("reason") or "")
    elif body.authorized_media_id is not None:
        m = s.get(AuthorizedMedia, body.authorized_media_id)
        if not m:
            raise HTTPException(404, "Mídia autorizada não encontrada")
        c = sel._local_candidates([m])
        if not c:
            raise HTTPException(409, "Arquivo autorizado indisponível")
        if m.youtube_id:
            c[0].provider = "authorized_youtube"
        opt = Option(c[0], 0.0, 0.5, None, "catalog", "manual", "escolhido na revisão")
    else:
        raise HTTPException(422, "Informe a alternativa ou a mídia autorizada")
    if old.get("external_id") and old.get("provider"):
        sel.segments.release(f"{old['provider']}:{old['external_id']}", float(old.get("in_point") or 0),
                             float(old.get("out_point") or old.get("in_point") or 0),
                             (old.get("channel") or "").lower() if old.get("source") == "youtube" else "")
    try:
        entry = sel._download(sc, opt)
    except NoCandidate as e:
        raise HTTPException(409, f"Não foi possível usar essa opção: {e}") from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Download falhou: {str(e)[:200]}") from e
    entry.update(method=opt.method, reason=opt.seen, realism=opt.realism, step=opt.source, pinned=True,
                 replaced_from=old.get("page_url") or old.get("asset"), edited_at=now().isoformat())
    if old.get("asset"):  # a escolha anterior vira alternativa (dá para desfazer)
        prev = {"candidate": Candidate(provider=old.get("provider") or "", external_id=old.get("external_id") or "",
                                       title=old.get("title") or "", duration=old.get("clip_duration") or 0,
                                       width=0, height=0, page_url=old.get("page_url") or "",
                                       thumbnail=None).to_dict(),
                "score": old.get("score"), "pos": 0.5, "method": old.get("method"), "source": old.get("source"),
                "reason": "escolha anterior"}
        alts = [a for i, a in enumerate(alts) if i != body.alternative] + [prev]
    entry["alternatives"] = alts[:6]
    entry["validation"] = classify(entry, sc, config_min_score(job), policy(load_settings()))
    sel.selection[sid] = entry
    sel.save()
    return {"ok": True, "entry": {k: entry.get(k) for k in ("source_used", "provider", "title", "asset", "validation")}}


class PinIn(BaseModel):
    pinned: bool = True


@router.post("/productions/{production_id}/scenes/{sid}/pin")
def pin_asset(production_id: int, sid: str, body: PinIn, s: Session = Depends(get_session)):
    _production(s, production_id, editable=True)
    job = job_dir(production_id)
    selection = _load(job, "selection.json", {})
    if sid not in selection:
        raise HTTPException(404, "Cena sem asset")
    selection[sid]["pinned"] = body.pinned  # fixada: não é trocada quando o plano da cena muda
    _write(job, "selection.json", selection)
    return {"ok": True}


class IntervalIn(BaseModel):
    in_point: float = Field(ge=0)


@router.post("/productions/{production_id}/scenes/{sid}/interval")
def edit_interval(production_id: int, sid: str, body: IntervalIn, s: Session = Depends(get_session)):
    from ..pipeline import media_index

    _production(s, production_id, editable=True)
    job = job_dir(production_id)
    sc = _scene(_load(job, "plan.json"), sid)
    selection = _load(job, "selection.json", {})
    e = selection.get(sid)
    if not e or e.get("is_image") or not e.get("asset"):
        raise HTTPException(409, "Só cenas com vídeo têm trecho editável")
    dur = sc["end"] - sc["start"]
    total = float(e.get("clip_duration") or 0)
    if total and body.in_point + dur > total - 0.05:
        raise HTTPException(422, f"O trecho passa do fim do vídeo ({total:.1f}s)")
    if e.get("provider") in ("authorized_youtube", "authorized_local"):
        m = s.get(AuthorizedMedia, int(e["external_id"]))
        if not m:
            raise HTTPException(409, "Arquivo autorizado não existe mais")
        meta = media_index.cut_segment(Path(m.local_path), body.in_point, body.in_point + dur + 0.5, job / e["asset"])
        e["in"], e["resolution"] = 0.0, [meta["width"], meta["height"]]
    elif e.get("provider") in ("pexels", "pixabay") or e.get("in", 0) == e.get("in_point"):
        e["in"] = body.in_point  # o arquivo é o clipe inteiro: basta mudar o ponto de entrada
    else:
        raise HTTPException(409, "Este asset é um trecho já recortado; troque pela alternativa ou associe um arquivo")
    e["in_point"], e["out_point"] = round(body.in_point, 2), round(body.in_point + dur, 2)
    e["segment_check"] = {"status": "edited", "interval": [e["in_point"], e["out_point"]],
                          "reason": "trecho escolhido na revisão (não conferido pela visão)"}
    e["pinned"], e["edited_at"] = True, now().isoformat()
    e["validation"] = classify(e, sc, config_min_score(job), policy(load_settings()))
    _write(job, "selection.json", selection)
    return {"ok": True, "validation": e["validation"]}


class SearchMoreIn(BaseModel):
    queries: list[str] = Field(default_factory=list, max_length=5)


@router.post("/productions/{production_id}/scenes/{sid}/search-more")
def search_more(production_id: int, sid: str, body: SearchMoreIn, s: Session = Depends(get_session)):
    """Nova rodada de busca e avaliação só desta cena, dentro do teto de gasto; não troca nada sozinho."""
    from ..pipeline.select import Selector

    p = _production(s, production_id, editable=True)
    job = job_dir(production_id)
    sc = _scene(_load(job, "plan.json"), sid)
    ctx = _ctx(p)
    sel = Selector(ctx)
    sel.plan_searches([sc])
    queries = [q.strip() for q in body.queries if q.strip()] or list(sc.get("queries") or [])
    d = sel.decide(sc, rewrite="none", queries=queries)
    current = sel.selection.get(sid) or {}
    known = {(a["candidate"].get("provider"), a["candidate"].get("external_id"))
             for a in current.get("alternatives") or []}
    known.add((current.get("provider"), current.get("external_id")))
    new = [{"candidate": o.candidate.to_dict(), "score": o.score, "pos": o.pos, "method": o.method,
            "source": o.source, "reason": o.seen, "realism": o.realism}
           for o in d.options if (o.candidate.provider, o.candidate.external_id) not in known]
    if current:
        current["alternatives"] = (list(current.get("alternatives") or []) + new)[:10]
        sel.selection[sid] = current
        sel.save()
    return {"ok": True, "found": len(new), "searches": d.stats.get("searches", 0),
            "vision_calls": d.stats.get("vision_calls", 0), "budget_blocked": bool(d.stats.get("vision_budget"))}


class InterpretationIn(BaseModel):
    subject: str | None = None
    entity_ids: list[str] | None = None
    documentary_query: str | None = None
    visual_role: Literal["exact_evidence", "contextual_illustration", "explanation", "metaphor", "reconstruction",
                         "comparison", "data_explanation"] | None = None
    required_identity: Literal["none", "generic", "category", "species", "exact_person", "exact_event",
                               "exact_place", "exact_object"] | None = None


@router.patch("/productions/{production_id}/scenes/{sid}/interpretation")
def fix_interpretation(production_id: int, sid: str, body: InterpretationIn, s: Session = Depends(get_session)):
    """Correção manual: a cena muda de impressão digital, então só ela é selecionada de novo."""
    p = _production(s, production_id, editable=True)
    job = job_dir(production_id)
    plan = _load(job, "plan.json")
    sc = _scene(plan, sid)
    changes = body.model_dump(exclude_none=True)
    sc.update(changes)
    sc["unresolved"] = [u for u in sc.get("unresolved") or [] if "revis" not in u]
    check_scene(sc, sc["text"], _load(job, "context_bible.json", {}))
    sc["interpretation_confidence"] = "explicit"
    sc["edited_at"] = now().isoformat()
    _write(job, "plan.json", plan)
    _requeue(s, p, ["select", "generate", "direct", "render", "upload"], "Na fila (revisão de interpretação)")
    return {"ok": True, "scene": {k: sc.get(k) for k in ("subject", "entity_ids", "visual_role", "required_identity",
                                                        "unresolved")}}


class EntityIn(BaseModel):
    name: str | None = None
    aliases: list[str] | None = None
    scientific_name: str | None = None


@router.patch("/productions/{production_id}/entities/{eid}")
def fix_entity(production_id: int, eid: str, body: EntityIn, s: Session = Depends(get_session)):
    p = _production(s, production_id, editable=True)
    job = job_dir(production_id)
    bible = _load(job, "context_bible.json", {})
    ent = next((e for e in bible.get("entities") or [] if e["id"] == eid), None)
    if not ent:
        raise HTTPException(404, "Entidade não encontrada")
    ent.update(body.model_dump(exclude_none=True))
    if body.scientific_name:
        ent["ambiguous_name"] = False
    _write(job, "context_bible.json", bible)
    plan = _load(job, "plan.json")
    touched = 0
    for sc in plan["scenes"]:
        if eid in (sc.get("entity_ids") or []):
            sc["entity_rev"] = int(sc.get("entity_rev") or 0) + 1  # muda a impressão digital da cena
            sc["unresolved"] = [u for u in sc.get("unresolved") or [] if ent["name"] not in u]
            touched += 1
    _write(job, "plan.json", plan)
    _requeue(s, p, ["select", "generate", "direct", "render", "upload"], "Na fila (entidade corrigida)")
    return {"ok": True, "scenes": touched}


class LanguageIn(BaseModel):
    video_language: str


@router.post("/productions/{production_id}/language")
def fix_language(production_id: int, body: LanguageIn, s: Session = Depends(get_session)):
    """Idioma corrigido: narração (TTS), legendas e textos seguem; os hashes decidem o que refazer."""
    p = _production(s, production_id, editable=True)
    if body.video_language not in LANGUAGES:
        raise HTTPException(422, "Idioma não suportado")
    p.config = {**(p.config or {}), "video_language": body.video_language, "language": body.video_language}
    s.add(p)
    s.commit()
    _requeue(s, p, [], "Na fila (idioma corrigido)")
    return {"ok": True}


@router.post("/productions/{production_id}/rerender")
def rerender(production_id: int, s: Session = Depends(get_session)):
    """Monta de novo com as trocas: direção e render (só as cenas alteradas são refeitas) e o envio."""
    p = _production(s, production_id, editable=True)
    _requeue(s, p, ["direct", "render", "upload"], "Na fila (render parcial)")
    return {"ok": True}


@router.get("/productions/{production_id}/assets/{path:path}")
def production_asset(production_id: int, path: str, s: Session = Depends(get_session)):
    """Arquivo da produção para o preview (só dentro da pasta da produção)."""
    _production(s, production_id)
    base = job_dir(production_id).resolve()
    target = (base / path).resolve()
    if base not in target.parents or not target.is_file():
        raise HTTPException(404, "Arquivo não encontrado")
    return FileResponse(target)


@router.get("/productions/{production_id}/manifest")
def production_manifest(production_id: int, s: Session = Depends(get_session)):
    _production(s, production_id)
    m = build_manifest(job_dir(production_id))
    if m is None:
        raise HTTPException(404, "A produção ainda não tem plano")
    return m


@router.get("/productions/{production_id}/credits", response_class=PlainTextResponse)
def production_credits(production_id: int, s: Session = Depends(get_session)):
    _production(s, production_id)
    return credits_text(build_manifest(job_dir(production_id))) or ""

