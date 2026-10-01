"""Contexto compartilhado pelas etapas de uma produção."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from sqlmodel import select

from ..config import job_dir, load_settings
from ..db import session_scope
from ..issues import severity_of
from ..models import Issue, Production, ProductionConfig, now


class Cancelled(Exception):
    pass


class StepError(Exception):
    """Falha com código do catálogo (§13)."""

    def __init__(self, code: str, message: str, detail: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


class JobContext:
    def __init__(self, production: Production, step_weights: dict[str, float], render_lock: threading.Semaphore):
        self.production_id: int = production.id  # type: ignore[assignment]
        self.channel_id = production.channel_id
        self.script = production.script
        self.config = ProductionConfig.model_validate(production.config)
        self.dir: Path = job_dir(self.production_id)
        self.settings = load_settings()
        self.render_lock = render_lock
        self._weights = step_weights
        self._step: str | None = None
        self._step_offset = 0.0
        self._last_write = 0.0
        self._lock = threading.Lock()

    # ---- arquivos -------------------------------------------------------
    def path(self, *parts: str) -> Path:
        p = self.dir.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def read_json(self, name: str) -> Any:
        return json.loads(self.path(name).read_text(encoding="utf-8"))

    def write_json(self, name: str, data: Any) -> Path:
        p = self.path(name)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
        return p

    # ---- progresso ------------------------------------------------------
    def begin_step(self, step: str, label: str) -> None:
        order = list(self._weights)
        self._step = step
        self._step_offset = sum(self._weights[s] for s in order[: order.index(step)])
        self._write(progress=self._step_offset, step=step, step_label=label, force=True)

    def progress(self, fraction: float, label: str | None = None) -> None:
        """fraction = 0..1 dentro da etapa atual."""
        assert self._step
        fraction = max(0.0, min(1.0, fraction))
        overall = self._step_offset + self._weights[self._step] * fraction
        self._write(progress=overall, step_label=label)

    def _write(self, force: bool = False, **fields: Any) -> None:
        with self._lock:
            t = time.monotonic()
            if not force and t - self._last_write < 0.5:
                return
            self._last_write = t
            with session_scope() as s:
                p = s.get(Production, self.production_id)
                if not p:
                    return
                for k, v in fields.items():
                    if v is not None:
                        setattr(p, k, round(v, 2) if k == "progress" else v)
                p.updated_at = now()
                s.add(p)
                s.commit()

    # ---- problemas, custo, cancelamento ---------------------------------
    def issue(self, code: str, message: str, scene: str | None = None, detail: str | None = None,
              severity: str | None = None) -> None:
        with session_scope() as s:
            s.add(Issue(production_id=self.production_id, code=code, severity=severity or severity_of(code),
                        scene=scene, message=message, detail=detail))
            p = s.get(Production, self.production_id)
            if p:
                p.updated_at = now()
                s.add(p)
            s.commit()

    def add_cost(self, amount: float) -> None:
        if amount <= 0:
            return
        with self._lock, session_scope() as s:
            p = s.get(Production, self.production_id)
            if p:
                p.cost_actual = round((p.cost_actual or 0) + amount, 4)
                s.add(p)
                s.commit()

    def check_cancel(self) -> None:
        with session_scope() as s:
            status = s.exec(select(Production.status).where(Production.id == self.production_id)).first()
        if status in ("cancel_requested", "cancelled"):
            raise Cancelled()
