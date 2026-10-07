"""Claude via SDK oficial: saída estruturada (Pydantic), prompt caching, cache local de respostas e Batch API.

Estrutura de toda chamada (OTIMIZACAO_CUSTO_CLAUDE.md §3), do mais estável para o mais variável:
  1. system: instruções fixas da etapa (regras, exemplos)          ← cache_control
  2. contexto da produção (preset, brief, roteiro numerado)        ← cache_control
     (uma lista de blocos ganha um breakpoint em cada um: ex. roteiro e, depois, a bíblia da produção)
  3. pedido desta chamada (ex.: "planeje as unidades 0–89")         (sem cache)
Nada variável (data, id da produção) entra nas partes 1 e 2; o contexto é gerado uma vez por produção e
reaproveitado byte a byte. Parâmetros de raciocínio iguais em todas as chamadas da etapa.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import TypeVar

import anthropic
from pydantic import BaseModel
from sqlmodel import select

from ...config import get_secret
from ..http import ProviderError
from .base import LLMUsage

T = TypeVar("T", bound=BaseModel)
log = logging.getLogger("aieditor.llm")
PROMPT_VERSION = "v2"  # mude ao alterar prompts: invalida o cache local de respostas
BATCH_POLL_SECONDS = 30


def _prices(model: str) -> tuple[float, float, float, float]:
    """(entrada, saída, gravação de cache, leitura de cache) por milhão de tokens, editáveis na Configuração."""
    from ...db import session_scope
    from ...models import CLAUDE_PRICES, ProviderPrice

    with session_scope() as s:
        rows = s.exec(select(ProviderPrice).where(ProviderPrice.provider == f"claude:{model}")).all()
    price = {r.unit: r.price for r in rows}
    default = CLAUDE_PRICES.get(model, CLAUDE_PRICES["claude-sonnet-5-5"])
    return tuple(price.get(u, d) for u, d in zip(("input", "output", "cache_write", "cache_read"), default))  # type: ignore[return-value]


def _usage_from(resp, model: str, task: str, seconds: float, batch: bool = False) -> LLMUsage:
    u = resp.usage
    usage = LLMUsage(
        input_tokens=u.input_tokens or 0, output_tokens=u.output_tokens or 0,
        cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        cache_read_input_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        seconds=round(seconds, 2), model=model, task=task, batch=batch)
    p_in, p_out, p_cw, p_cr = _prices(model)
    usage.cost = (usage.input_tokens * p_in + usage.output_tokens * p_out
                  + usage.cache_creation_input_tokens * p_cw + usage.cache_read_input_tokens * p_cr) / 1e6
    if batch:
        usage.cost *= 0.5  # Batch API: 50% de desconto em tudo
    return usage


def _cache_key(task: str, model: str, system: str, context: str | None, user: str, schema: type, effort, thinking) -> str:
    raw = json.dumps([PROMPT_VERSION, task, model, system, context, user, schema.__name__,
                      json.dumps(schema.model_json_schema(), sort_keys=True), effort, thinking],
                     ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


class LLMCallFailed(ProviderError):
    """Resposta cobrada mas inútil (cortada por max_tokens, recusa ou JSON inválido).

    `usage` (preenchido por structured) leva o custo junto, para quem chamou registrá-lo na produção;
    `truncated` diz que repetir o mesmo pedido corta de novo: é preciso pedir menos por chamada."""

    def __init__(self, message: str, response, truncated: bool = False):
        super().__init__(message)
        self.response = response
        self.truncated = truncated
        self.usage: LLMUsage | None = None


def _with_format(params: dict, schema: type) -> dict:
    """Pedido com saída estruturada no esquema (o mesmo formato que o messages.parse do SDK monta)."""
    from anthropic.lib._parse._transform import transform_schema
    from pydantic import TypeAdapter

    body = dict(params)
    body["output_config"] = {**body.get("output_config", {}), "format": {
        "type": "json_schema", "schema": transform_schema(TypeAdapter(schema).json_schema())}}
    return body


def _parse_message(msg, schema: type[T]) -> T:
    if msg.stop_reason == "refusal":
        raise LLMCallFailed(f"Claude recusou a solicitação: {getattr(msg, 'stop_details', None)}", msg)
    if msg.stop_reason == "max_tokens":
        raise LLMCallFailed(f"Resposta do Claude cortada por max_tokens ({msg.usage.output_tokens} tokens de saída)",
                            msg, truncated=True)
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    try:
        return schema.model_validate_json(text)
    except ValueError as e:
        raise LLMCallFailed(f"Claude não devolveu JSON válido para o schema: {str(e)[:200]}", msg) from e


class AnthropicLLM:
    id = "anthropic"

    def _client(self) -> anthropic.Anthropic:
        key = get_secret("anthropic")
        if not key:
            raise ProviderError("Chave da Anthropic não configurada", 401)
        return anthropic.Anthropic(api_key=key, max_retries=3)

    # ---------------------------------------------------------------- parâmetros por modelo
    @staticmethod
    def _params(model: str, system: str, user: str, context: str | list[str] | None, effort: str | None,
                thinking: str, max_tokens: int, cache_ttl: str) -> dict:
        cc = {"type": "ephemeral"} if cache_ttl == "5m" else {"type": "ephemeral", "ttl": cache_ttl}
        content = []
        for block in ([context] if isinstance(context, str) else context or []):
            if block:
                content.append({"type": "text", "text": block, "cache_control": cc})
        content.append({"type": "text", "text": user})
        params: dict = {
            "model": model, "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": cc}],
            "messages": [{"role": "user", "content": content}],
        }
        if model.startswith("claude-haiku-4-5"):
            # Haiku 4.5: sem effort (dá erro) e sem raciocínio estendido por padrão
            return params
        if thinking == "off":
            # Sonnet 5.5 não aceita thinking "disabled": "between_tools" desliga o raciocínio (effort ≤ high)
            params["thinking"] = {"type": "between_tools"} if model.startswith("claude-sonnet-5-5") else {"type": "adaptive"}
        else:
            params["thinking"] = {"type": "adaptive"}
        if effort:
            params["output_config"] = {"effort": effort}
        return params

    @staticmethod
    def _uses_fallbacks(model: str) -> bool:
        # refazer a chamada noutro modelo em caso de recusa (só modelos que aceitam o parâmetro)
        return model.startswith(("claude-sonnet-5-5", "claude-opus-5", "claude-fable-5"))

    # ---------------------------------------------------------------- chamada estruturada
    def structured(self, *, model: str, system: str, user: str, schema: type[T], effort: str | None = None,
                   max_tokens: int = 16000, context: str | list[str] | None = None, task: str = "",
                   thinking: str = "adaptive", use_cache: bool = True, batch: bool = False,
                   cache_ttl: str = "5m") -> tuple[T, LLMUsage]:
        from ...db import session_scope
        from ...models import LlmCache

        key = _cache_key(task, model, system, context, user, schema, effort, thinking)
        if use_cache:
            with session_scope() as s:
                hit = s.get(LlmCache, key)
                if hit:
                    return schema.model_validate(hit.result), LLMUsage(model=model, task=task, local_cache=True)
        params = self._params(model, system, user, context, effort, thinking, max_tokens, cache_ttl)
        t = time.perf_counter()
        try:
            if batch:
                parsed, resp = self._run_batch(params, schema, task)
            else:
                parsed, resp = self._run(params, schema)
        except LLMCallFailed as e:
            # a resposta foi cobrada mesmo inútil: o uso vai junto no erro para entrar no custo da produção
            e.usage = _usage_from(e.response, model, task, time.perf_counter() - t, batch)
            log.warning("claude %s %s falhou (%s): in=%d out=%d US$%.4f", task, model, e, e.usage.input_tokens,
                        e.usage.output_tokens, e.usage.cost)
            raise
        usage = _usage_from(resp, model, task, time.perf_counter() - t, batch)
        log.info("claude %s %s: in=%d out=%d cache_w=%d cache_r=%d US$%.4f %.1fs", task, model, usage.input_tokens,
                 usage.output_tokens, usage.cache_creation_input_tokens, usage.cache_read_input_tokens, usage.cost,
                 usage.seconds)
        with session_scope() as s:
            s.merge(LlmCache(key=key, task=task, model=model, result=parsed.model_dump()))
            s.commit()
        return parsed, usage

    def _run(self, params: dict, schema: type[T]):
        # create + validação aqui (não messages.parse): o parse do SDK lança o erro de JSON antes de devolver a
        # resposta, e uma resposta cortada sumia sem o uso cobrado e sem o motivo (max_tokens)
        client = self._client()
        body = _with_format(params, schema)
        if self._uses_fallbacks(params["model"]):
            resp = client.beta.messages.create(**body, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        else:
            resp = client.messages.create(**body)
        return _parse_message(resp, schema), resp

    # ---------------------------------------------------------------- Batch API (modo econômico)
    def _run_batch(self, params: dict, schema: type[T], task: str):
        """Envia 1 pedido pela Message Batches API (50% mais barata, assíncrona) e espera o resultado."""
        client = self._client()
        body = _with_format(params, schema)
        batch = client.messages.batches.create(requests=[{"custom_id": task or "req", "params": body}])
        log.info("lote %s enviado (%s)", batch.id, task)
        while True:
            batch = client.messages.batches.retrieve(batch.id)
            if batch.processing_status == "ended":
                break
            time.sleep(BATCH_POLL_SECONDS)
        for item in client.messages.batches.results(batch.id):
            if item.result.type != "succeeded":
                raise ProviderError(f"Lote {batch.id}: {item.result.type}")
            msg = item.result.message
            return _parse_message(msg, schema), msg
        raise ProviderError(f"Lote {batch.id} sem resultado")

    def test(self) -> dict:
        info = self._client().models.retrieve("claude-sonnet-5-5")
        return {"ok": True, "detail": info.display_name}
