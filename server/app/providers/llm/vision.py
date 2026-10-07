"""IA de visão: quem olha os clipes e as imagens geradas e diz se mostram o que o roteiro pede.

Ordem, do mais barato ao mais caro: Gemini (grátis no plano gratuito, mas a cota acaba em quase toda produção),
OpenAI (gpt-4.1-mini, fração de centavo por folha) e Claude (Haiku). Sem visão a escolha vira só o texto dos
títulos, às cegas; por isso a próxima assume quando a anterior não pode (cota, sem crédito, erro).

As pagas (OpenAI e Claude) respeitam o teto de gasto da produção (app/budget.py): perto dele, a avaliação para e
a seleção continua pelo ranking de texto. A produção nunca para por causa do teto.

`settings["vision"]["provider"]`: "auto" (todas, nessa ordem), "gemini", "openai" ou "claude".
"""
from __future__ import annotations

import logging
from typing import Callable

from pydantic import BaseModel

from ...config import get_secret, load_settings
from ..http import ProviderError
from . import gemini, openai_vision

log = logging.getLogger("aieditor.vision")

CLAUDE_SYSTEM = ("You are a strict photo editor for a documentary channel. You look at the image and follow the "
                 "instructions in the request exactly. Judge only what is really visible. Answer only with the JSON "
                 "the schema asks for.")


def _mode() -> str:
    return (load_settings().get("vision") or {}).get("provider", "auto")


def claude_available() -> bool:
    return _mode() in ("auto", "claude") and bool(get_secret("anthropic"))


def openai_usable() -> bool:
    return _mode() in ("auto", "openai") and openai_vision.available()


def gemini_usable() -> bool:
    return _mode() in ("auto", "gemini") and gemini.available()


def paid_available() -> bool:
    return openai_usable() or claude_available()


def available() -> bool:
    return gemini_usable() or paid_available()


def signature(gemini_model: str) -> str:
    """Quem pode responder uma avaliação (modo + modelos configurados). Entra na chave do cache de visão: trocar de
    modelo invalida as notas; o provedor que de fato respondeu fica registrado junto do resultado."""
    from .base import get_llm

    try:
        claude_model = get_llm("vision")[1]["model"]
    except Exception:  # noqa: BLE001
        claude_model = "?"
    return "|".join([_mode(), f"gemini={gemini_model}", f"openai={openai_vision.model()}", f"claude={claude_model}"])


class VisionBudgetExceeded(ProviderError):
    """O teto de gasto da produção não comporta mais uma avaliação paga."""


def _paid(call: Callable[[], tuple], budget, record: Callable | None) -> tuple:
    """Chamada paga sob o teto: reserva lugar, registra o custo na produção e fecha a reserva."""
    if budget is not None and not budget.allow_paid():
        raise VisionBudgetExceeded("Teto de gasto da produção atingido: avaliação pelo ranking de texto")
    try:
        parsed, usage = call()
    except Exception:
        if budget is not None:
            budget.settle(None)
        raise
    if record is not None:
        record(usage)
    if budget is not None:
        budget.settle(usage.cost)
    return parsed, usage


def rate_sheet(image_jpeg: bytes, prompt: str, gemini_model: str, schema: type[BaseModel], budget=None,
               record: Callable | None = None) -> tuple:
    """(avaliação, custo, provedor). Lança GeminiQuotaExhausted só quando não há outra IA para assumir.

    budget (app.budget.VisionBudget) limita as pagas ao teto da produção; record(usage) registra cada chamada
    paga no llm_usage.json e no custo da produção."""
    if gemini_usable():
        try:
            parsed, cost = gemini.rate_sheet(image_jpeg, prompt, gemini_model, schema=schema)
            return parsed, cost, "gemini"
        except ProviderError as e:  # inclui GeminiQuotaExhausted
            if not paid_available():
                raise
            log.info("Gemini indisponível (%s): a próxima IA de visão assume", e)
    if openai_usable():
        try:
            parsed, usage = _paid(lambda: openai_vision.rate_sheet(image_jpeg, prompt, schema, CLAUDE_SYSTEM),
                                  budget, record)
            return parsed, usage.cost, "openai"
        except VisionBudgetExceeded:
            raise
        except ProviderError as e:
            if not claude_available():
                raise
            log.info("OpenAI indisponível (%s): Claude assume a avaliação", e)
    if not claude_available():
        raise ProviderError("Nenhuma IA de visão disponível (configure Gemini, OpenAI ou Anthropic)")
    from .base import call_llm

    parsed, usage = _paid(lambda: call_llm("vision", system=CLAUDE_SYSTEM, user=prompt, schema=schema,
                                           images=[image_jpeg], max_tokens=4000), budget, record)
    return parsed, usage.cost, "claude"
