from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, delete, select

from ..config import job_dir, load_settings
from ..db import get_session, session_scope
from ..estimate import estimate
from ..lang import detect as detect_language
from ..pipeline.report import build_report
from ..models import CREATION_FIELDS, Channel, Issue, Preset, Production, ProductionConfig, ProductionStep, now

router = APIRouter(tags=["productions"])

AUDIO_EXT = {"mp3", "wav", "m4a"}
SSE_LIFETIME = 25


def _card(p: Production, channel_name: str | None, issues: list[Issue],
          steps: list[ProductionStep] | None = None) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for i in issues:
        counts[i.severity] = counts.get(i.severity, 0) + 1
    cfg = p.config or {}
    return {
        "id": p.id, "title": p.title, "channel_id": p.channel_id, "channel_name": channel_name,
        "status": p.status, "step": p.step, "step_label": p.step_label, "progress": p.progress,
        "drive_url": p.drive_url, "output_path": p.output_path, "duration_seconds": p.duration_seconds,
        "cost_estimated": p.cost_estimated, "cost_actual": p.cost_actual, "error": p.error,
        "created_at": p.created_at, "started_at": p.started_at, "finished_at": p.finished_at,
        "updated_at": p.updated_at, "ai_content": cfg.get("real_pct", 100) < 100,
        "issue_counts": counts,
        "llm_usage": _llm_summary(p.id),
        "step_seconds": {s.step: round((_aware(s.finished_at) - _aware(s.started_at)).total_seconds(), 1)
                         for s in steps or [] if s.started_at and s.finished_at and s.status == "done"},
        "issues": [{"id": i.id, "code": i.code, "severity": i.severity, "scene": i.scene, "message": i.message,
                    "detail": i.detail, "created_at": i.created_at} for i in issues],
    }


def _llm_summary(production_id: int | None) -> dict | None:
    path = job_dir(production_id) / "llm_usage.json" if production_id else None
    if not path or not path.exists():
        return None
    try:
        summary = json.loads(path.read_text(encoding="utf-8")).get("summary")
    except (OSError, ValueError):
        return None
    if not summary:
        return None
    tokens = sum(summary.get(k, 0) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                                             "cache_creation_input_tokens"))
    return {"cost": summary.get("cost"), "tokens": tokens, "top_task": summary.get("top_task"),
            "cache_read": summary.get("cache_read_input_tokens", 0), "calls": summary.get("calls", 0)}


def _cards(s: Session, productions: list[Production]) -> list[dict]:
    if not productions:
        return []
    ids = [p.id for p in productions]
    channels = {c.id: c.name for c in s.exec(select(Channel))}
    issues: dict[int, list[Issue]] = {}
    for i in s.exec(select(Issue).where(Issue.production_id.in_(ids)).order_by(Issue.id)):
        issues.setdefault(i.production_id, []).append(i)
    steps: dict[int, list[ProductionStep]] = {}
    for st in s.exec(select(ProductionStep).where(ProductionStep.production_id.in_(ids))):
        steps.setdefault(st.production_id, []).append(st)
    return [_card(p, channels.get(p.channel_id), issues.get(p.id, []), steps.get(p.id))  # type: ignore[arg-type]
            for p in productions]


@router.get("/productions")
def list_productions(s: Session = Depends(get_session)):
    return _cards(s, list(s.exec(select(Production).order_by(Production.created_at.desc()))))


@router.get("/productions/events")
async def production_events(request: Request):
    """SSE: snapshot inicial e depois cada produção alterada (o worker grava no SQLite).

    Cada conexão dura no máximo SSE_LIFETIME segundos e o navegador reconecta sozinho. Uma conexão
    infinita impede o uvicorn de reiniciar (--reload) e de desligar.
    """

    async def stream():
        last = datetime.min.replace(tzinfo=timezone.utc)
        first = True
        deadline = asyncio.get_running_loop().time() + SSE_LIFETIME
        yield "retry: 1500\n\n"
        while not await request.is_disconnected() and asyncio.get_running_loop().time() < deadline:
            with session_scope() as s:
                q = select(Production).order_by(Production.created_at.desc())
                rows = list(s.exec(q))
                changed = [p for p in rows if first or _aware(p.updated_at) > last]
                if changed:
                    last = max(_aware(p.updated_at) for p in rows)
                    event = "snapshot" if first else "update"
                    payload = json.dumps(_cards(s, changed), default=str, ensure_ascii=False)
                    yield f"event: {event}\ndata: {payload}\n\n"
                elif not first:
                    yield ": ping\n\n"
            first = False
            await asyncio.sleep(1.0)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@router.get("/productions/{production_id}")
def get_production(production_id: int, s: Session = Depends(get_session)):
    p = s.get(Production, production_id)
    if not p:
        raise HTTPException(404, "Produção não encontrada")
    card = _cards(s, [p])[0]
    card["config"] = p.config
    card["script"] = p.script
    card["steps"] = [r.model_dump() for r in s.exec(select(ProductionStep)
                                                     .where(ProductionStep.production_id == production_id))]
    return card


# Decididos pelo sistema, nunca pela tela: idioma (detectado no roteiro), buscas (inglês), estilo visual e
# representação de época (interpretados do roteiro pela Bíblia de Contexto).
AUTOMATIC_FIELDS = {"language", "search_language", "visual_style", "period_look"}


def _build_config(s: Session, channel_id: int, title: str, overrides: dict, audio_mode: str,
                  script: str = "") -> ProductionConfig:
    ch = s.get(Channel, channel_id)
    if not ch:
        raise HTTPException(404, "Canal não encontrado")
    base = Preset.model_validate(ch.preset).model_dump()
    music = overrides.pop("music_enabled", None)
    base.update({k: v for k, v in overrides.items() if k in Preset.model_fields and k not in AUTOMATIC_FIELDS})
    if music is not None:
        base["music"]["enabled"] = bool(music)
    lang = detect_language(script) or "en"
    base.update(language=lang, search_language="en", visual_style="", period_look="cinematic")
    return ProductionConfig(**base, title=title, channel_name=ch.name, audio_mode=audio_mode,  # type: ignore[arg-type]
                            video_language=lang)


@router.post("/productions")
async def create_production(
    channel_id: int = Form(...),
    title: str = Form(...),
    script: str = Form(...),
    config: str = Form("{}"),
    save_as_default: bool = Form(False),
    audio: UploadFile | None = File(None),
    s: Session = Depends(get_session),
):
    if not script.strip():
        raise HTTPException(400, "Roteiro vazio")
    from ..whisper_models import model_ready

    model = load_settings()["transcription"]["model"]
    if not model_ready(model):  # app instalado: o modelo é baixado no assistente inicial / Configuração
        raise HTTPException(409, f"Baixe o modelo de transcrição '{model}' antes de criar produções "
                                 "(Configuração → Transcrição)")
    audio_mode = "upload" if audio and audio.filename else "tts"
    if audio_mode == "upload":
        ext = audio.filename.rsplit(".", 1)[-1].lower()  # type: ignore[union-attr]
        if ext not in AUDIO_EXT:
            raise HTTPException(400, "Envie um arquivo mp3, wav ou m4a")
    cfg = _build_config(s, channel_id, title, json.loads(config or "{}"), audio_mode, script)
    if audio_mode == "tts" and not cfg.tts_voice:
        raise HTTPException(400, "Sem áudio enviado e nenhum narrador selecionado")
    if save_as_default:
        ch = s.get(Channel, channel_id)
        assert ch
        chosen = cfg.model_dump()
        ch.preset = {**ch.preset, **{k: chosen[k] for k in CREATION_FIELDS},
                     "music": {**ch.preset.get("music", {}), "enabled": cfg.music.enabled}}
        s.add(ch)
    p = Production(channel_id=channel_id, title=title, script=script, config=cfg.model_dump(), status="queued",
                   step_label="Na fila", cost_estimated=estimate(cfg, script)["cost"]["total"])
    s.add(p)
    s.commit()
    s.refresh(p)
    if audio_mode == "upload":
        dest = job_dir(p.id) / "input" / f"narration.{ext}"  # type: ignore[arg-type]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(await audio.read())  # type: ignore[union-attr]
    return _cards(s, [p])[0]


@router.post("/productions/{production_id}/cancel")
def cancel_production(production_id: int, s: Session = Depends(get_session)):
    p = s.get(Production, production_id)
    if not p:
        raise HTTPException(404, "Produção não encontrada")
    if p.status == "queued":
        p.status = "cancelled"
        p.step_label = "Cancelado"
    elif p.status == "running":
        p.status = "cancel_requested"
        p.step_label = "Cancelando…"
    else:
        raise HTTPException(409, f"Produção está '{p.status}' e não pode ser cancelada")
    p.updated_at = now()
    s.add(p)
    s.commit()
    return {"ok": True}


@router.post("/productions/{production_id}/retry")
def retry_production(production_id: int, s: Session = Depends(get_session)):
    """Volta para a fila; o runner pula as etapas já concluídas e retoma da que falhou."""
    p = s.get(Production, production_id)
    if not p:
        raise HTTPException(404, "Produção não encontrada")
    if p.status not in ("failed", "cancelled"):
        raise HTTPException(409, "Só é possível tentar de novo produções que falharam ou foram canceladas")
    for row in s.exec(select(ProductionStep).where(ProductionStep.production_id == production_id,
                                                   ProductionStep.status.in_(["failed", "running"]))):
        row.status = "pending"
        s.add(row)
    p.status = "queued"
    p.error = None
    p.step_label = "Na fila (retomando)"
    p.finished_at = None
    p.updated_at = now()
    s.add(p)
    s.commit()
    return {"ok": True}


@router.delete("/productions/{production_id}")
def delete_production(production_id: int, s: Session = Depends(get_session)):
    p = s.get(Production, production_id)
    if not p:
        raise HTTPException(404, "Produção não encontrada")
    if p.status in ("running", "cancel_requested"):
        raise HTTPException(409, "Cancele a produção antes de excluir")
    s.exec(delete(Issue).where(Issue.production_id == production_id))
    s.exec(delete(ProductionStep).where(ProductionStep.production_id == production_id))
    s.delete(p)
    s.commit()
    return {"ok": True}


@router.get("/productions/{production_id}/direction")
def production_direction(production_id: int, s: Session = Depends(get_session)):
    """Relatório de direção montado na hora a partir dos arquivos da produção."""
    p = s.get(Production, production_id)
    if not p:
        raise HTTPException(404, "Produção não encontrada")
    issues = [i.model_dump() for i in s.exec(select(Issue).where(Issue.production_id == production_id))]
    cfg = ProductionConfig.model_validate(p.config)
    report = build_report(job_dir(production_id), {"id": p.id, "title": p.title, "channel_name": cfg.channel_name,
                                                  "created_at": str(p.created_at), "drive_url": p.drive_url},
                          cfg, issues)
    if report is None:
        raise HTTPException(404, "A produção ainda não chegou ao planejamento de cenas")
    return report


class EstimateIn(BaseModel):
    channel_id: int
    title: str = ""
    script: str = ""
    config: dict = {}
    audio_seconds: float | None = None
    audio_mode: str = "tts"


@router.post("/estimate")
def estimate_route(body: EstimateIn, s: Session = Depends(get_session)):
    cfg = _build_config(s, body.channel_id, body.title or "-", dict(body.config), body.audio_mode, body.script)
    return estimate(cfg, body.script, body.audio_seconds)
