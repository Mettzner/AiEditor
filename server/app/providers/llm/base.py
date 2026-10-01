"""Adapter de LLM: cada tarefa (plan, direct, rewrite...) escolhe provedor e modelo na Configuração."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0


class LLMProvider(Protocol):
    id: str

    def structured(self, *, model: str, system: str, user: str, schema: type[T], effort: str | None = None,
                   max_tokens: int = 16000) -> tuple[T, LLMUsage]: ...

    def test(self) -> dict: ...


def get_llm(task: str) -> tuple[LLMProvider, dict]:
    from ...config import load_settings
    from .anthropic import AnthropicLLM

    cfg = load_settings()["llm"].get(task) or load_settings()["llm"]["plan"]
    registry: dict[str, LLMProvider] = {"anthropic": AnthropicLLM()}
    provider = registry.get(cfg["provider"])
    if provider is None:
        raise NotImplementedError(f"Provedor de LLM '{cfg['provider']}' ainda não implementado")
    return provider, cfg
