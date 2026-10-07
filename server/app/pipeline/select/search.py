"""Busca multi-fonte com cache no SQLite (search_cache) e single-flight.

Regras (Fase A do plano de melhorias):
- O cache é lido ANTES de qualquer verificação de cota: com a cota esgotada e um resultado válido no cache, a busca
  é atendida sem chamada externa. A cota só é reservada pelo `fetch` (busca remota).
- Validade por provedor (settings.selection.search_cache_ttl_hours, com "default"); o Pixabay exige cache ≥ 24 h.
  Linhas vencidas são apagadas por `purge_expired()` (dados de API não ficam guardados indefinidamente).
- Resultado vazio de verdade é cacheado (status "empty"); falha transitória (rede, 5xx) vira status "error" com TTL
  curto (search_error_ttl_seconds) e é relançada para quem consultar nesse intervalo. Falta de cota nunca é
  cacheada: depende do saldo, não da consulta.
- Single-flight (app/singleflight.py): buscas idênticas ao mesmo tempo fazem UM fetch, também entre processos.
- stats recebe: searches (fetches remotos), cache_hit, cache_miss, cache_empty, cache_error, quota_blocked.
"""
from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from ...config import load_settings
from ... import singleflight
from ...db import session_scope
from ...models import SearchCache, now
from ...providers.http import ProviderError
from ...providers.stock.base import Candidate, candidate_from_dict

_stats_lock = threading.Lock()
DEFAULT_TTL_HOURS = {"default": 168, "pixabay": 168, "pexels": 168, "youtube": 168, "archives": 336}


class CachedProviderError(ProviderError):
    """A mesma busca falhou há pouco (cache negativo de TTL curto)."""


def _norm(query: str) -> str:
    return " ".join(query.lower().split())


def cache_key(provider: str, kind: str, query: str, per_page: int, lang: str, page: int = 0) -> str:
    raw = f"{provider}|{kind}|{_norm(query)}|{per_page}|{lang}" + (f"|p{page}" if page else "")
    return hashlib.sha1(raw.encode()).hexdigest()


def ttl_hours(provider: str) -> float:
    sel = load_settings()["selection"]
    table = {**DEFAULT_TTL_HOURS, **(sel.get("search_cache_ttl_hours") or {})}
    if provider in table:
        return float(table[provider])
    if "search_cache_days" in sel:  # configuração antiga, valor único em dias
        return float(sel["search_cache_days"]) * 24
    return float(table["default"])


def _error_ttl() -> float:
    return float(load_settings()["selection"].get("search_error_ttl_seconds", 120))


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _bump(stats: dict | None, key: str, n: int = 1) -> None:
    if stats is None:
        return
    with _stats_lock:
        stats[key] = stats.get(key, 0) + n


def _read(key: str, provider: str) -> tuple[str, SearchCache | None]:
    """("hit" | "error" | "miss", linha). Linhas antigas sem expires_at vencem pelo created_at + TTL."""
    with session_scope() as s:
        row = s.get(SearchCache, key)
        if row is None:
            return "miss", None
        s.expunge(row)
    expires = _aware(row.expires_at)
    if expires is None:
        created = _aware(row.created_at) or now()
        expires = created + timedelta(hours=ttl_hours(provider))
    if now() >= expires:
        return "miss", row
    return ("error" if row.status == "error" else "hit"), row


def _store(key: str, provider: str, query: str, results: list[Candidate] | None, status: str,
           error: str | None = None, next_page_token: str | None = None) -> None:
    ttl = timedelta(seconds=_error_ttl()) if status == "error" else timedelta(hours=ttl_hours(provider))
    with session_scope() as s:
        row = s.get(SearchCache, key) or SearchCache(key=key, provider=provider, query=_norm(query))
        row.results = [c.to_dict() for c in results or []]
        row.created_at = now()
        row.expires_at = now() + ttl
        row.status = status
        row.error = error
        row.next_page_token = next_page_token
        s.add(row)
        try:
            s.commit()
        except IntegrityError:  # outra cena gravou a mesma busca ao mesmo tempo: o resultado já está no cache
            s.rollback()


def _serve(state: str, row: SearchCache, stats: dict | None) -> list[Candidate]:
    if state == "error":
        _bump(stats, "cache_error")
        raise CachedProviderError(f"busca falhou há pouco (cache de erro): {row.error}")
    _bump(stats, "cache_hit")
    if row.status == "empty":
        _bump(stats, "cache_empty")
    return [candidate_from_dict(d) for d in row.results]


def cached_fetch(provider: str, kind: str, query: str, per_page: int, lang: str,
                 fetch: Callable[[], tuple[list[Candidate], str | None]], stats: dict | None = None,
                 page: int = 0) -> tuple[list[Candidate], str | None]:
    """Como cached_search, para fetches que também devolvem o token da próxima página."""
    key = cache_key(provider, kind, query, per_page, lang, page)

    def lookup():
        state, row = _read(key, provider)
        return (state, row) if state != "miss" and row is not None else None

    def compute():
        _bump(stats, "cache_miss")
        try:
            results, next_token = fetch()
        except ProviderError as e:
            from ...providers.youtube.client import CredentialError, QuotaExhausted

            if isinstance(e, QuotaExhausted):
                _bump(stats, "quota_blocked")
            elif not isinstance(e, CredentialError):  # credencial não é transitória: não cacheia
                _store(key, provider, query, None, "error", str(e)[:300])
            raise
        _bump(stats, "searches")
        _store(key, provider, query, results, "ok" if results else "empty", next_page_token=next_token)
        return ("fresh", (results, next_token))

    state, value = singleflight.run(f"search:{key}", lookup, compute)
    if state == "fresh":
        return value
    return _serve(state, value, stats), value.next_page_token


def cached_search(provider: str, kind: str, query: str, per_page: int, lang: str,
                  fetch: Callable[[], list[Candidate]], stats: dict | None = None) -> list[Candidate]:
    return cached_fetch(provider, kind, query, per_page, lang, lambda: (fetch(), None), stats)[0]


def purge_expired(grace_hours: float = 0) -> int:
    """Apaga resultados vencidos (respeita o TTL de cada provedor). Devolve quantas linhas saíram."""
    removed = 0
    cutoff_now = now() - timedelta(hours=grace_hours)
    with session_scope() as s:
        for row in list(s.exec(select(SearchCache))):
            expires = _aware(row.expires_at) or ((_aware(row.created_at) or now())
                                                 + timedelta(hours=ttl_hours(row.provider)))
            if expires <= cutoff_now:
                s.delete(row)
                removed += 1
        s.commit()
    singleflight.purge()
    return removed


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
