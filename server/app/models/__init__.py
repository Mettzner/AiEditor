from sqlmodel import select

from .preset import MusicConfig, Preset, ProductionConfig, SubtitleStyle
from .tables import Channel, Issue, Production, ProductionStep, ProviderPrice, UsedAsset, now

# Valores de partida, editáveis na Configuração. Confira os preços atuais de cada provedor.
DEFAULT_PRICES = [
    ("anthropic", "per_1m_input_tokens", 4.00, "claude-opus-5-5"),
    ("anthropic", "per_1m_output_tokens", 20.00, "claude-opus-5-5"),
    ("darkvi", "per_tts_minute", 0.0, "incluso no plano"),
    ("darkvi", "per_image", 0.0, "incluso no plano"),
    ("fal", "per_second_video", 0.05, "Veo 3.1 Lite sem áudio (conferir)"),
    ("elevenlabs", "per_music_minute", 0.75, "Music API"),
    ("gemini", "per_video_analysis", 0.002, "Fase 2"),
]


def seed_defaults() -> None:
    from ..db import session_scope

    with session_scope() as s:
        if not s.exec(select(ProviderPrice)).first():
            for provider, unit, price, note in DEFAULT_PRICES:
                s.add(ProviderPrice(provider=provider, unit=unit, price=price, note=note))
            s.commit()


__all__ = [
    "Channel",
    "Issue",
    "MusicConfig",
    "Preset",
    "Production",
    "ProductionConfig",
    "ProductionStep",
    "ProviderPrice",
    "SubtitleStyle",
    "UsedAsset",
    "now",
    "seed_defaults",
]
