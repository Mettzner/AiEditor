"""Estimativa de duração, cenas, custo, cotas e tempo (Passo 3 e Passo 4 da Criação)."""
from __future__ import annotations

import math

from sqlmodel import select

from .config import load_settings
from .db import session_scope
from .models import ProductionConfig, ProviderPrice
from .pipeline.allocate import targets


def _prices() -> dict[tuple[str, str], float]:
    with session_scope() as s:
        return {(p.provider, p.unit): p.price for p in s.exec(select(ProviderPrice))}


def estimate(config: ProductionConfig, script: str, audio_seconds: float | None = None) -> dict:
    settings = load_settings()
    words = len(script.split())
    wpm = settings["words_per_minute"].get(config.language, 150)
    duration = audio_seconds or (words / wpm * 60 if words else 0)
    scenes = max(1, round(duration / config.avg_scene_seconds)) if duration else 0
    goal = targets(duration, config)
    ai_scenes = round(goal["ai"] / config.avg_scene_seconds) if duration else 0
    ai_images = ai_scenes  # Fase 1: vídeo de IA ainda vira imagem

    prices = _prices()
    script_tokens = words * 1.4
    units = duration / (config.avg_scene_seconds * 0.6) if duration else 0
    windows = max(1, math.ceil(units / 90)) if duration else 0
    # planejamento: sistema fixo (~4.000 tokens, em cache) + roteiro em unidades (gravado 1x no cache pela chamada
    # da Bíblia de Contexto e lido por todas as janelas de cenas) + bíblia no pedido de cada janela (~2.000 tokens)
    # + saída (bíblia ~1.500 tokens; ~330 tokens por cena, sem repetir a narração)
    from .providers.llm.anthropic import _prices as claude_prices

    model = settings["llm"]["plan"]["model"]
    p_in, p_out, p_cw, p_cr = claude_prices(model)
    context_tokens = script_tokens * 1.3 + 400
    llm_cost = ((4000 + context_tokens) * p_cw + windows * (4000 + context_tokens) * p_cr
                + windows * 2150 * p_in + (1500 + scenes * 330 + windows * 1200) * p_out) / 1e6
    if getattr(config, "llm_economy", False):
        llm_cost *= 0.5
    tts_cost = (duration / 60 * prices.get(("darkvi", "per_tts_minute"), 0)) if config.audio_mode == "tts" else 0
    img_cost = ai_images * prices.get(("darkvi", "per_image"), 0)
    music_cost = 0.0  # biblioteca local; geração entra na Fase 4

    yt_scenes = round(goal["youtube"] / config.avg_scene_seconds) if duration else 0
    from .providers.youtube import quota as yt_quota

    yt = yt_quota.status()
    yt_fit = min(yt_scenes, yt["searches_left"])
    darkvi = settings["darkvi"]
    minutes = (
        (2 if config.audio_mode == "tts" else 0.3)
        + duration / 60 * 0.35  # whisper small na CPU
        + 0.4 + windows * 1.2  # Bíblia de Contexto + planejamento
        + scenes * 4 / max(1, settings["selection"]["parallel_scenes"]) / 60 * 3
        + ai_images / 5  # 5 imagens/min
        + duration / 60 * (0.6 if settings["render"]["mode"] == "fast" else 1.1)
        + 1
    )
    return {
        "duration_seconds": round(duration, 1),
        "words": words,
        "scenes": scenes,
        "seconds": {k: round(v, 1) for k, v in goal.items()},
        "ai_media": config.ai_media,
        "cost": {
            "tts": round(tts_cost, 2), "llm": round(llm_cost, 2), "ai": round(img_cost, 2),
            "music": music_cost, "total": round(tts_cost + llm_cost + img_cost + music_cost, 2),
        },
        "quotas": {
            "darkvi_images_needed": ai_images,
            "darkvi_remaining": darkvi.get("remaining"),
            "darkvi_limit": darkvi.get("limit"),
            "darkvi_updated_at": darkvi.get("updated_at"),
            "darkvi_enough": darkvi.get("remaining") is None or ai_images <= int(darkvi["remaining"]),
            "youtube_scenes": yt_scenes,
            "youtube_scenes_fit": yt_fit,
            "youtube_units_needed": yt_scenes * (yt_quota.SEARCH_COST + yt_quota.VIDEOS_COST),
            "youtube_available": yt["available"],
            "youtube_daily_quota": yt["daily_quota"],
        },
        "time_minutes": round(minutes),
    }
