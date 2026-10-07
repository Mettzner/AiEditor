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
    wpm = settings["words_per_minute"].get(config.lang, 150)
    duration = audio_seconds or (words / wpm * 60 if words else 0)
    scenes = max(1, round(duration / config.avg_scene_seconds)) if duration else 0
    goal = targets(duration, config)
    ai_scenes = round(goal["ai"] / config.avg_scene_seconds) if duration else 0
    ai_images = ai_scenes  # Fase 1: vídeo de IA ainda vira imagem

    prices = _prices()
    script_tokens = words * 1.4
    units = duration / (config.avg_scene_seconds * 0.6) if duration else 0
    from .pipeline.plan import WINDOW_UNITS

    windows = max(1, math.ceil(units / WINDOW_UNITS)) if duration else 0
    from .providers.llm.anthropic import price_info

    llm_cfg = settings["llm"]
    context_tokens = script_tokens * 1.3 + 400
    prefix = 4000 + context_tokens
    economy = bool(getattr(config, "llm_economy", False))
    unknown_prices: list[str] = []

    def llm_task(task: str, calls_low: float, calls_high: float, in_fresh: float, out_low: float, out_high: float,
                 cached_prefix: float = 0.0, first_writes: int = 0) -> dict:
        """Faixa de custo de uma tarefa: calls × (entrada nova + saída) + prefixo em cache (1ª gravação, demais
        leituras). out_* são tokens de saída por chamada."""
        model = (llm_cfg.get(task) or llm_cfg["plan"])["model"]
        (p_in, p_out, p_cw, p_cr), known = price_info(model)
        if not known:
            unknown_prices.append(model)

        def cost(calls: float, out: float) -> float:
            if calls <= 0:
                return 0.0
            writes = min(calls, first_writes) if cached_prefix else 0
            reads = max(0.0, calls - writes) if cached_prefix else 0
            return (writes * cached_prefix * p_cw + reads * cached_prefix * p_cr + calls * in_fresh * p_in
                    + calls * out * p_out) / 1e6

        lo, hi = cost(calls_low, out_low), cost(calls_high, out_high)
        if economy and task in ("bible", "plan"):  # Batch API: desconto só nessas chamadas
            lo, hi = lo * 0.5, hi * 0.5
        return {"model": model, "calls": [calls_low, calls_high], "low": round(lo, 4), "high": round(hi, 4),
                "price_known": known}

    real_scenes = round((goal["youtube"] + goal["stock"]) / config.avg_scene_seconds) if duration else 0
    by_task = {
        # Bíblia: lê o roteiro inteiro com raciocínio alto (saída com raciocínio 4–16 mil tokens)
        "bible": llm_task("bible", 1 if duration else 0, 1 if duration else 0, 150, 4000, 16000, prefix, 1),
        # cenas: janelas de 45 unidades; a 1ª grava o prefixo, as demais leem do cache (até 1 retentativa cada)
        "plan": llm_task("plan", windows, windows * 1.3, 2150, scenes / max(1, windows) * 300,
                         scenes / max(1, windows) * 420 + 1200, prefix, 2),
        # reescrita de buscas: 0 a 1 chamada em lote por etapa (+1 do fallback de IA)
        "rewrite": llm_task("rewrite", 0, 2 if real_scenes else 0, 120 * min(real_scenes, 40), 0,
                            120 * min(real_scenes, 40)),
        "overlay": llm_task("overlay", 0, 1 if duration else 0, 600, 0, 400),
    }
    llm_low = sum(t["low"] for t in by_task.values())
    llm_high = sum(t["high"] for t in by_task.values())
    llm_cost = (llm_low + llm_high) / 2
    # visão: Gemini grátis no melhor caso; sem cota, OpenAI/Claude por folha (até o teto da produção)
    sheets_low = 0
    sheets_high = real_scenes * 2 + ai_images * 2
    vision_paid = 0.0
    if (settings.get("vision") or {}).get("provider", "auto") in ("auto", "claude", "openai"):
        vision_paid = sheets_high * (0.002 if (settings.get("vision") or {}).get("provider") == "openai" else 0.006)
    budget_cap = float((settings.get("budget") or {}).get("max_usd_per_production", 0) or 0)
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
        + 1.0 + windows * 1.2  # Bíblia de Contexto (com raciocínio) + planejamento
        + scenes * 4 / max(1, settings["selection"]["parallel_scenes"]) / 60 * 3
        + ai_images / 5  # 5 imagens/min
        + duration / 60 * (0.6 if settings["render"]["mode"] == "fast" else 1.1)
        + 1
    )
    total_low = round(tts_cost + llm_low + img_cost + music_cost, 2)
    total_high_raw = tts_cost + llm_high + img_cost + music_cost + vision_paid
    total_high = round(min(total_high_raw, budget_cap + tts_cost + img_cost) if budget_cap else total_high_raw, 2)
    from .models import PRICES_AS_OF

    assumptions = [
        f"~{script_tokens:.0f} tokens de roteiro ({words} palavras × 1,4); {scenes} cenas de "
        f"{config.avg_scene_seconds:.0f}s; {windows} janela(s) de planejamento",
        "planejamento com cache de prompt: a 1ª chamada grava o prefixo, as demais leem a 10%",
        "visão: Gemini no plano grátis = US$ 0 (faixa baixa); sem cota, até 2 folhas por cena avaliadas "
        "pela OpenAI/Claude (faixa alta)",
        "TTS e imagens da Darkvi pelo preço cadastrado (incluso no plano = US$ 0)",
    ]
    if budget_cap:
        assumptions.append(f"a faixa alta respeita o teto de US$ {budget_cap:.2f} de IA por produção")
    if economy:
        assumptions.append("modo econômico: Bíblia e planejamento pela Batch API (50% nessas chamadas)")
    return {
        "duration_seconds": round(duration, 1),
        "words": words,
        "scenes": scenes,
        "seconds": {k: round(v, 1) for k, v in goal.items()},
        "ai_media": config.ai_media,
        "cost": {
            "tts": round(tts_cost, 2), "llm": round(llm_cost, 2), "ai": round(img_cost, 2),
            "music": music_cost, "total": round(tts_cost + llm_cost + img_cost + music_cost, 2),
            "vision_high": round(vision_paid, 2),
            "range": {"low": total_low, "high": total_high},
            "by_task": by_task,
            "assumptions": assumptions,
            "unknown_prices": sorted(set(unknown_prices)),
            "currency": "USD",
            "prices_as_of": PRICES_AS_OF,
            "vision_sheets": [sheets_low, sheets_high],
        },
        "quotas": {
            "darkvi_images_needed": ai_images,
            "darkvi_remaining": darkvi.get("remaining"),
            "darkvi_limit": darkvi.get("limit"),
            "darkvi_updated_at": darkvi.get("updated_at"),
            "darkvi_enough": darkvi.get("remaining") is None or ai_images <= int(darkvi["remaining"]),
            "youtube_scenes": yt_scenes,
            "youtube_scenes_fit": yt_fit,
            # na unidade do bucket de busca (chamadas no regime por bucket; unidades no legado)
            "youtube_units_needed": yt_scenes * yt_quota.search_call_units(),
            "youtube_available": yt["available"],
            "youtube_daily_quota": yt["daily_quota"],
            "youtube_quota_unit": yt["unit"],
            "youtube_quota_mode": yt["mode"],
        },
        "time_minutes": round(minutes),
    }
