"""OpenAI (ChatGPT) na IA de visão: 2ª opção, quando o Gemini fica sem cota, antes do Claude.

API REST de Chat Completions (sem SDK): a folha de miniaturas vai como imagem em data URI e a resposta vem em
JSON no formato do schema. O gpt-4.1-mini custa uma fração do Claude por folha. Chave em Configuração → Chaves
(Credential Manager, provedor "openai"); modelo em settings["openai"]["vision_model"].
"""
from __future__ import annotations

import base64
import json
import threading
import time

from pydantic import BaseModel, ValidationError

from ...config import get_secret, load_settings
from ..http import ProviderError, json_or_raise, request
from .base import LLMUsage

URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4.1-mini"
# US$ por 1M tokens (entrada, saída); modelo fora da tabela usa o do gpt-4.1-mini
PRICES = {"gpt-4.1-mini": (0.40, 1.60), "gpt-4.1-nano": (0.10, 0.40), "gpt-4.1": (2.00, 8.00),
          "gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.50, 10.00)}
MAX_OUTPUT_TOKENS = 1500

_off = threading.Event()  # sem crédito / chave inválida: fica fora até o worker reiniciar
_off_reason = ""


class OpenAIUnavailable(ProviderError):
    pass


def model() -> str:
    return (load_settings().get("openai") or {}).get("vision_model") or DEFAULT_MODEL


def available() -> bool:
    return bool(get_secret("openai")) and not _off.is_set()


def _disable(reason: str) -> None:
    global _off_reason
    _off_reason = reason
    _off.set()


def _headers() -> dict:
    key = get_secret("openai")
    if not key:
        raise OpenAIUnavailable("Chave da OpenAI não configurada")
    return {"Authorization": f"Bearer {key}"}


def rate_sheet(image_jpeg: bytes, prompt: str, schema: type[BaseModel], system: str) -> tuple[BaseModel, LLMUsage]:
    if _off.is_set():
        raise OpenAIUnavailable(f"OpenAI indisponível: {_off_reason}")
    name = model()
    image = "data:image/jpeg;base64," + base64.b64encode(image_jpeg).decode()
    body = {
        "model": name,
        "temperature": 0.2,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "response_format": {"type": "json_schema", "json_schema": {
            "name": schema.__name__, "schema": schema.model_json_schema(), "strict": False}},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": [{"type": "text", "text": prompt},
                                         {"type": "image_url", "image_url": {"url": image, "detail": "high"}}]},
        ],
    }
    started = time.perf_counter()
    resp = request("POST", f"{URL}/chat/completions", headers=_headers(), json=body, timeout=60, retries=1)
    if resp.status_code in (401, 403):
        _disable("chave inválida")
        raise OpenAIUnavailable("Chave da OpenAI inválida")
    if resp.status_code == 429 and "insufficient_quota" in resp.text:
        _disable("sem crédito na conta")
        raise OpenAIUnavailable("Conta da OpenAI sem crédito")
    data = json_or_raise(resp, "OpenAI")
    text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    try:
        parsed = schema.model_validate(json.loads(text))
    except (ValueError, ValidationError) as e:
        raise ProviderError(f"OpenAI não devolveu JSON válido: {text[:200]}") from e
    u = data.get("usage") or {}
    p_in, p_out = PRICES.get(name, PRICES[DEFAULT_MODEL])
    usage = LLMUsage(input_tokens=u.get("prompt_tokens", 0), output_tokens=u.get("completion_tokens", 0),
                     seconds=round(time.perf_counter() - started, 2), model=name, task="vision")
    usage.cost = (usage.input_tokens * p_in + usage.output_tokens * p_out) / 1e6
    return parsed, usage


def test() -> dict:
    resp = request("GET", f"{URL}/models/{model()}", headers=_headers(), retries=0)
    if resp.status_code in (401, 403):
        return {"ok": False, "error": "Chave inválida"}
    if resp.status_code == 404:
        return {"ok": False, "error": f"Modelo {model()} não disponível nesta conta"}
    json_or_raise(resp, "OpenAI")
    _off.clear()
    return {"ok": True, "detail": f"{model()} disponível"}
