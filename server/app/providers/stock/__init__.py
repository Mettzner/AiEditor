"""Registro dos bancos de vídeo. Storyblocks e Shutterstock: adapters previstos, ainda sem implementação."""
from __future__ import annotations

from ...config import load_settings
from .base import Candidate, Rendition, StockProvider
from .pexels import Pexels
from .pixabay import Pixabay

REGISTRY: dict[str, StockProvider] = {"pexels": Pexels(), "pixabay": Pixabay()}


def enabled_providers() -> list[StockProvider]:
    cfg = sorted(load_settings()["stock_providers"], key=lambda p: p.get("priority", 99))
    return [REGISTRY[p["id"]] for p in cfg if p.get("enabled") and p["id"] in REGISTRY]


def priority_of(provider_id: str) -> int:
    for p in load_settings()["stock_providers"]:
        if p["id"] == provider_id:
            return int(p.get("priority", 99))
    return 99


__all__ = ["Candidate", "Rendition", "StockProvider", "REGISTRY", "enabled_providers", "priority_of"]
