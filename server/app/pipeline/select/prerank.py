"""Etapa [1] do funil (filtro técnico) + pré-ranking textual barato.

Fase 2 adiciona aqui o ranking de thumbnails num modelo de visão leve e, em vision_rank.py,
a análise de vídeo dos top 5.
"""
from __future__ import annotations

import re

from ...providers.stock import Candidate, priority_of

STOP = {"the", "a", "an", "of", "and", "in", "on", "at", "to", "with", "for", "by", "video", "stock", "footage"}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-zà-ÿ0-9]+", text.lower()) if len(t) > 2 and t not in STOP}


def technical_filter(cands: list[Candidate], duration: float, used: set[str], min_height: int = 1080,
                     require_duration: bool = True) -> list[Candidate]:
    out = []
    for c in cands:
        if c.key in used or not c.renditions:
            continue
        if c.width and c.height and c.width <= c.height:  # só horizontal
            continue
        if c.height < min_height:
            continue
        if require_duration and c.duration < duration:
            continue
        out.append(c)
    return out


def text_score(c: Candidate, queries: list[str], duration: float) -> float:
    q = set().union(*(_tokens(x) for x in queries)) if queries else set()
    t = _tokens(c.title)
    overlap = len(q & t) / max(1, len(q))
    primary = len(_tokens(queries[0]) & t) / max(1, len(_tokens(queries[0]))) if queries else 0
    from_primary_query = 0.15 if queries and c.query == queries[0] else 0
    dur_fit = 0.1 if c.duration >= duration * 1.5 else 0
    prio = 0.05 / priority_of(c.provider)
    return round(10 * min(1.0, 0.45 * overlap + 0.35 * primary + from_primary_query + dur_fit + prio), 2)


def rank(cands: list[Candidate], queries: list[str], duration: float) -> list[Candidate]:
    for c in cands:
        c.score = text_score(c, queries, duration)
    return sorted(cands, key=lambda c: c.score, reverse=True)
