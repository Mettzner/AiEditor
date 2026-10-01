"""Cliente base da Darkvi (https://darkvi.com/api).

ATENÇÃO: os formatos de resposta abaixo foram inferidos da especificação (§6.1/§8.1).
`_pick` procura o campo em `body`, `body.data` e `body.result` para tolerar variações;
confirme os nomes reais com a documentação e ajuste se necessário.
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


def _pick(body: Any, *keys: str) -> Any:
    if not isinstance(body, dict):
        return None
    for container in (body, body.get("data"), body.get("result")):
        if isinstance(container, dict):
            for k in keys:
                if container.get(k) is not None:
                    return container[k]
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
        code = _pick(e.body, "code", "error") if isinstance(e.body, dict) else None
        msg = AUTH_ERRORS.get(str(code), f"Darkvi {path}: HTTP {e.status} {e.body!r}"[:400])
        raise DarkviError(msg, e.status, e.body) from e


def validate() -> dict:
    body = call("GET", "/v1/auth/validate")
    return {"ok": True, "detail": body}
