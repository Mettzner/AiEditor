"""Alocação de fontes por cena, sobre o TEMPO e não sobre a contagem (§6.4)."""
from __future__ import annotations

from ..config import load_settings
from ..models import ProductionConfig

# Fase 1: ainda sem adapter do YouTube; as cenas alocadas a ele migram para os bancos.
YOUTUBE_IMPLEMENTED = False


def targets(total: float, config: ProductionConfig) -> dict[str, float]:
    real = total * config.real_pct / 100
    yt = real * config.youtube_pct / 100 if load_settings()["youtube"]["enabled"] else 0.0
    return {"ai": total - real, "youtube": yt, "stock": real - yt}


def allocate(scenes: list[dict], config: ProductionConfig) -> dict[str, str]:
    total = sum(s["end"] - s["start"] for s in scenes)
    goal = targets(total, config)
    result: dict[str, str] = {}
    remaining = list(scenes)

    def fill(source: str, key: str) -> None:
        nonlocal remaining
        budget = goal[source]
        if budget <= 0:
            return
        used = 0.0
        for s in sorted(remaining, key=lambda s: s["affinity"][key], reverse=True):
            dur = s["end"] - s["start"]
            # aceita a cena enquanto ela aproxima o total da meta
            if abs(budget - (used + dur)) <= abs(budget - used):
                result[s["id"]] = source
                used += dur
        remaining = [s for s in remaining if s["id"] not in result]

    fill("ai", "ai")
    fill("youtube", "youtube")
    for s in remaining:
        result[s["id"]] = "stock"
    return result


def composition(scenes: list[dict]) -> dict[str, float]:
    """Segundos por fonte efetiva (stock, youtube, ai) a partir de scenes com 'source'."""
    out = {"stock": 0.0, "youtube": 0.0, "ai": 0.0}
    for s in scenes:
        src = s.get("final_source") or s["source"]
        key = "ai" if src.startswith("ai") else src
        out[key] = out.get(key, 0.0) + (s["end"] - s["start"])
    return out
