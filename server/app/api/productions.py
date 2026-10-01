from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, delete, select

from ..config import job_dir
from ..db import get_session, session_scope
from ..estimate import estimate
from ..models import Channel, Issue, Preset, Production, ProductionConfig, ProductionStep, now

router = APIRouter(tags=["productions"])

AUDIO_EXT = {"mp3", "wav", "m4a"}


def _card(p: Production, channel_name: str | None, issues: list[Issue]) -> dict[str, Any]:
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
        "issues": [{"id": i.id, "code": i.code, "severity": i.severity, "scene": i.scene, "message": i.message,
                    "detail": i.detail, "created_at": i.created_at} for i in issues],
    }


def _cards(s: Session, productions: list[Production]) -> list[dict]:
    if not productions:
        return []
    ids = [p.id for p in productions]
    channels = {c.id: c.name for c in s.exec(select(Channel))}
    issues: dict[int, list[Issue]] = {}
    for i in s.exec(select(Issue).where(Issue.production_id.in_(ids)).order_by(Issue.id)):
        issues.setdefault(i.production_id, []).append(i)
    return [_card(p, channels.get(p.channel_id), issues.get(p.id, [])) for p in productions]  # type: ignore[arg-type]


@router.get("/productions")
def list_productions(s: Session = Depends(get_session)):
    return _cards(s, list(s.exec(select(Production).order_by(Production.created_at.desc()))))


@router.get("/productions/events")
async def production_events(request: Request):
    """SSE: snapshot inicial e depois cada produção alterada (o worker grava no SQLite)."""

    async def stream():
        last = datetime.min.replace(tzinfo=timezone.utc)
        first = True
        while not await request.is_disconnected():
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


def _build_config(s: Session, channel_id: int, title: str, overrides: dict, audio_mode: str) -> ProductionConfig:
    ch = s.get(Channel, channel_id)
    if not ch:
        raise HTTPException(404, "Canal não encontrado")
    base = Preset.model_validate(ch.preset).model_dump()
    music = overrides.pop("music_enabled", None)
    base.update({k: v for k, v in overrides.items() if k in Preset.model_fields})
    if music is not None:
        base["music"]["enabled"] = bool(music)
    return ProductionConfig(**base, title=title, channel_name=ch.name, audio_mode=audio_mode)  # type: ignore[arg-type]


@router.post("/productions")
async def create_production(
    channel_id: int = Form(...),
    title: str = Form(...),
    script: str = Form(...),
    config: str = Form("{}"),
    audio: UploadFile | None = File(None),
    s: Session = Depends(get_session),
):
    if not script.strip():
        raise HTTPException(400, "Roteiro vazio")
    audio_mode = "upload" if audio and audio.filename else "tts"
    if audio_mode == "upload":
        ext = audio.filename.rsplit(".", 1)[-1].lower()  # type: ignore[union-attr]
        if ext not in AUDIO_EXT:
            raise HTTPException(400, "Envie um arquivo mp3, wav ou m4a")
    cfg = _build_config(s, channel_id, title, json.loads(config or "{}"), audio_mode)
    if audio_mode == "tts" and not cfg.tts_voice:
        raise HTTPException(400, "Sem áudio enviado e o canal não tem voz TTS definida")
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


class EstimateIn(BaseModel):
    channel_id: int
    title: str = ""
    script: str = ""
    config: dict = {}
    audio_seconds: float | None = None
    audio_mode: str = "tts"


@router.post("/estimate")
def estimate_route(body: EstimateIn, s: Session = Depends(get_session)):
    cfg = _build_config(s, body.channel_id, body.title or "-", dict(body.config), body.audio_mode)
    return estimate(cfg, body.script, body.audio_seconds)
