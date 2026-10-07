"""Orquestração das etapas de uma produção (§6)."""
from __future__ import annotations

import logging
import threading
import traceback
from typing import Callable

from sqlmodel import select

from .. import artifacts
from ..db import session_scope
from ..models import Production, ProductionConfig, ProductionStep, now
from ..pipeline import audio, direct, generate, plan, render, select as select_step, transcribe, upload
from ..providers.llm.base import BatchPending, set_current_production
from .context import Cancelled, JobContext, StepError

log = logging.getLogger("aieditor.runner")

# (id, peso %, rótulo, função)
STEPS: list[tuple[str, float, str, Callable[[JobContext], str | None]]] = [
    ("audio", 5, "Áudio", audio.run),
    ("transcribe", 5, "Transcrição", transcribe.run),
    ("plan", 10, "Planejamento de cenas", plan.run),
    ("select", 40, "Selecionando cenas", select_step.run),
    ("generate", 15, "Gerando mídia de IA", generate.run),
    ("direct", 5, "Direção", direct.run),
    ("render", 15, "Renderizando", render.run),
    ("upload", 5, "Enviando ao Drive", upload.run),
]


def applicable_steps(config: ProductionConfig) -> list[str]:
    steps = [s[0] for s in STEPS]
    if config.real_pct >= 100:
        steps.remove("generate")
    if config.real_pct <= 0:
        steps.remove("select")
    return steps


def step_weights(config: ProductionConfig) -> dict[str, float]:
    """Pesos normalizados para 100, redistribuindo etapas que não se aplicam."""
    active = applicable_steps(config)
    raw = {sid: w for sid, w, _, _ in STEPS if sid in active}
    total = sum(raw.values())
    return {k: v * 100 / total for k, v in raw.items()}


def _set_step_row(production_id: int, step: str, **fields) -> None:
    with session_scope() as s:
        row = s.exec(select(ProductionStep).where(ProductionStep.production_id == production_id,
                                                  ProductionStep.step == step)).first()
        if not row:
            row = ProductionStep(production_id=production_id, step=step)
        for k, v in fields.items():
            setattr(row, k, v)
        s.add(row)
        s.commit()


def _finish(production_id: int, **fields) -> None:
    with session_scope() as s:
        p = s.get(Production, production_id)
        if not p:
            return
        for k, v in fields.items():
            setattr(p, k, v)
        p.updated_at = now()
        p.finished_at = now() if fields.get("status") in ("done", "failed", "cancelled") else p.finished_at
        s.add(p)
        s.commit()


def run_production(production_id: int, render_lock: threading.Semaphore) -> None:
    with session_scope() as s:
        production = s.get(Production, production_id)
        if not production:
            return
        production.started_at = production.started_at or now()
        production.error = None
        s.add(production)
        s.commit()
        s.refresh(production)
        done_steps = {r.step for r in s.exec(select(ProductionStep).where(
            ProductionStep.production_id == production_id, ProductionStep.status.in_(["done", "skipped"])))}

    from ..budget import release_stale

    stale = release_stale(production_id)  # reservas abertas de uma execução que morreu não seguram o teto
    if stale:
        log.info("produção %s: %d reserva(s) de custo órfã(s) liberada(s)", production_id, stale)
    set_current_production(production_id)
    config = ProductionConfig.model_validate(production.config)
    weights = step_weights(config)
    ctx = JobContext(production, weights, render_lock)
    active = applicable_steps(config)

    try:
        for sid, _, label, fn in STEPS:
            if sid not in active:
                _set_step_row(production_id, sid, status="skipped")
                continue
            if sid in done_steps:
                ok, why = artifacts.still_valid(sid, ctx.dir, ctx.script, production.config, ctx.settings)
                if ok:
                    continue
                # concluída, mas as entradas mudaram ou a saída sumiu: refaz esta (as seguintes se invalidam
                # sozinhas pelos hashes; etapas independentes da mudança continuam aproveitadas)
                log.info("produção %s: etapa %s refeita (%s)", production_id, sid, why)
                ctx.issue("STEP_INVALIDATED", f"Etapa '{label}' refeita: {why}", severity="info")
            ctx.check_cancel()
            ctx.begin_step(sid, label)
            _set_step_row(production_id, sid, status="running", started_at=now(), finished_at=None)
            log.info("produção %s: etapa %s", production_id, sid)
            output = fn(ctx)
            artifacts.record(sid, ctx.dir, ctx.script, production.config, ctx.settings)
            _set_step_row(production_id, sid, status="done", finished_at=now(), output=output)
        _finish(production_id, status="done", progress=100.0, step="done", step_label="Concluído")
    except BatchPending as e:
        # lote da Batch API ainda processando: a etapa volta a pendente e a produção espera fora do worker
        _set_step_row(production_id, ctx._step or "?", status="pending")
        with session_scope() as s:
            p = s.get(Production, production_id)
            if p and p.status == "running":
                p.status, p.step_label = "waiting_provider", "Aguardando lote do provedor (até 24 h)"
                p.resume_at = e.retry_at or now()
                p.updated_at = now()
                s.add(p)
                s.commit()
        log.info("produção %s aguardando lote %s até %s", production_id, e.batch_id, e.retry_at)
    except Cancelled:
        _finish(production_id, status="cancelled", step_label="Cancelado")
    except StepError as e:
        _mark_failed(ctx, production_id, e.code, e.message, e.detail)
    except Exception as e:  # noqa: BLE001
        _mark_failed(ctx, production_id, "STEP_FAILED", f"{type(e).__name__}: {e}", traceback.format_exc())


def _mark_failed(ctx: JobContext, production_id: int, code: str, message: str, detail: str | None) -> None:
    log.error("produção %s falhou: %s %s", production_id, code, message)
    step = ctx._step or "?"
    _set_step_row(production_id, step, status="failed", finished_at=now())
    ctx.issue(code, f"[{step}] {message}", detail=detail, severity="error")
    _finish(production_id, status="failed", error=message)
