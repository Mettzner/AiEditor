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
from ..fsutil import atomic_write_text
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
        return atomic_write_text(self.path(name), json.dumps(data, indent=2, ensure_ascii=False))

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

    def record_llm(self, usage, step: str | None = None) -> None:
        """Registra uma chamada de IA paga (Claude, Gemini) em llm_usage.json e soma o custo na produção."""
        entry = usage.to_dict() if hasattr(usage, "to_dict") else dict(usage)
        entry["step"] = step or self._step
        with self._lock:
            path = self.path("llm_usage.json")
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"calls": []}
            data["calls"].append(entry)
            calls = data["calls"]
            by_task: dict[str, dict] = {}
            for c in calls:
                t = by_task.setdefault(c.get("task") or c.get("step") or "?", {"calls": 0, "cost": 0.0, "tokens": 0})
                t["calls"] += 1
                t["cost"] = round(t["cost"] + (c.get("cost") or 0), 6)
                t["tokens"] += sum(c.get(k) or 0 for k in ("input_tokens", "output_tokens",
                                                           "cache_creation_input_tokens", "cache_read_input_tokens"))
            data["summary"] = {
                "calls": len(calls), "cost": round(sum(c.get("cost") or 0 for c in calls), 6),
                "input_tokens": sum(c.get("input_tokens") or 0 for c in calls),
                "output_tokens": sum(c.get("output_tokens") or 0 for c in calls),
                "cache_read_input_tokens": sum(c.get("cache_read_input_tokens") or 0 for c in calls),
                "cache_creation_input_tokens": sum(c.get("cache_creation_input_tokens") or 0 for c in calls),
                "local_cache_hits": sum(1 for c in calls if c.get("local_cache")),
                "by_task": by_task,
                "top_task": max(by_task, key=lambda k: by_task[k]["cost"]) if by_task else None,
            }
            atomic_write_text(path, json.dumps(data, indent=1, ensure_ascii=False))
        self.add_cost(entry.get("cost") or 0)

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
