"""Claude via SDK oficial, com saída estruturada (Pydantic) e fallback do servidor em caso de recusa."""
from __future__ import annotations

from typing import TypeVar

import anthropic
from pydantic import BaseModel
from sqlmodel import select

from ...config import get_secret
from ..http import ProviderError
from .base import LLMUsage

T = TypeVar("T", bound=BaseModel)


def _prices() -> tuple[float, float]:
    from ...db import session_scope
    from ...models import ProviderPrice

    with session_scope() as s:
        rows = s.exec(select(ProviderPrice).where(ProviderPrice.provider == "anthropic")).all()
    price = {r.unit: r.price for r in rows}
    return price.get("per_1m_input_tokens", 0.0), price.get("per_1m_output_tokens", 0.0)


class AnthropicLLM:
    id = "anthropic"

    def _client(self) -> anthropic.Anthropic:
        key = get_secret("anthropic")
        if not key:
            raise ProviderError("Chave da Anthropic não configurada", 401)
        return anthropic.Anthropic(api_key=key, max_retries=3)

    def structured(self, *, model: str, system: str, user: str, schema: type[T], effort: str | None = None,
                   max_tokens: int = 16000) -> tuple[T, LLMUsage]:
        output_config = {"effort": effort} if effort else anthropic.omit
        response = self._client().beta.messages.parse(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
            output_config=output_config,
            # Em recusa por política, o próprio servidor refaz a chamada num modelo alternativo.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise ProviderError(f"Claude recusou a solicitação: {response.stop_details}")
        if response.stop_reason == "max_tokens":
            raise ProviderError("Resposta do Claude cortada por max_tokens")
        parsed = response.parsed_output
        if parsed is None:
            raise ProviderError("Claude não devolveu JSON válido para o schema")
        p_in, p_out = _prices()
        usage = LLMUsage(response.usage.input_tokens, response.usage.output_tokens)
        usage.cost = usage.input_tokens / 1e6 * p_in + usage.output_tokens / 1e6 * p_out
        return parsed, usage

    def test(self) -> dict:
        info = self._client().models.retrieve("claude-opus-5-5")
        return {"ok": True, "detail": info.display_name}
