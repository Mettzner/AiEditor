"""Preset de canal (§5) e config congelada da produção."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AiMedia = Literal["both", "video", "image"]
MediaStyle = Literal["real_only", "real_preferred", "free"]
SelectionMode = Literal["fast", "precise"]
PeriodLook = Literal["cinematic", "archival"]


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
    tts_voice: str | None = None  # idApi da Darkvi (enviado no POST /tts)
    tts_voice_name: str | None = None  # nome exibido na tela
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
    # Estilo de mídia aceito: só real · real preferido (estilizado quando a cena pede) · livre
    media_style: MediaStyle = "real_preferred"
    # Modo de seleção de cenas: rápido (padrão) ou preciso
    selection_mode: SelectionMode = "fast"
    # Econômico: planejamento pela Batch API do Claude (50% mais barato, pode levar até 24 h)
    llm_economy: bool = False
    # Contexto de época (CONTEXTO_PROFUNDO_DO_ROTEIRO.md §7.1 e §8)
    period_look: PeriodLook = "cinematic"  # imagens geradas de época: reconstituição de filme ou aparência de arquivo
    period_grade: bool = True  # gradação de cor uniforme por bloco histórico
    context_cards: bool = True  # card de lugar e data na troca de bloco de contexto
    # Acabamento da edição: efeitos sonoros (whoosh, impacto, riser, teclas) e textura de filme (grão e vinheta)
    sfx: bool = True
    film_look: bool = True


# Campos escolhidos na Criação; "salvar como padrão do canal" grava só estes no preset.
CREATION_FIELDS = [
    "language", "search_language", "visual_style", "direction", "real_pct", "ai_media", "youtube_pct",
    "avg_scene_seconds", "subtitles", "tts_voice", "tts_voice_name", "media_style", "selection_mode",
    "period_look", "period_grade", "context_cards", "sfx", "film_look",
]


class ProductionConfig(Preset):
    """Preset copiado no início da produção + escolhas do wizard."""

    title: str
    channel_name: str
    audio_mode: Literal["upload", "tts"] = "tts"
    # Idioma de tudo que fala ou aparece no vídeo (narração, legendas, cards). Definido na criação a partir
    # do preset e do idioma detectado no roteiro; `search_language` vale só para as buscas.
    video_language: str | None = None

    @property
    def lang(self) -> str:
        return self.video_language or self.language
