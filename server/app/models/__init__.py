from sqlmodel import select

from .preset import CREATION_FIELDS, MusicConfig, Preset, ProductionConfig, SubtitleStyle
from .tables import (AssetDescription, CacheLease, Channel, Issue, LlmCache, Production, ProductionStep, ProviderPrice, QuotaUsage,
                     SearchCache, UsedAsset, VisionCache, YtQuota, now)

# Valores de partida, editáveis na Configuração. Confira os preços atuais de cada provedor.
# Claude: preço por milhão de tokens, por modelo: entrada, saída, gravação de cache (5 min) e leitura de cache.
CLAUDE_PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 5.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.20),
    "claude-haiku-4-5": (1.00, 5.00, 1.25, 0.10),
}
DEFAULT_PRICES = [
    *[(f"claude:{m}", unit, price, "por 1M tokens")
      for m, prices in CLAUDE_PRICES.items()
      for unit, price in zip(("input", "output", "cache_write", "cache_read"), prices)],
    ("darkvi", "per_tts_minute", 0.0, "incluso no plano"),
    ("darkvi", "per_image", 0.0, "incluso no plano"),
    ("fal", "per_second_video", 0.05, "Veo 3.1 Lite sem áudio (conferir)"),
    ("elevenlabs", "per_music_minute", 0.75, "Music API"),
    ("gemini", "per_1m_input_tokens", 0.30, "Flash (folha de miniaturas)"),
    ("gemini", "per_1m_output_tokens", 2.50, "Flash (folha de miniaturas)"),
]


def seed_defaults() -> None:
    from ..db import session_scope

    with session_scope() as s:
        existing = {(p.provider, p.unit) for p in s.exec(select(ProviderPrice))}
        for provider, unit, price, note in DEFAULT_PRICES:
            if (provider, unit) not in existing:
                s.add(ProviderPrice(provider=provider, unit=unit, price=price, note=note))
        s.commit()


__all__ = [
    "AssetDescription",
    "CacheLease",
    "QuotaUsage",
    "CREATION_FIELDS",
    "Channel",
    "Issue",
    "LlmCache",
    "MusicConfig",
    "Preset",
    "Production",
    "ProductionConfig",
    "ProductionStep",
    "ProviderPrice",
    "SearchCache",
    "VisionCache",
    "YtQuota",
    "SubtitleStyle",
    "UsedAsset",
    "now",
    "seed_defaults",
]
