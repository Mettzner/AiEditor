"""Freesound API v2 (https://freesound.org/docs/api/): busca de efeitos sonoros por texto.

A chave (token) é grátis em https://freesound.org/apiv2/apply. Baixamos a prévia em MP3 de alta qualidade
(~128 kbps), que é pública e não exige OAuth; para efeitos curtos é mais que suficiente. Por padrão só
CC0 (domínio público, sem atribuição); CC BY entra se o usuário permitir, e vai para o creditos.txt.
Nunca "Attribution NonCommercial": vídeos monetizados são uso comercial.
"""
from __future__ import annotations

from ...config import get_secret
from ..http import ProviderError, json_or_raise, request

API = "https://freesound.org/apiv2/search/text/"
CC0 = "Creative Commons 0"
CC_BY = "Attribution"

# o que buscar por categoria: (query, duração mínima, duração máxima)
QUERIES = {
    "whoosh": ("whoosh transition", 0.4, 2.5),
    "swoosh_soft": ("soft swoosh", 0.15, 1.2),
    "impact": ("cinematic impact boom", 0.8, 5.0),
    "riser": ("cinematic riser", 1.5, 6.0),
    "click": ("typewriter single key", 0.03, 0.5),
}


def _token() -> str:
    key = get_secret("freesound")
    if not key:
        raise ProviderError("Chave do Freesound não configurada", 401)
    return key


def _license_name(url: str) -> str:
    u = url.lower()
    if "publicdomain/zero" in u:
        return CC0
    if "by-nc" in u or "noncommercial" in u:
        return "Attribution NonCommercial"
    if "/by/" in u:
        return CC_BY
    return url


def search(category: str, limit: int = 8, allow_attribution: bool = False) -> list[dict]:
    query, dmin, dmax = QUERIES[category]
    licenses = f'license:("{CC0}" OR "{CC_BY}")' if allow_attribution else f'license:"{CC0}"'
    resp = request("GET", API, params={
        "query": query, "token": _token(), "page_size": limit, "sort": "rating_desc",
        "filter": f"duration:[{dmin} TO {dmax}] {licenses}",
        "fields": "id,name,username,license,duration,previews,url",
    })
    if resp.status_code == 401:
        raise ProviderError("Freesound: chave inválida", 401)
    body = json_or_raise(resp, "Freesound")
    out = []
    for r in body.get("results", []):
        lic = _license_name(r.get("license", ""))
        url = (r.get("previews") or {}).get("preview-hq-mp3")
        if not url or lic not in ((CC0, CC_BY) if allow_attribution else (CC0,)):
            continue
        out.append({"id": r["id"], "name": r.get("name", ""), "author": r.get("username", ""), "license": lic,
                    "duration": r.get("duration"), "preview": url, "page_url": r.get("url", "")})
    return out


def test() -> dict:
    found = search("whoosh", limit=3)
    return {"ok": True, "detail": f"{len(found)} sons CC0 encontrados para 'whoosh'"}
