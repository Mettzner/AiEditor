"""Preset de canal (§5) e config congelada da produção."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AiMedia = Literal["both", "video", "image"]


class SubtitleStyle(BaseModel):
    font: str = "Montserrat Bold"
    size: int = 54
    color: str = "#FFFFFF"
    outline_color: str = "#000000"
    outline: int = 3
    position: Literal["bottom", "middle", "top"] = "bottom"
    margin_v: int = 70


class MusicConfig(BaseModel):
    enabled: bool = True
    mood: str = "tenso, sombrio, lento"
    source: Literal["library", "generate", "library_then_generate"] = "library"
    volume_db: float = -22


class Preset(BaseModel):
    language: str = "en"
    search_language: str = "en"
    tts_voice: str | None = None
    reference_image: str | None = None  # caminho local da imagem de referência
    reference_key: str | None = None  # key devolvida pela Darkvi após o upload
    direction: str = "classico"
    real_pct: int = Field(70, ge=0, le=100)
    ai_media: AiMedia = "both"
    youtube_pct: int = Field(40, ge=0, le=100)
    avg_scene_seconds: float = Field(6.0, ge=2, le=20)
    subtitles: bool = True
    subtitle_style: SubtitleStyle = SubtitleStyle()
    music: MusicConfig = MusicConfig()
    font: str = "Montserrat Bold"
    color_primary: str = "#FFFFFF"
    color_accent: str = "#E63946"
    drive_folder: str = "/Canais"
    drive_subfolder_per_production: bool = True
    visual_style: str = ""


class ProductionConfig(Preset):
    """Preset copiado no início da produção + escolhas do wizard."""

    title: str
    channel_name: str
    audio_mode: Literal["upload", "tts"] = "tts"
