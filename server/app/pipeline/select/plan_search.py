"""Plano de busca por bloco (Fase C1/C4): agrupa cenas por contexto + entidade (ou assunto), com catálogo de
candidatos compartilhado, consulta documental que preserva identidade e orçamento por importância da cena.

- Grupo: (context_id, entidades da cena) ou (context_id, assunto normalizado). Cenas do mesmo grupo reaproveitam
  os candidatos já descobertos (sem nova busca) e usam a mesma consulta documental (cache → sem nova cota).
- Consulta documental (YouTube e acervos): mantém nome próprio, nome científico, ano e lugar — o que a busca
  genérica dos bancos remove. Nunca leva termos de must_avoid.
- Importância: evidência exata/identidade exata = "high" (mais páginas e comparação entre fontes);
  ilustração = "normal"; metáfora = "low" (1 página, nada de paginação).
- O artefato search_plan.json registra consultas, filtros, páginas, idioma, orçamento, candidatos e motivos de
  rejeição por grupo.
"""
from __future__ import annotations

import re

from ..semantics import norm
from ..validation import needs_exact

DOC_MAX_WORDS = 7


def identity_terms(scene: dict, bible: dict) -> list[str]:
    """Nomes que PROVAM a identidade da cena (entidade, aliases, nome científico), normalizados."""
    by_id = {e["id"]: e for e in bible.get("entities") or []}
    terms: list[str] = []
    for eid in scene.get("entity_ids") or []:
        e = by_id.get(eid)
        if not e:
            continue
        for n in [e.get("name"), e.get("scientific_name"), *(e.get("aliases") or [])]:
            n = norm(n or "")
            if n and len(n) > 2 and n not in terms:
                terms.append(n)
    return terms


def documentary_query(scene: dict, bible: dict, must_avoid: list[str] | None = None) -> str:
    """Consulta para fontes documentais: a do planejamento, com a entidade obrigatória e sem termos proibidos."""
    raw = (scene.get("documentary_query") or "").strip()
    if not raw:
        return ""
    banned = {w for a in must_avoid or [] for w in norm(a).split()}
    words = [w for w in re.findall(r"[\w'-]+", raw) if norm(w) not in banned]
    terms = identity_terms(scene, bible)
    joined = norm(" ".join(words))
    if terms and not any(t in joined for t in terms):
        e = next((e for e in bible.get("entities") or [] if e["id"] in (scene.get("entity_ids") or [])), None)
        best = (e.get("scientific_name") or e.get("name")) if e else ""
        words = best.split() + words
    return " ".join(words[:DOC_MAX_WORDS])


def importance(scene: dict) -> str:
    if needs_exact(scene) or scene.get("required_identity") in ("exact_person", "exact_event", "species"):
        return "high"
    if scene.get("visual_role") == "metaphor" or scene.get("literal") is False:
        return "low"
    return "normal"


def group_key(scene: dict) -> str:
    ents = sorted(scene.get("entity_ids") or [])
    base = ",".join(ents) if ents else norm(scene.get("subject") or "")[:40]
    return f"{scene.get('context_id') or '-'}|{base or scene['id']}"


def build_search_plan(scenes: list[dict], bible: dict, cfg: dict) -> dict:
    """Grupos de busca e orçamento por importância. Não faz chamada nenhuma."""
    groups: dict[str, dict] = {}
    for s in scenes:
        key = group_key(s)
        g = groups.setdefault(key, {"key": key, "context_id": s.get("context_id"), "entity_ids": s.get("entity_ids") or [],
                                    "subject": s.get("subject"), "scenes": [], "importance": "low",
                                    "documentary_query": "", "queries": {}, "language": "en",
                                    "budget": {}, "pages": {}, "candidates": {}, "rejected": {}})
        g["scenes"].append(s["id"])
        imp = importance(s)
        if ["low", "normal", "high"].index(imp) > ["low", "normal", "high"].index(g["importance"]):
            g["importance"] = imp
        doc = documentary_query(s, bible, s.get("must_avoid"))
        if doc and not g["documentary_query"]:
            g["documentary_query"] = doc
    max_pages = int(cfg.get("max_youtube_pages_per_group", 2))
    for g in groups.values():
        g["budget"] = {
            "youtube_pages": max_pages if g["importance"] == "high" else (1 if g["importance"] == "low"
                                                                          else min(2, max_pages)),
            "youtube_queries": int(cfg.get("youtube_queries_per_scene", 1)) + (1 if g["importance"] == "high" else 0),
            "compare_sources": g["importance"] == "high" and bool(cfg.get("compare_sources_for_exact", True)),
        }
    return {"groups": groups, "scene_group": {sid: g["key"] for g in groups.values() for sid in g["scenes"]}}
