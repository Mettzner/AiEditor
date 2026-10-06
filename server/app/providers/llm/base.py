"""Adapter de LLM: cada etapa (plan, rewrite, overlay...) escolhe provedor, modelo e raciocínio na Configuração."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    seconds: float = 0.0
    model: str = ""
    task: str = ""
    local_cache: bool = False  # resposta reaproveitada do cache local (custo zero)
    batch: bool = False

    def to_dict(self) -> dict:
        return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in asdict(self).items()}


class LLMProvider(Protocol):
    id: str

    def structured(self, *, model: str, system: str, user: str, schema: type[T], effort: str | None = None,
                   max_tokens: int = 16000, context: str | None = None, task: str = "",
                   thinking: str = "adaptive", use_cache: bool = True, batch: bool = False,
                   cache_ttl: str = "5m") -> tuple[T, LLMUsage]: ...

    def test(self) -> dict: ...


def get_llm(task: str) -> tuple[LLMProvider, dict]:
    from ...config import load_settings
    from .anthropic import AnthropicLLM

    settings = load_settings()["llm"]
    cfg = dict(settings.get(task) or settings["plan"])
    cfg.setdefault("thinking", "adaptive")
    cfg["cache_ttl"] = settings.get("cache_ttl", "5m")
    registry: dict[str, LLMProvider] = {"anthropic": AnthropicLLM()}
    provider = registry.get(cfg["provider"])
    if provider is None:
        raise NotImplementedError(f"Provedor de LLM '{cfg['provider']}' ainda não implementado")
    return provider, cfg


def call_llm(task: str, *, system: str, user: str, schema: type[T], context: str | None = None,
             max_tokens: int = 8000, batch: bool = False) -> tuple[T, LLMUsage]:
    """Atalho: chama o modelo configurado para a etapa com os parâmetros dela (iguais em toda chamada da etapa,
    para não invalidar o cache do prompt)."""
    llm, cfg = get_llm(task)
    return llm.structured(model=cfg["model"], system=system, user=user, schema=schema, effort=cfg.get("effort"),
                          max_tokens=max_tokens, context=context, task=task, thinking=cfg.get("thinking", "adaptive"),
                          batch=batch, cache_ttl=cfg.get("cache_ttl", "5m"))


def failed_usage(error: BaseException) -> LLMUsage | None:
    """Uso cobrado de uma chamada que falhou (resposta cortada, recusa, JSON inválido); None se nada foi cobrado.

    Quem chama registra esse uso na produção: a Anthropic cobra a tentativa mesmo sem resposta aproveitável."""
    return getattr(error, "usage", None)
