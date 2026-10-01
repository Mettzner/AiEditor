"""Pixabay Videos API (https://pixabay.com/api/docs/#api_search_videos).

Regras da API: resultados em cache por 24 h (feito em pipeline/select/search.py), nada de downloads em
massa, e o `large` pode vir com URL vazia (sem versão 4K).
"""
from __future__ import annotations

from ...config import get_secret
from ..http import ProviderError, json_or_raise, request
from .base import Candidate, Rendition

API = "https://pixabay.com/api/videos/"
LANGS = {"cs", "da", "de", "en", "es", "fr", "id", "it", "hu", "nl", "no", "pl", "pt", "ro", "sk", "fi", "sv",
         "tr", "vi", "th", "bg", "ru", "el", "ja", "ko", "zh"}


class Pixabay:
    id = "pixabay"

    def _key(self) -> str:
        key = get_secret("pixabay")
        if not key:
            raise ProviderError("Chave do Pixabay não configurada", 401)
        return key

    def search(self, query: str, per_page: int = 15, lang: str = "en") -> list[Candidate]:
        resp = request("GET", API, params={
            "key": self._key(), "q": query[:100], "lang": lang if lang in LANGS else "en",
            "per_page": min(200, max(3, per_page)), "safesearch": "true", "video_type": "film",
            "min_width": 1280,
        })
        if resp.status_code == 429:
            raise ProviderError("Pixabay: limite de 100 requisições/min atingido", 429)
        body = json_or_raise(resp, "Pixabay")
        out = []
        for h in body.get("hits", []):
            videos = h.get("videos", {})
            renditions = [Rendition(v["url"], v.get("width") or 0, v.get("height") or 0)
                          for v in videos.values() if isinstance(v, dict) and v.get("url") and v.get("width")]
            largest = max(renditions, key=lambda r: r.height, default=None)
            user = h.get("user", "")
            out.append(Candidate(
                provider=self.id, external_id=str(h["id"]), title=h.get("tags", ""),
                duration=float(h.get("duration") or 0), width=largest.width if largest else 0,
                height=largest.height if largest else 0, page_url=h.get("pageURL", ""),
                thumbnail=(videos.get("medium") or {}).get("thumbnail"), renditions=renditions, query=query,
                author=user, author_url=f"https://pixabay.com/users/{user}-{h.get('user_id')}/" if user else "",
            ))
        return out

    def test(self) -> dict:
        res = self.search("ocean", per_page=3)
        return {"ok": True, "detail": f"{len(res)} resultado(s)"}
