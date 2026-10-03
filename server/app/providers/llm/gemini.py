"""Gemini (google-genai) para a folha de miniaturas: 1 imagem → avaliação por linha em JSON.

Velocidade (AJUSTE_ESTILO_CONTEXTUAL.md §10.3): raciocínio no mínimo, resposta curta, timeout de 25 s
com 1 nova tentativa.

Cota: o plano gratuito tem limites baixos (ex.: 20 requisições/dia por modelo). Um 429 de cota DIÁRIA
abre o disjuntor até a meia-noite do Pacífico: o Gemini fica indisponível e a seleção segue pelo ranking
de texto na hora, sem esperar. Um 429 POR MINUTO espera o tempo indicado pelo Google uma única vez.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta

from pydantic import BaseModel
from sqlmodel import select

from ...config import get_secret, load_settings, update_settings
from ..http import ProviderError

TIMEOUT_MS = 25_000
MAX_OUTPUT_TOKENS = 800
MAX_MINUTE_WAIT = 45


class RowScore(BaseModel):
    row: int
    score: float
    best_frame: int
    reason: str


class SheetScores(BaseModel):
    candidates: list[RowScore]


class GeminiQuotaExhausted(ProviderError):
    pass


_clients: dict[str, object] = {}
_gate = threading.Lock()
_client_lock = threading.Lock()


def _prices() -> tuple[float, float]:
    from ...db import session_scope
    from ...models import ProviderPrice

    with session_scope() as s:
        rows = s.exec(select(ProviderPrice).where(ProviderPrice.provider == "gemini")).all()
    price = {r.unit: r.price for r in rows}
    return price.get("per_1m_input_tokens", 0.0), price.get("per_1m_output_tokens", 0.0)


# ---------------------------------------------------------------- disjuntor de cota
def _next_pacific_midnight() -> datetime:
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("America/Los_Angeles"))
    return (now + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)


def quota_status() -> dict:
    g = load_settings().get("gemini", {})
    until = g.get("blocked_until")
    if until and datetime.fromisoformat(until) > datetime.now(datetime.fromisoformat(until).tzinfo):
        return {"blocked": True, "until": until, "reason": g.get("blocked_reason", "")}
    return {"blocked": False, "until": None, "reason": ""}


def _block(until: datetime, reason: str) -> None:
    update_settings({"gemini": {"blocked_until": until.isoformat(), "blocked_reason": reason}})


def available() -> bool:
    return bool(get_secret("gemini")) and not quota_status()["blocked"]


def _client():
    """Cliente reaproveitado por chave. Um cliente temporário é coletado no meio da paginação e o SDK
    fecha a conexão ("Cannot send a request, as the client has been closed")."""
    from google import genai
    from google.genai import types

    key = get_secret("gemini")
    if not key:
        raise ProviderError("Chave do Gemini não configurada", 401)
    # trava: sem ela, cenas paralelas criavam clientes ao mesmo tempo e um substituía o outro em uso
    # (o substituído era coletado e fechado → "WinError 10038: não é um soquete")
    with _client_lock:
        if key not in _clients:
            _clients.clear()  # chave trocada na Configuração: descarta o cliente antigo
            _clients[key] = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=TIMEOUT_MS))
        return _clients[key]


def _quota_info(e) -> tuple[str, float | None]:
    """(quotaId, retryDelay em segundos) a partir dos detalhes do erro 429."""
    quota_id, delay = "", None
    for d in ((getattr(e, "details", None) or {}).get("error", {}) or {}).get("details", []) or []:
        t = d.get("@type", "")
        if "QuotaFailure" in t:
            quota_id = " ".join(v.get("quotaId", "") for v in d.get("violations", []))
        if "RetryInfo" in t:
            m = re.match(r"([\d.]+)s", d.get("retryDelay", "") or "")
            delay = float(m.group(1)) if m else None
    return quota_id, delay


def _thinking(model: str):
    from google.genai import types

    if model.startswith("gemini-2"):
        return types.ThinkingConfig(thinking_budget=0)
    return types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL)


def rate_sheet(image_jpeg: bytes, prompt: str, model: str, schema: type[BaseModel] = SheetScores) -> tuple:
    """Devolve (avaliação, custo). Lança GeminiQuotaExhausted quando a cota diária acaba."""
    from google.genai import errors, types

    if quota_status()["blocked"]:
        raise GeminiQuotaExhausted("Cota do Gemini esgotada até a meia-noite do Pacífico")

    def call(thinking=True):
        cfg = dict(response_mime_type="application/json", response_schema=schema, temperature=0.2,
                   max_output_tokens=MAX_OUTPUT_TOKENS)
        if thinking:
            cfg["thinking_config"] = _thinking(model)
        return _client().models.generate_content(
            model=model, contents=[types.Part.from_bytes(data=image_jpeg, mime_type="image/jpeg"), prompt],
            config=types.GenerateContentConfig(**cfg))

    resp = None
    waited_minute = False
    for attempt in range(3):
        try:
            resp = call(thinking=attempt < 2)  # se o modelo recusar o nível de raciocínio, a 3ª vai sem
            break
        except errors.APIError as e:
            code = getattr(e, "code", None)
            if code == 429:
                quota_id, delay = _quota_info(e)
                if "PerDay" in quota_id or "per_day" in quota_id.lower():
                    _block(_next_pacific_midnight(), f"cota diária do Gemini esgotada ({quota_id})")
                    raise GeminiQuotaExhausted(f"Cota diária do Gemini esgotada ({quota_id})") from e
                if waited_minute or (delay or 0) > MAX_MINUTE_WAIT:
                    raise ProviderError(f"Gemini: limite por minuto ({quota_id})") from e
                with _gate:  # uma espera só, compartilhada entre as cenas paralelas
                    time.sleep(min(MAX_MINUTE_WAIT, delay or 10))
                waited_minute = True
                continue
            if code == 400 and attempt < 2 and "think" in str(e).lower():
                continue  # nível de raciocínio não suportado por este modelo
            if code in (500, 503, 504) and attempt == 0:
                time.sleep(2)
                continue
            raise ProviderError(f"Gemini: {e}") from e
        except Exception as e:  # noqa: BLE001 — timeout de rede
            if attempt == 0 and "timeout" in type(e).__name__.lower() + str(e).lower():
                continue
            raise ProviderError(f"Gemini: {e}") from e
    if resp is None:
        raise ProviderError("Gemini não respondeu")
    parsed = resp.parsed
    if not isinstance(parsed, schema):
        raise ProviderError(f"Gemini não devolveu JSON válido: {(resp.text or '')[:200]}")
    usage = resp.usage_metadata
    p_in, p_out = _prices()
    cost = 0.0
    if usage:
        cost = ((usage.prompt_token_count or 0) / 1e6 * p_in
                + ((usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)) / 1e6 * p_out)
    return parsed, cost


def test() -> dict:
    flash = sorted(m.name.removeprefix("models/") for m in _client().models.list() if "flash" in (m.name or ""))
    st = quota_status()
    extra = f" · BLOQUEADO até {st['until']} ({st['reason']})" if st["blocked"] else ""
    return {"ok": True, "detail": f"modelos Flash disponíveis: {', '.join(flash[-8:]) or 'nenhum'}{extra}"}
