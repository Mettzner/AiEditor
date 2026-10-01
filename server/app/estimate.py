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
    llm_in = windows * (script_tokens + 1500 + 90 * 30)
    llm_out = scenes * 220 + windows * 3000
    llm_cost = (llm_in / 1e6 * prices.get(("anthropic", "per_1m_input_tokens"), 0)
                + llm_out / 1e6 * prices.get(("anthropic", "per_1m_output_tokens"), 0))
    tts_cost = (duration / 60 * prices.get(("darkvi", "per_tts_minute"), 0)) if config.audio_mode == "tts" else 0
    img_cost = ai_images * prices.get(("darkvi", "per_image"), 0)
    music_cost = 0.0  # biblioteca local; geração entra na Fase 4

    yt_scenes = round(goal["youtube"] / config.avg_scene_seconds) if duration else 0
    darkvi = settings["darkvi"]
    minutes = (
        (2 if config.audio_mode == "tts" else 0.3)
        + duration / 60 * 0.35  # whisper small na CPU
        + windows * 1.2  # planejamento
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
            "youtube_units_needed": yt_scenes * 3 * 100,
            "youtube_daily_quota": 10000,
        },
        "time_minutes": round(minutes),
    }
