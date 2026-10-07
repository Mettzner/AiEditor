"""Etapas [1] e [2] do funil: filtro técnico + estilo/franquias (só metadados) e pré-ranking por texto."""
from __future__ import annotations

import re

from ...providers.stock import Candidate, priority_of
from ...providers.stock.archives import ARCHIVE_PROVIDERS
from ..visual import metadata_check

STOP = {"the", "a", "an", "of", "and", "in", "on", "at", "to", "with", "for", "by", "video", "stock", "footage",
        "de", "da", "do", "em", "um", "uma", "com", "para", "que", "os", "as", "no", "na"}
# Fotos passam por crop 16:9 com Ken Burns, então aceitam proporções comuns de câmera (3:2, 4:3).
PHOTO_ASPECT = (1.3, 2.4)
YT_GAMING = "20"


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-zà-ÿ0-9]+", (text or "").lower()) if len(t) > 2 and t not in STOP}


def _stem(t: str) -> str:
    return t[:-1] if t.endswith("s") and len(t) > 3 else t


def technical_filter(cands: list[Candidate], duration: float, used: set[str], cfg: dict,
                     youtube_max_duration: float = 1800, must_avoid: list[str] | None = None,
                     global_avoid: list[str] | None = None, allowance: str = "real_only",
                     allowed_styles: list[str] | None = None,
                     franchises: list[str] | None = None) -> tuple[list[Candidate], dict[str, int]]:
    """Devolve os aprovados e a contagem de reprovações por motivo (vai para o relatório).

    Termos de estilo nos metadados viram rejeição (cena real_only) ou penalidade de −30% (guardada em
    `c.penalty`); franquias, interfaces e marca d'água são sempre rejeitadas.
    """
    allowed = allowed_styles or ["real_footage"]
    out, rejected = [], {}

    def reject(reason: str) -> None:
        rejected[reason] = rejected.get(reason, 0) + 1

    for c in cands:
        if c.key in used:
            reject("já usado")
            continue
        if c.category == YT_GAMING and "video_game" not in allowed:
            reject("estilo")
            continue
        block, penalty = metadata_check(c.title, allowance, allowed, must_avoid or [], global_avoid or [],
                                        franchises or [])
        if block:
            reject(block)
            continue
        c.penalty = penalty
        aspect = c.width / c.height if c.width and c.height else 0
        if c.provider in ARCHIVE_PROVIDERS:
            # arquivo histórico: resolução menor e 4:3 são normais (o render recorta em 16:9); a LoC não informa
            # dimensões na busca, então dimensão desconhecida passa e a IA de visão julga a qualidade
            if c.is_image:
                if c.width and c.height and not (1.0 <= aspect <= 2.6):
                    reject("proporção")
                elif c.height and c.height < int(cfg.get("archive_min_photo_height", 500)):
                    reject("resolução")
                else:
                    out.append(c)
            elif not (1.2 <= aspect <= 2.4):
                reject("proporção")
            elif c.duration < duration + cfg["duration_margin"]:
                reject("curto")
            elif c.height < int(cfg.get("archive_min_video_height", 240)):
                reject("resolução")
            else:
                out.append(c)
            continue
        if c.is_image:
            lo, hi = PHOTO_ASPECT
            if not (lo <= aspect <= hi):
                reject("proporção")
            elif c.height < 720:
                reject("resolução")
            else:
                out.append(c)
            continue
        if not (cfg["min_aspect"] <= aspect <= cfg["max_aspect"]):
            reject("proporção")
            continue
        if c.duration < duration + cfg["duration_margin"]:
            reject("curto")
            continue
        if c.provider in ("youtube", "authorized_youtube", "authorized_local"):
            if c.height < cfg.get("youtube_min_height", 720):
                reject("resolução")
            elif c.duration > youtube_max_duration:
                reject("longo demais")
            else:
                out.append(c)
            continue
        if c.height < cfg["min_height"] or not any(r.height >= cfg["min_height"] for r in c.renditions):
            reject("sem 1080p")
            continue
        out.append(c)
    return out, rejected


def text_score(c: Candidate, queries: list[str], visual_intent: str, duration: float, subject: str = "",
               must_show: list[str] | None = None) -> float:
    """0,6 × sobreposição com assunto + must_show e 0,4 × com o resto; sem nenhuma palavra do assunto, −50%;
    estilo não permitido pela cena, −30% (c.penalty)."""
    # título/tags da fonte + o que uma IA de visão já viu nesse candidato em outra cena (observado, não aprovado)
    t = {_stem(x) for x in _tokens(c.title)} | {_stem(x) for x in _tokens(getattr(c, "observed", ""))}
    subj = {_stem(x) for x in _tokens(subject)}
    key = subj | {_stem(x) for m in (must_show or []) for x in _tokens(m)}
    rest = {_stem(x) for q in queries for x in _tokens(q)} | {_stem(x) for x in _tokens(visual_intent)}
    rest -= key
    key_overlap = len(key & t) / max(1, len(key)) if key else 0.0
    rest_overlap = len(rest & t) / max(1, len(rest)) if rest else 0.0
    base = 0.6 * key_overlap + 0.4 * rest_overlap
    bonus = (0.1 if c.is_image or c.duration >= duration * 1.5 else 0) + (
        0.05 / priority_of(c.provider) if c.provider != "youtube" else 0.05)
    score = 10 * min(1.0, base + bonus)
    if subj and not (subj & t):
        score *= 0.5
    return round(score * getattr(c, "penalty", 1.0), 2)


IDENTITY_CAP = 0.3  # sem nenhum nome da identidade exigida: nota × 0,3 (qualidade não compensa)
SEMANTIC_WEIGHT = 0.4


def _has_identity(c: Candidate, terms: list[str]) -> bool:
    from ..semantics import norm

    text = norm(" ".join([c.title, getattr(c, "description", ""), getattr(c, "observed", "")]))
    return any(t in text for t in terms)


def diversify(ranked: list[Candidate], keep: int, per_channel: int = 2) -> list[Candidate]:
    """Amostra para a visão: os melhores, com no máximo `per_channel` do mesmo canal/autor; vagas que sobrarem
    voltam a ser preenchidas pela ordem do ranking."""
    out: list[Candidate] = []
    count: dict[str, int] = {}
    for c in ranked:
        who = (getattr(c, "channel_id", "") or c.author or c.provider).lower()
        if count.get(who, 0) < per_channel:
            out.append(c)
            count[who] = count.get(who, 0) + 1
        if len(out) >= keep:
            return out
    out += [c for c in ranked if c not in out][: keep - len(out)]
    return out


def rank(cands: list[Candidate], queries: list[str], visual_intent: str, duration: float,
         keep: int | None = None, subject: str = "", must_show: list[str] | None = None,
         identity: list[str] | None = None, exact: bool = False, per_channel: int | None = None) -> list[Candidate]:
    """Ranking híbrido: lexical (sempre) + semântico (opcional, providers/embeddings) + identidade.

    Cena de identidade exata: candidato sem nenhum nome da entidade (título, descrição, o que uma IA já viu) tem
    a nota limitada, e nenhum bônus semântico/qualidade a recupera."""
    from ...providers import embeddings

    for c in cands:
        c.score = text_score(c, queries, visual_intent, duration, subject, must_show)
    sims = embeddings.similarities(" ".join([subject, visual_intent, *queries[:1]]),
                                   [" ".join([c.title, getattr(c, "observed", "")]) for c in cands])
    if sims:
        for c, sim in zip(cands, sims):
            c.score = round((1 - SEMANTIC_WEIGHT) * c.score + SEMANTIC_WEIGHT * 10 * sim, 2)
    if identity and exact:
        for c in cands:
            if not _has_identity(c, identity):
                c.score = round(c.score * IDENTITY_CAP, 2)
    ranked = sorted(cands, key=lambda c: c.score, reverse=True)
    if keep and per_channel:
        return diversify(ranked, keep, per_channel)
    return ranked[:keep] if keep else ranked
