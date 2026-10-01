"""Pexels Videos API (https://www.pexels.com/api/documentation/#videos-search)."""
from __future__ import annotations

from ...config import get_secret
from ..http import ProviderError, json_or_raise, request
from .base import Candidate, Rendition

LOCALES = {"en": "en-US", "pt": "pt-BR", "es": "es-ES", "fr": "fr-FR", "de": "de-DE", "it": "it-IT"}
# A documentação atual usa /v1/videos/search; a rota antiga /videos/search fica como reserva.
ENDPOINTS = ["https://api.pexels.com/v1/videos/search", "https://api.pexels.com/videos/search"]


class Pexels:
    id = "pexels"

    def __init__(self) -> None:
        self._endpoint = ENDPOINTS[0]

    def _key(self) -> str:
        key = get_secret("pexels")
        if not key:
            raise ProviderError("Chave do Pexels não configurada", 401)
        return key

    def _get(self, params: dict) -> dict:
        for endpoint in [self._endpoint] + [e for e in ENDPOINTS if e != self._endpoint]:
            resp = request("GET", endpoint, headers={"Authorization": self._key()}, params=params)
            if resp.status_code == 404:
                continue
            if resp.status_code == 429:
                raise ProviderError("Pexels: limite de requisições atingido (padrão: 200/h, 20.000/mês)", 429,
                                    {"remaining": resp.headers.get("X-Ratelimit-Remaining"),
                                     "reset": resp.headers.get("X-Ratelimit-Reset")})
            self._endpoint = endpoint
            return json_or_raise(resp, "Pexels")
        raise ProviderError("Pexels: endpoint de busca de vídeos não encontrado", 404)

    def search(self, query: str, per_page: int = 15, lang: str = "en") -> list[Candidate]:
        body = self._get({"query": query, "per_page": per_page, "orientation": "landscape", "size": "large",
                          "locale": LOCALES.get(lang, "en-US")})
        out = []
        for v in body.get("videos", []):
            slug = (v.get("url") or "").rstrip("/").rsplit("/", 1)[-1]
            words = [p for p in slug.split("-") if not p.isdigit()]
            tags = [t if isinstance(t, str) else t.get("title", "") for t in v.get("tags") or []]
            renditions = [
                Rendition(f["link"], f.get("width") or 0, f.get("height") or 0)
                for f in v.get("video_files", [])
                # entradas HLS também vêm como video/mp4, mas com .m3u8 e sem dimensões
                if f.get("link") and f.get("quality") != "hls" and f.get("width") and f.get("height")
                and ".m3u8" not in f["link"]
            ]
            user = v.get("user") or {}
            out.append(Candidate(
                provider=self.id, external_id=str(v["id"]), title=" ".join(words + tags).strip(),
                duration=float(v.get("duration") or 0), width=v.get("width") or 0, height=v.get("height") or 0,
                page_url=v.get("url") or "", thumbnail=v.get("image"), renditions=renditions, query=query,
                author=user.get("name", ""), author_url=user.get("url", ""),
                preview_frames=[p["picture"] for p in v.get("video_pictures", []) if p.get("picture")],
            ))
        return out

    def test(self) -> dict:
        res = self.search("ocean", per_page=1)
        return {"ok": True, "detail": f"{len(res)} resultado(s) via {self._endpoint}"}
