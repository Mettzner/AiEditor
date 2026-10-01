"""Busca multi-fonte com cache em disco (24 h)."""
from __future__ import annotations

import hashlib
import json
import time

from ...config import CACHE_DIR
from ...providers.http import ProviderError
from ...providers.stock import Candidate, Rendition, StockProvider

TTL = 24 * 3600
_dir = CACHE_DIR / "search"
_dir.mkdir(parents=True, exist_ok=True)


def _from_dict(d: dict) -> Candidate:
    d = dict(d)
    d["renditions"] = [Rendition(**r) for r in d.get("renditions", [])]
    return Candidate(**d)


def search(provider: StockProvider, query: str, per_page: int, lang: str = "en") -> list[Candidate]:
    """Cache de 24 h: exigência do Pixabay e economia de cota do Pexels."""
    key = hashlib.sha1(f"{provider.id}|{query.lower()}|{per_page}|{lang}".encode()).hexdigest()
    path = _dir / f"{key}.json"
    if path.exists() and time.time() - path.stat().st_mtime < TTL:
        return [_from_dict(d) for d in json.loads(path.read_text(encoding="utf-8"))]
    results = provider.search(query, per_page=per_page, lang=lang)
    path.write_text(json.dumps([c.to_dict() for c in results]), encoding="utf-8")
    return results


def search_all(providers: list[StockProvider], queries: list[str], per_page: int,
               lang: str = "en") -> tuple[list[Candidate], list[str]]:
    seen: dict[str, Candidate] = {}
    errors: list[str] = []
    for q in queries:
        for p in providers:
            try:
                for c in search(p, q, per_page, lang):
                    seen.setdefault(c.key, c)
            except ProviderError as e:
                errors.append(f"{p.id} '{q}': {e}")
    return list(seen.values()), errors
