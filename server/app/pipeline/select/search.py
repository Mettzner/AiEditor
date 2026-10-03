"""Busca multi-fonte com cache no SQLite (search_cache, validade em selection.search_cache_days).

Atende à exigência de cache do Pixabay (≥ 24 h) e evita gastar cota repetindo buscas.
"""
from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Callable

from ...config import load_settings
from ...db import session_scope
from ...models import SearchCache, now
from ...providers.http import ProviderError
from ...providers.stock.base import Candidate, candidate_from_dict


_stats_lock = threading.Lock()


def _norm(query: str) -> str:
    return " ".join(query.lower().split())


def cache_key(provider: str, kind: str, query: str, per_page: int, lang: str) -> str:
    return hashlib.sha1(f"{provider}|{kind}|{_norm(query)}|{per_page}|{lang}".encode()).hexdigest()


def cached_search(provider: str, kind: str, query: str, per_page: int, lang: str,
                  fetch: Callable[[], list[Candidate]], stats: dict | None = None) -> list[Candidate]:
    key = cache_key(provider, kind, query, per_page, lang)
    days = float(load_settings()["selection"].get("search_cache_days", 7))
    with session_scope() as s:
        row = s.get(SearchCache, key)
        created = row.created_at if row else None
        if created is not None and created.tzinfo is None:
            created = created.replace(tzinfo=now().tzinfo)
        if row and created and now() - created < timedelta(days=days):
            return [candidate_from_dict(d) for d in row.results]
    results = fetch()
    if stats is not None:
        with _stats_lock:
            stats["searches"] = stats.get("searches", 0) + 1
    with session_scope() as s:
        row = s.get(SearchCache, key) or SearchCache(key=key, provider=provider, query=_norm(query))
        row.results = [c.to_dict() for c in results]
        row.created_at = now()
        s.add(row)
        s.commit()
    return results


def search_all(providers: list, queries: list[str], per_page: int, lang: str = "en", photos: bool = False,
               stats: dict | None = None, media_type: str | None = None) -> tuple[list[Candidate], list[str]]:
    """Busca em todos os bancos, com todas as queries × provedores em paralelo e deduplicação.

    media_type: video_type (vídeos) ou image_type (fotos) do Pixabay, conforme os estilos aceitos na cena.
    """
    kind = ("photo" if photos else "video") + (f":{media_type}" if media_type else "")
    jobs = [(p, q) for q in queries for p in providers]

    def one(job):
        p, q = job
        fn = p.search_photos if photos else p.search
        extra = {("image_type" if photos else "video_type"): media_type} if media_type else {}
        try:
            return cached_search(p.id, kind, q, per_page, lang,
                                 lambda: fn(q, per_page=per_page, lang=lang, **extra), stats), None
        except ProviderError as e:
            return [], f"{p.id} '{q}': {e}"

    seen: dict[str, Candidate] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(jobs)))) as pool:
        for results, err in pool.map(one, jobs):
            if err:
                errors.append(err)
            for c in results:
                seen.setdefault(c.key, c)
    return list(seen.values()), errors
