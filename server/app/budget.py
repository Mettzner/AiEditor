"""Teto de gasto com IA por produção (settings["budget"]).

O planejamento tem custo previsível (cresce com o roteiro); o que dispara é a IA de visão quando o Gemini fica
sem cota e o Claude assume: cada folha de miniaturas é uma chamada paga, e um vídeo de 30 minutos tem centenas.
O orçamento libera o Claude na visão só enquanto o gasto da produção + a próxima chamada + uma reserva para as
etapas seguintes couber no teto. Estourou: a escolha segue pelo ranking de texto, de graça.
"""
from __future__ import annotations

import threading

from sqlmodel import select

from .config import load_settings
from .db import session_scope
from .models import Production

# direção, correção de idioma dos overlays e validação de imagens de IA ainda vêm depois da seleção
DEFAULT_RESERVE = 0.15
DEFAULT_CALL_COST = 0.01


def limit_usd() -> float:
    return float((load_settings().get("budget") or {}).get("max_usd_per_production", 2.0) or 0)


def production_cost(production_id: int) -> float:
    with session_scope() as s:
        cost = s.exec(select(Production.cost_actual).where(Production.id == production_id)).first()
    return float(cost or 0)


class VisionBudget:
    """Compartilhado entre as cenas avaliadas em paralelo (thread-safe)."""

    def __init__(self, production_id: int, limit: float | None = None, reserve: float = DEFAULT_RESERVE):
        self.production_id = production_id
        self.limit = limit_usd() if limit is None else limit
        self.reserve = reserve
        self.lock = threading.Lock()
        self.calls = 0
        self.spent = 0.0
        self.pending = 0  # chamadas liberadas ainda sem custo registrado
        self.exhausted = False

    def _avg(self) -> float:
        return self.spent / self.calls if self.calls else DEFAULT_CALL_COST

    def allow_paid(self) -> bool:
        """Reserva lugar para uma chamada se couber no teto. Teto 0 = sem limite."""
        if self.limit <= 0:
            return True
        with self.lock:
            if self.exhausted:
                return False
            projected = production_cost(self.production_id) + (self.pending + 1) * self._avg() + self.reserve
            if projected > self.limit:
                self.exhausted = True
                return False
            self.pending += 1
            return True

    def settle(self, cost: float | None) -> None:
        """Fecha a chamada liberada por allow_paid (cost None = não aconteceu)."""
        with self.lock:
            self.pending = max(0, self.pending - 1)
            if cost is not None:
                self.calls += 1
                self.spent += cost
