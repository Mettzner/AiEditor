"""Teto de gasto com IA por produção (settings["budget"]): reserva antes da chamada, reconciliação depois.

Toda chamada paga (Bíblia, planejamento, reescrita, overlays, visão paga) passa pelo Ledger da produção:
  1. reserve(tarefa, estimativa): só libera se custo já registrado + reservas abertas + estimativa (+ folga)
     couber no teto; senão lança BudgetExceeded e a chamada não acontece;
  2. a chamada roda;
  3. settle(custo real): fecha a reserva (o custo real entra em Production.cost_actual pelo record_llm).
As reservas ficam no SQLite (cost_entry) para auditoria e retomada: ao reiniciar, reservas abertas de um processo
que morreu são liberadas (release_stale) — o que foi de fato cobrado já está no custo registrado.

Teto de gasto (US$) é diferente de cota de API (YouTube, Gemini grátis) e de limite de chamadas: cada um tem seu
controle. A produção nunca para por causa do teto: etapas obrigatórias caem no caminho determinístico e a
seleção segue pelo ranking de texto.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field

from sqlmodel import select

from .config import load_settings
from .db import session_scope
from .models import CostEntry, Production, now

# direção, correção de idioma dos overlays e validação de imagens de IA ainda vêm depois da seleção
DEFAULT_RESERVE = 0.15
DEFAULT_CALL_COST = 0.01


class BudgetExceeded(Exception):
    """O teto de gasto da produção não comporta esta chamada paga."""

    def __init__(self, message: str, task: str = "", estimate: float = 0.0, limit: float = 0.0):
        super().__init__(message)
        self.task = task
        self.estimate = estimate
        self.limit = limit


def limit_usd() -> float:
    return float((load_settings().get("budget") or {}).get("max_usd_per_production", 2.0) or 0)


def production_cost(production_id: int) -> float:
    with session_scope() as s:
        cost = s.exec(select(Production.cost_actual).where(Production.id == production_id)).first()
    return float(cost or 0)


@dataclass
class Reservation:
    ledger: "Ledger | None"
    entry_id: str
    task: str
    estimate: float
    closed: bool = False

    def settle(self, actual: float | None, price_known: bool = True, model: str = "") -> None:
        """Fecha a reserva com o custo real (None = a chamada não aconteceu: libera)."""
        if self.closed:
            return
        self.closed = True
        if self.ledger is not None:
            self.ledger._close(self, actual, price_known, model)

    def release(self) -> None:
        self.settle(None)


@dataclass
class Ledger:
    production_id: int
    limit: float
    lock: threading.Lock = field(default_factory=threading.Lock)
    open: dict[str, Reservation] = field(default_factory=dict)

    @classmethod
    def for_production(cls, production_id: int, limit: float | None = None) -> "Ledger":
        """Um Ledger por produção no processo (as cenas em paralelo compartilham as reservas)."""
        with _registry_lock:
            led = _registry.get(production_id)
            if led is None:
                led = _registry[production_id] = cls(production_id, limit_usd() if limit is None else limit)
            elif limit is not None:
                led.limit = limit
            return led

    def committed(self) -> float:
        return production_cost(self.production_id) + sum(r.estimate for r in self.open.values())

    def reserve(self, task: str, estimate: float, headroom: float = 0.0, provider: str = "",
                model: str = "") -> Reservation:
        estimate = max(0.0, float(estimate or 0))
        rid = uuid.uuid4().hex
        if self.limit <= 0:  # sem teto: registra para auditoria, nunca bloqueia
            res = Reservation(self, rid, task, estimate)
            with self.lock:
                self.open[rid] = res
            self._persist(res, "reserved", provider, model)
            return res
        with self.lock:
            projected = self.committed() + estimate + headroom
            if projected > self.limit:
                raise BudgetExceeded(f"Teto de US$ {self.limit:.2f} por produção: '{task}' (≈ US$ {estimate:.4f}) "
                                     f"não cabe (comprometido US$ {projected - estimate - headroom:.4f})",
                                     task, estimate, self.limit)
            res = Reservation(self, rid, task, estimate)
            self.open[rid] = res
        self._persist(res, "reserved", provider, model)
        return res

    def _persist(self, res: Reservation, status: str, provider: str, model: str) -> None:
        with session_scope() as s:
            s.add(CostEntry(id=res.entry_id, production_id=self.production_id, task=res.task, provider=provider,
                            model=model, status=status, estimated_usd=round(res.estimate, 6)))
            s.commit()

    def _close(self, res: Reservation, actual: float | None, price_known: bool, model: str) -> None:
        with self.lock:
            self.open.pop(res.entry_id, None)
        with session_scope() as s:
            row = s.get(CostEntry, res.entry_id)
            if row:
                row.status = "released" if actual is None else "settled"
                row.actual_usd = None if actual is None else round(float(actual), 6)
                row.price_known = price_known
                row.model = model or row.model
                row.settled_at = now()
                s.add(row)
                s.commit()


_registry: dict[int, Ledger] = {}
_registry_lock = threading.Lock()


def release_stale(production_id: int) -> int:
    """Retomada: reservas abertas de uma execução anterior (processo morto) não seguram o teto."""
    with _registry_lock:
        _registry.pop(production_id, None)
    n = 0
    with session_scope() as s:
        for row in s.exec(select(CostEntry).where(CostEntry.production_id == production_id,
                                                  CostEntry.status == "reserved")):
            row.status = "released"
            row.settled_at = now()
            s.add(row)
            n += 1
        s.commit()
    return n


def ledger_summary(production_id: int) -> dict:
    """Por tarefa: chamadas fechadas, estimado × real, preço desconhecido e bloqueios."""
    out: dict[str, dict] = {}
    with session_scope() as s:
        for row in s.exec(select(CostEntry).where(CostEntry.production_id == production_id)):
            t = out.setdefault(row.task, {"settled": 0, "released": 0, "reserved": 0, "estimated_usd": 0.0,
                                          "actual_usd": 0.0, "unknown_price": 0})
            t[row.status] = t.get(row.status, 0) + 1
            t["estimated_usd"] = round(t["estimated_usd"] + (row.estimated_usd or 0), 6)
            t["actual_usd"] = round(t["actual_usd"] + (row.actual_usd or 0), 6)
            t["unknown_price"] += 0 if row.price_known else 1
    return out


class VisionBudget:
    """Teto aplicado à visão paga (OpenAI/Claude), compartilhado entre as cenas em paralelo.

    Interface antiga (allow_paid/settle) sobre o Ledger: cada allow_paid() abre uma reserva do custo médio por
    avaliação, mais uma folga (reserve) para as etapas seguintes; settle(custo) fecha a reserva da mesma thread.
    """

    def __init__(self, production_id: int, limit: float | None = None, reserve: float = DEFAULT_RESERVE):
        self.production_id = production_id
        self.ledger = Ledger.for_production(production_id, limit)
        self.limit = self.ledger.limit
        self.reserve = reserve
        self.lock = threading.Lock()
        self.calls = 0
        self.spent = 0.0
        self.exhausted = False
        self._local = threading.local()

    @property
    def pending(self) -> int:
        return sum(1 for r in self.ledger.open.values() if r.task == "vision")

    def _avg(self) -> float:
        return self.spent / self.calls if self.calls else DEFAULT_CALL_COST

    def allow_paid(self) -> bool:
        """Reserva lugar para uma chamada se couber no teto. Teto 0 = sem limite."""
        if self.limit <= 0:
            return True
        with self.lock:
            if self.exhausted:
                return False
        try:
            res = self.ledger.reserve("vision", self._avg(), headroom=self.reserve)
        except BudgetExceeded:
            with self.lock:
                self.exhausted = True
            return False
        stack = getattr(self._local, "stack", None)
        if stack is None:
            stack = self._local.stack = []
        stack.append(res)
        return True

    def settle(self, cost: float | None) -> None:
        """Fecha a chamada liberada por allow_paid (cost None = não aconteceu)."""
        stack = getattr(self._local, "stack", None)
        if stack:
            stack.pop().settle(cost)
        if cost is not None:
            with self.lock:
                self.calls += 1
                self.spent += cost


def paid_llm(ctx, task: str, call, estimate: float, step: str | None = None):
    """Chamada paga sob o teto: reserva a estimativa, chama, registra o uso (sucesso ou falha cobrada) e fecha a
    reserva com o custo real. Lança BudgetExceeded sem chamar se não couber."""
    from .providers.llm.base import failed_usage

    res = Ledger.for_production(ctx.production_id).reserve(task, estimate, provider="anthropic")
    try:
        parsed, usage = call()
    except Exception as e:
        from .providers.llm.base import BatchPending

        if isinstance(e, BatchPending):  # nada cobrado ainda: o custo entra quando o lote voltar
            res.settle(None)
            raise
        paid = failed_usage(e)
        if paid is not None:  # a Anthropic cobra a tentativa mesmo sem resposta aproveitável
            ctx.record_llm(paid, step=step) if step else ctx.record_llm(paid)
            res.settle(paid.cost, getattr(paid, "price_known", True), getattr(paid, "model", ""))
        else:
            res.settle(None)
        raise
    ctx.record_llm(usage, step=step) if step else ctx.record_llm(usage)
    res.settle(getattr(usage, "cost", 0.0), getattr(usage, "price_known", True), getattr(usage, "model", ""))
    return parsed, usage
