"""Cliente base da Darkvi (https://darkvi.com/api).

Formatos do TTS confirmados em produção; os de imagens (/v1/images) ainda são inferidos do §8.1,
por isso `_pick` procura as chaves em profundidade.
"""
from __future__ import annotations

from typing import Any

from ...config import get_secret
from ..http import ProviderError, json_or_raise, request

BASE_URL = "https://darkvi.com/api"

AUTH_ERRORS = {
    "AUTH_MISSING_TOKEN": "Token da Darkvi não informado.",
    "AUTH_INVALID_TOKEN": "Token da Darkvi inválido ou expirado.",
    "USER_NOT_FOUND": "Usuário da Darkvi não encontrado para este token.",
}


class DarkviError(ProviderError):
    pass


def _token() -> str:
    token = get_secret("darkvi")
    if not token:
        raise DarkviError(AUTH_ERRORS["AUTH_MISSING_TOKEN"], 401)
    return token


def _pick(body: Any, *keys: str, max_depth: int = 3) -> Any:
    """Procura a primeira chave em largura: topo, depois data/result, depois data.created etc.

    Formatos confirmados: POST /tts → {ok, message, data: {created: {id, ...}}};
    GET /tts/:id → {id, status: "DONE", ...} no topo.
    """
    level = [body] if isinstance(body, dict) else []
    for _ in range(max_depth + 1):
        for container in level:
            for k in keys:
                if container.get(k) is not None:
                    return container[k]
        level = [v for c in level for v in c.values() if isinstance(v, dict)]
        if not level:
            break
    return None


def call(method: str, path: str, *, json: Any = None, files: Any = None, data: Any = None,
         raw: bool = False) -> Any:
    resp = request(method, f"{BASE_URL}{path}", headers={"Authorization": f"Bearer {_token()}"},
                   json=json, files=files, data=data)
    if raw:
        if resp.status_code >= 400:
            raise DarkviError(f"Darkvi {path}: HTTP {resp.status_code}", resp.status_code, resp.text[:300])
        return resp
    try:
        return json_or_raise(resp, f"Darkvi {path}")
    except ProviderError as e:
        body = e.body if isinstance(e.body, dict) else {}
        code = str(body.get("code") or "")
        if code in AUTH_ERRORS:
            msg = AUTH_ERRORS[code]
        elif code == "INSUFFICIENT_BALANCE":
            msg = f"Saldo insuficiente na Darkvi ({body.get('details') or body.get('message')})"
        elif body.get("message"):
            msg = f"Darkvi {path}: {body['message']} (HTTP {e.status}{', ' + code if code else ''})"
        else:
            msg = f"Darkvi {path}: HTTP {e.status} {e.body!r}"[:400]
        raise DarkviError(msg, e.status, e.body) from e


def validate() -> dict:
    body = call("GET", "/v1/auth/validate")
    return {"ok": True, "detail": body}
